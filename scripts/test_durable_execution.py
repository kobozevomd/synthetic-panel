from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import durable_execution as durable
import generate


@dataclass
class Task:
    rid: str
    system_prompt: str = "system prompt"
    image_path: str | None = None


class Result:
    def __init__(self, text="ok", request_id="req", input_tokens=100, output_tokens=20):
        self.text = text
        self.model = "claude-sonnet-5"
        self.request_id = request_id
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0


class FakeProvider:
    def __init__(self, effects=None):
        self.effects = list(effects or [])
        self.calls = 0

    def generate(self, *args, **kwargs):
        self.calls += 1
        effect = self.effects.pop(0) if self.effects else Result(request_id=f"req-{self.calls}")
        if isinstance(effect, BaseException):
            raise effect
        return effect


class HttpFailure(RuntimeError):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.status_code = code


def prompt_builder(task):
    return f"user prompt {task.rid}"


def row_builder(task, result, actual):
    return {"rid": task.rid, "text": result.text, "actual_cost_usd": actual}


class DurableTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.tasks = [Task("r1"), Task("r2")]
        self.pricing = durable.pricing_for("claude-sonnet-5")

    def tearDown(self):
        self.tmp.cleanup()

    def manifest(self, tasks=None, run_cap=10.0, daily_cap=10.0):
        tasks = tasks or self.tasks
        return durable.build_input_manifest(
            tasks, prompt_builder, "anthropic", "claude-sonnet-5", 300, None,
            self.pricing, run_cap, daily_cap, {"study": "x"}, {"config": "x"},
        )

    def executor(self, provider, manifest=None, run_cap=10.0, daily_cap=10.0, max_attempts=3):
        manifest = manifest or self.manifest(run_cap=run_cap, daily_cap=daily_cap)
        return durable.DurableExecutor(
            run_dir=self.run_dir,
            run_id="run-1",
            input_manifest=manifest,
            provider=provider,
            pricing=self.pricing,
            run_cap_usd=run_cap,
            daily_budget=durable.DailyBudget(self.root / "daily.json", daily_cap, day="2026-08-18"),
            max_attempts=max_attempts,
            backoff_seconds=0,
            prompt_builder=prompt_builder,
            row_builder=row_builder,
            temperature=0.85,
        )

    def test_completed_resume_skips_paid_calls_and_has_no_duplicate_rows(self):
        first = FakeProvider()
        status, path, summary = self.executor(first).execute(self.tasks)
        self.assertEqual(status, "completed")
        self.assertEqual(first.calls, 2)
        second = FakeProvider()
        status, path, summary = self.executor(second).execute(self.tasks)
        self.assertEqual(status, "completed")
        self.assertEqual(second.calls, 0)
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual([r["rid"] for r in rows], ["r1", "r2"])

    def test_crash_left_in_flight_is_quarantined_without_retry(self):
        manifest = self.manifest()
        spec = manifest["tasks"][0]
        daily = durable.DailyBudget(self.root / "daily.json", 10, day="2026-08-18")
        daily.reserve("run-1:r1", 0.1)
        record = {
            "rid": "r1", "status": "in_flight", "attempt": 1,
            "reserved_cost_usd": 0.1, "daily_key": "run-1:r1", "request": spec,
        }
        ledger = self.run_dir / durable.CALL_LEDGER_DIR / durable._safe_rid("r1")
        durable.atomic_write_json(ledger, record)
        provider = FakeProvider()
        status, _, summary = self.executor(provider, manifest=manifest).execute(self.tasks)
        self.assertEqual(status, "quarantined")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(summary["quarantined"], 1)

    def test_ambiguous_network_error_is_quarantined_not_retried(self):
        class APIConnectionError(RuntimeError):
            pass
        provider = FakeProvider([APIConnectionError("connection lost")])
        status, _, summary = self.executor(provider).execute(self.tasks)
        self.assertEqual(status, "quarantined")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(summary["retries"], 0)

    def test_transient_http_retries_then_completes(self):
        provider = FakeProvider([HttpFailure(429), Result(request_id="req-ok")])
        status, _, summary = self.executor(provider).execute(self.tasks)
        self.assertEqual(status, "completed")
        self.assertEqual(provider.calls, 3)  # r1 twice, r2 once
        self.assertEqual(summary["retries"], 1)

    def test_permanent_4xx_is_not_retried(self):
        provider = FakeProvider([HttpFailure(400)])
        status, _, summary = self.executor(provider).execute(self.tasks)
        self.assertEqual(status, "permanent_failed")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(summary["permanent_failed"], 1)

    def test_run_money_cap_blocks_before_provider_call(self):
        manifest = self.manifest(run_cap=0.000001)
        provider = FakeProvider()
        executor = self.executor(provider, manifest=manifest, run_cap=0.000001)
        with self.assertRaises(durable.BudgetExceeded):
            executor.execute(self.tasks)
        self.assertEqual(provider.calls, 0)

    def test_daily_money_cap_blocks_before_provider_call(self):
        manifest = self.manifest(daily_cap=0.000001)
        provider = FakeProvider()
        executor = self.executor(provider, manifest=manifest, daily_cap=0.000001)
        with self.assertRaises(durable.BudgetExceeded):
            executor.execute(self.tasks)
        self.assertEqual(provider.calls, 0)

    def test_usage_and_cost_are_aggregated(self):
        provider = FakeProvider([Result(input_tokens=1_000_000, output_tokens=100_000), Result(input_tokens=0, output_tokens=0)])
        status, _, summary = self.executor(provider).execute(self.tasks)
        self.assertEqual(status, "completed")
        self.assertEqual(summary["usage"]["input_tokens"], 1_000_000)
        self.assertEqual(summary["usage"]["output_tokens"], 100_000)
        self.assertEqual(summary["actual_cost_usd"], 3.0)  # intro $2 input + $10/MTok output


class ManifestAndProviderTests(unittest.TestCase):
    def test_confirmation_is_bound_to_exact_input_sha(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            self.assertFalse(durable.is_confirmed(run, "abc", None))
            self.assertTrue(durable.is_confirmed(run, "abc", "abc"))
            self.assertFalse(durable.is_confirmed(run, "def", None))

    def test_immutable_manifest_rejects_changed_input(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / durable.INPUT_MANIFEST_NAME
            one = {"input_sha256": "one", "created_at": "a"}
            two = {"input_sha256": "two", "created_at": "b"}
            durable.ensure_input_manifest(path, one)
            with self.assertRaises(RuntimeError):
                durable.ensure_input_manifest(path, two)

    def test_sonnet5_omits_temperature_and_reads_usage(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            provider = generate.AnthropicProvider(model="claude-sonnet-5")
        usage = mock.Mock(input_tokens=11, output_tokens=7, cache_creation_input_tokens=3, cache_read_input_tokens=5)
        response = mock.Mock(
            content=[mock.Mock(type="text", text="ответ")],
            usage=usage,
            id="msg-1",
            _request_id="req-1",
        )
        provider._client = mock.MagicMock()
        provider._client.messages.create.return_value = response
        result = provider.generate("system", "user", 0.85)
        kwargs = provider._client.messages.create.call_args.kwargs
        self.assertNotIn("temperature", kwargs)
        self.assertEqual(result.input_tokens, 11)
        self.assertEqual(result.cache_creation_input_tokens, 3)

    def test_sonnet46_keeps_temperature(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            provider = generate.AnthropicProvider(model="claude-sonnet-4-6")
        provider._client = mock.MagicMock()
        provider._client.messages.create.return_value = mock.Mock(
            content=[mock.Mock(type="text", text="ok")], usage=None, id="msg"
        )
        provider.generate("system", "user", 0.85)
        self.assertEqual(provider._client.messages.create.call_args.kwargs["temperature"], 0.85)


if __name__ == "__main__":
    unittest.main(verbosity=2)

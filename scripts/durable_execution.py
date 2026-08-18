"""Durable, budgeted execution for paid synthetic-panel generations.

The call ledger is the source of truth.  Each response is persisted atomically
before the aggregate responses.jsonl is advanced.  A process that dies with a
call marked ``in_flight`` leaves an ambiguous billable request; resume moves it
to ``quarantined`` and never retries it automatically.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


INPUT_MANIFEST_NAME = "generation_input_manifest.json"
CALL_LEDGER_DIR = "call_ledger"
CONFIRMATION_NAME = "generation_confirmation.json"
CANCEL_MARKER = "cancel.requested"
MONEY = 10**6


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


@dataclass(frozen=True)
class Pricing:
    model: str
    input_per_mtok: float
    output_per_mtok: float
    cache_write_5m_per_mtok: float
    cache_read_per_mtok: float
    effective_from: str
    effective_through: Optional[str]
    source_url: str
    verified_at: str


SONNET_5_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
PROMPT_CACHE_SOURCE = "https://platform.claude.com/docs/en/build-with-claude/prompt-caching"


def pricing_for(model: str, on_date: Optional[date] = None) -> Pricing:
    """Return the official direct-API tariff applicable on ``on_date``."""
    on_date = on_date or datetime.now(timezone.utc).date()
    if model == "claude-sonnet-5":
        return Pricing(model, 2.0, 10.0, 2.5, 0.2, "2026-06-30", None, SONNET_5_SOURCE, "2026-08-19")
    if model == "claude-sonnet-4-6":
        return Pricing(model, 3.0, 15.0, 3.75, 0.3, "2026-01-01", None, PROMPT_CACHE_SOURCE, "2026-08-18")
    raise ValueError(f"No audited pricing contract for model {model!r}")


def usage_cost_usd(usage: dict[str, int], pricing: Pricing) -> float:
    value = (
        usage.get("input_tokens", 0) * pricing.input_per_mtok
        + usage.get("output_tokens", 0) * pricing.output_per_mtok
        + usage.get("cache_creation_input_tokens", 0) * pricing.cache_write_5m_per_mtok
        + usage.get("cache_read_input_tokens", 0) * pricing.cache_read_per_mtok
    ) / MONEY
    return round(value, 10)


def _safe_rid(rid: str) -> str:
    prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", rid)[:80]
    return f"{prefix}-{hashlib.sha256(rid.encode()).hexdigest()[:16]}.json"


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _error_summary(exc: BaseException) -> dict[str, Any]:
    text = str(exc)
    # Never persist request bodies, prompts, headers, or accidentally echoed keys.
    text = re.sub(r"sk-ant-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)(api[-_ ]?key\s*[=:]\s*)\S+", r"\1[REDACTED]", text)
    return {"type": type(exc).__name__, "message": text[:500]}


def _status_code(exc: BaseException) -> Optional[int]:
    direct = getattr(exc, "status_code", None)
    if isinstance(direct, int):
        return direct
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) else None


def classify_failure(exc: BaseException) -> str:
    """Return permanent, transient, or ambiguous (fail-closed)."""
    code = _status_code(exc)
    if code is not None:
        if code in {408, 409, 425, 429} or code >= 500:
            return "transient"
        if 400 <= code < 500:
            return "permanent"
    name = type(exc).__name__.lower()
    if any(token in name for token in ("ratelimit", "overloaded", "internalserver", "serviceunavailable")):
        return "transient"
    if any(token in name for token in ("timeout", "connection", "network", "transport")):
        return "ambiguous"
    return "ambiguous"


@contextmanager
def _locked_json(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        value = _load_json(path) if path.exists() else {}
        yield value
        atomic_write_json(path, value)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


class BudgetExceeded(RuntimeError):
    pass


class DailyBudget:
    def __init__(self, path: Path, cap_usd: float, day: Optional[str] = None):
        self.path = path
        self.cap_usd = float(cap_usd)
        self.day = day or datetime.now(timezone.utc).date().isoformat()

    def _totals(self, data: dict) -> tuple[float, float]:
        entries = data.get("entries", {})
        actual = sum(float(v.get("actual_cost_usd", 0)) for v in entries.values())
        reserved = sum(float(v.get("reserved_cost_usd", 0)) for v in entries.values())
        return actual, reserved

    def reserve(self, key: str, amount: float) -> None:
        with _locked_json(self.path) as data:
            if data and data.get("date") != self.day:
                raise BudgetExceeded(f"daily ledger date mismatch: {data.get('date')} != {self.day}")
            data.setdefault("date", self.day)
            data.setdefault("cap_usd", self.cap_usd)
            data.setdefault("entries", {})
            actual, reserved = self._totals(data)
            existing = data["entries"].get(key, {})
            existing_reserve = float(existing.get("reserved_cost_usd", 0))
            if actual + reserved - existing_reserve + amount > self.cap_usd + 1e-12:
                raise BudgetExceeded(
                    f"daily cap ${self.cap_usd:.4f}: actual ${actual:.6f} + reserved "
                    f"${reserved - existing_reserve:.6f} + next ${amount:.6f}"
                )
            data["entries"][key] = {
                **existing,
                "reserved_cost_usd": amount,
                "status": "reserved",
                "updated_at": now_iso(),
            }

    def settle(self, key: str, actual_cost: float, status: str, keep_reserve: bool = False) -> None:
        with _locked_json(self.path) as data:
            data.setdefault("date", self.day)
            data.setdefault("cap_usd", self.cap_usd)
            entries = data.setdefault("entries", {})
            entry = entries.setdefault(key, {})
            entry["actual_cost_usd"] = float(actual_cost)
            if not keep_reserve:
                entry["reserved_cost_usd"] = 0.0
            entry["status"] = status
            entry["updated_at"] = now_iso()


def _task_request(task: Any, provider_name: str, model: str, max_tokens: int, temperature: Optional[float], user_prompt: str) -> dict:
    request_material = {
        "rid": task.rid,
        "provider": provider_name,
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system_prompt_sha256": hashlib.sha256(task.system_prompt.encode()).hexdigest(),
        "user_prompt_sha256": hashlib.sha256(user_prompt.encode()).hexdigest(),
        "image_sha256": hashlib.sha256(Path(task.image_path).read_bytes()).hexdigest() if task.image_path else None,
    }
    return {**request_material, "request_sha256": sha256_json(request_material)}


def _estimate_tokens(task: Any, user_prompt: str, max_tokens: int) -> tuple[int, int, int]:
    chars = len(task.system_prompt) + len(user_prompt)
    expected_in = max(1, math.ceil(chars / 3.0) + 32)
    # UTF-8 bytes are a conservative upper bound for BPE token count.
    reserve_in = len((task.system_prompt + user_prompt).encode("utf-8")) + 128
    expected_out = min(max_tokens, 120)
    return expected_in, reserve_in, expected_out


def build_input_manifest(
    tasks: Iterable[Any],
    prompt_builder: Callable[[Any], str],
    provider_name: str,
    model: str,
    max_tokens: int,
    temperature: Optional[float],
    pricing: Pricing,
    run_cap_usd: float,
    daily_cap_usd: float,
    study_snapshot: dict,
    config_snapshot: dict,
) -> dict:
    task_entries = []
    expected_in = expected_out = reserve_in = 0
    for task in tasks:
        user_prompt = prompt_builder(task)
        e_in, r_in, e_out = _estimate_tokens(task, user_prompt, max_tokens)
        expected_in += e_in
        expected_out += e_out
        reserve_in += r_in
        task_entries.append(_task_request(task, provider_name, model, max_tokens, temperature, user_prompt))
    estimated_cost = usage_cost_usd({"input_tokens": expected_in, "output_tokens": expected_out}, pricing)
    protective_reserve = usage_cost_usd({"input_tokens": reserve_in, "output_tokens": len(task_entries) * max_tokens}, pricing)
    immutable = {
        "version": "pan37-generation-input-v1",
        "provider": provider_name,
        "model": model,
        "temperature_in_payload": temperature,
        "max_tokens": max_tokens,
        "study_snapshot_sha256": sha256_json(study_snapshot),
        "config_snapshot_sha256": sha256_json(config_snapshot),
        "call_count": len(task_entries),
        "tasks": task_entries,
        "pricing": asdict(pricing),
        "estimate": {
            "input_tokens": expected_in,
            "output_tokens": expected_out,
            "estimated_cost_usd": estimated_cost,
            "protective_reserve_usd": protective_reserve,
            "method": "prompt chars/3 + 32 and 120 expected output; reserve uses UTF-8 bytes + max_tokens",
        },
        "caps": {"run_cap_usd": run_cap_usd, "daily_cap_usd": daily_cap_usd},
        "local_seed_scope": "persona jitter and bootstrap only; not API text determinism",
    }
    return {**immutable, "input_sha256": sha256_json(immutable), "created_at": now_iso()}


def ensure_input_manifest(path: Path, proposed: dict) -> dict:
    if path.exists():
        existing = _load_json(path)
        comparable_existing = {k: v for k, v in existing.items() if k != "created_at"}
        comparable_proposed = {k: v for k, v in proposed.items() if k != "created_at"}
        if comparable_existing != comparable_proposed:
            raise RuntimeError("immutable generation input manifest differs on resume")
        return existing
    atomic_write_json(path, proposed)
    return proposed


def write_confirmation(run_dir: Path, input_sha256: str, confirmed_by: str) -> Path:
    path = run_dir / CONFIRMATION_NAME
    value = {"input_sha256": input_sha256, "confirmed_by": confirmed_by, "confirmed_at": now_iso()}
    if path.exists() and _load_json(path).get("input_sha256") != input_sha256:
        raise RuntimeError("confirmation exists for a different immutable input")
    atomic_write_json(path, value)
    return path


def is_confirmed(run_dir: Path, input_sha256: str, runtime_sha: Optional[str]) -> bool:
    if runtime_sha == input_sha256:
        write_confirmation(run_dir, input_sha256, "cli")
    path = run_dir / CONFIRMATION_NAME
    return path.exists() and _load_json(path).get("input_sha256") == input_sha256


class DurableExecutor:
    def __init__(
        self,
        *,
        run_dir: Path,
        run_id: str,
        input_manifest: dict,
        provider: Any,
        pricing: Pricing,
        run_cap_usd: float,
        daily_budget: DailyBudget,
        max_attempts: int,
        backoff_seconds: float,
        prompt_builder: Callable[[Any], str],
        row_builder: Callable[[Any, Any, float], dict],
        temperature: float,
    ):
        self.run_dir = run_dir
        self.run_id = run_id
        self.input_manifest = input_manifest
        self.provider = provider
        self.pricing = pricing
        self.run_cap_usd = float(run_cap_usd)
        self.daily_budget = daily_budget
        self.max_attempts = int(max_attempts)
        self.backoff_seconds = float(backoff_seconds)
        self.prompt_builder = prompt_builder
        self.row_builder = row_builder
        self.temperature = temperature
        self.ledger_dir = run_dir / CALL_LEDGER_DIR
        self.ledger_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, rid: str) -> Path:
        return self.ledger_dir / _safe_rid(rid)

    def _records(self) -> list[dict]:
        return [_load_json(p) for p in sorted(self.ledger_dir.glob("*.json"))]

    def summary(self) -> dict:
        records = self._records()
        usage_keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        usage = {key: sum(int((r.get("usage") or {}).get(key, 0)) for r in records) for key in usage_keys}
        return {
            "call_count": self.input_manifest["call_count"],
            "completed": sum(r.get("status") == "completed" for r in records),
            "quarantined": sum(r.get("status") == "quarantined" for r in records),
            "permanent_failed": sum(r.get("status") == "permanent_failed" for r in records),
            "attempts": sum(int(r.get("attempt", 0)) for r in records),
            "retries": sum(max(0, int(r.get("attempt", 0)) - 1) for r in records),
            "usage": usage,
            "actual_cost_usd": round(sum(float(r.get("actual_cost_usd", 0)) for r in records), 10),
            "committed_reserve_usd": round(sum(float(r.get("reserved_cost_usd", 0)) for r in records if r.get("status") == "quarantined"), 10),
        }

    def _run_budget_check(self, next_reserve: float) -> None:
        summary = self.summary()
        if summary["actual_cost_usd"] + summary["committed_reserve_usd"] + next_reserve > self.run_cap_usd + 1e-12:
            raise BudgetExceeded(
                f"run cap ${self.run_cap_usd:.4f}: actual ${summary['actual_cost_usd']:.6f} + "
                f"quarantine reserve ${summary['committed_reserve_usd']:.6f} + next ${next_reserve:.6f}"
            )

    def _rebuild_responses(self, tasks: Iterable[Any]) -> Path:
        by_rid = {r["rid"]: r for r in self._records() if r.get("status") == "completed"}
        path = self.run_dir / "responses.jsonl"
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for task in tasks:
                record = by_rid.get(task.rid)
                if record:
                    fh.write(json.dumps(record["response"], ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return path

    def execute(self, tasks: list[Any]) -> tuple[str, Path, dict]:
        # Any crash-left in-flight call is ambiguous and must not be repeated.
        for path in self.ledger_dir.glob("*.json"):
            record = _load_json(path)
            if record.get("status") == "in_flight":
                record["status"] = "quarantined"
                record["quarantine_reason"] = "process resumed after ambiguous in-flight state"
                record["updated_at"] = now_iso()
                atomic_write_json(path, record)
                self.daily_budget.settle(record["daily_key"], 0.0, "quarantined", keep_reserve=True)
            elif record.get("status") == "completed":
                self.daily_budget.settle(record["daily_key"], float(record.get("actual_cost_usd", 0)), "completed")

        records_by_rid = {r["rid"]: r for r in self._records()}
        if any(r.get("status") == "quarantined" for r in records_by_rid.values()):
            return "quarantined", self._rebuild_responses(tasks), self.summary()

        task_specs = {t["rid"]: t for t in self.input_manifest["tasks"]}
        for task in tasks:
            if (self.run_dir / CANCEL_MARKER).exists():
                return "cancelled", self._rebuild_responses(tasks), self.summary()
            existing = records_by_rid.get(task.rid)
            if existing and existing.get("status") == "completed":
                continue
            if existing and existing.get("status") == "permanent_failed":
                return "permanent_failed", self._rebuild_responses(tasks), self.summary()

            user_prompt = self.prompt_builder(task)
            spec = task_specs[task.rid]
            _, reserve_in, _ = _estimate_tokens(task, user_prompt, int(spec["max_tokens"]))
            reserve = usage_cost_usd(
                {"input_tokens": reserve_in, "output_tokens": int(spec["max_tokens"])}, self.pricing
            )
            record = existing or {
                "version": "pan37-call-ledger-v1",
                "rid": task.rid,
                "request": spec,
                "attempt": 0,
                "status": "pending",
                "created_at": now_iso(),
                "daily_key": f"{self.run_id}:{task.rid}",
            }

            while int(record.get("attempt", 0)) < self.max_attempts:
                self._run_budget_check(reserve)
                self.daily_budget.reserve(record["daily_key"], reserve)
                record["attempt"] = int(record.get("attempt", 0)) + 1
                record["status"] = "in_flight"
                record["reserved_cost_usd"] = reserve
                record["started_at"] = now_iso()
                record["updated_at"] = now_iso()
                atomic_write_json(self._path(task.rid), record)
                try:
                    result = self.provider.generate(
                        task.system_prompt,
                        user_prompt,
                        self.temperature,
                        image_path=task.image_path,
                    )
                except BaseException as exc:
                    kind = classify_failure(exc)
                    record["last_error"] = _error_summary(exc)
                    record["updated_at"] = now_iso()
                    if kind == "ambiguous":
                        record["status"] = "quarantined"
                        record["quarantine_reason"] = "network outcome ambiguous; manual resolution required"
                        atomic_write_json(self._path(task.rid), record)
                        self.daily_budget.settle(record["daily_key"], 0.0, "quarantined", keep_reserve=True)
                        return "quarantined", self._rebuild_responses(tasks), self.summary()
                    self.daily_budget.settle(record["daily_key"], 0.0, kind)
                    record["reserved_cost_usd"] = 0.0
                    if kind == "permanent":
                        record["status"] = "permanent_failed"
                        atomic_write_json(self._path(task.rid), record)
                        return "permanent_failed", self._rebuild_responses(tasks), self.summary()
                    record["status"] = "retry_wait"
                    atomic_write_json(self._path(task.rid), record)
                    if int(record["attempt"]) >= self.max_attempts:
                        record["status"] = "transient_exhausted"
                        atomic_write_json(self._path(task.rid), record)
                        return "transient_exhausted", self._rebuild_responses(tasks), self.summary()
                    if self.backoff_seconds:
                        time.sleep(self.backoff_seconds * (2 ** (int(record["attempt"]) - 1)))
                    continue

                usage = {
                    "input_tokens": int(getattr(result, "input_tokens", 0) or 0),
                    "output_tokens": int(getattr(result, "output_tokens", 0) or 0),
                    "cache_creation_input_tokens": int(getattr(result, "cache_creation_input_tokens", 0) or 0),
                    "cache_read_input_tokens": int(getattr(result, "cache_read_input_tokens", 0) or 0),
                }
                actual = usage_cost_usd(usage, self.pricing)
                row = self.row_builder(task, result, actual)
                record.update(
                    status="completed",
                    reserved_cost_usd=0.0,
                    actual_cost_usd=actual,
                    usage=usage,
                    model=getattr(result, "model", None),
                    request_id=getattr(result, "request_id", None),
                    response=row,
                    completed_at=now_iso(),
                    updated_at=now_iso(),
                )
                # Result first, daily settlement second. Resume reconciles the latter.
                atomic_write_json(self._path(task.rid), record)
                self.daily_budget.settle(record["daily_key"], actual, "completed")
                records_by_rid[task.rid] = record
                self._rebuild_responses(tasks)
                break

        return "completed", self._rebuild_responses(tasks), self.summary()

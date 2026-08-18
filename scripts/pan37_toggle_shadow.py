#!/usr/bin/env python3
"""Controlled 120-call PAN-37 point-toggle shadow experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import durable_execution
import generate
import report
import run_study
import ssr_core


TOGGLE_ID = "__decoy_toggle_period__"
TOGGLE_TEXT = "Семейная ипотека 3,5% с лимитом до 30 млн рублей."
ORIGINAL_ID = "SEMEYNAYA"
EXPECTED_CALLS = 120
MAX_ESTIMATE_USD = 1.0
MAX_RUN_CAP_USD = 1.0


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_value(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def atomic_json(path: Path, value: dict) -> None:
    durable_execution.atomic_write_json(path, value)


def source_state(source_run: Path) -> tuple[dict, list[dict], dict]:
    manifest = json.loads((source_run / "manifest.json").read_text(encoding="utf-8"))
    responses = read_jsonl(source_run / "responses.jsonl")
    hashes = {
        name: sha256_file(source_run / name)
        for name in (
            "generation_input_manifest.json", "responses.jsonl", "manifest.json",
            "pmf_by_respondent.csv", "pmf_by_segment.csv", "report.md",
        )
    }
    return manifest, responses, hashes


def build_shadow_inputs(root: Path, source_manifest: dict, config: dict) -> tuple[dict, list[generate.ResponseTask], str]:
    if source_manifest["controls"]["decoy"]["decoy_of"] != ORIGINAL_ID:
        raise RuntimeError("source run decoy_of is not SEMEYNAYA")
    original_text = next(item["text"] for item in source_manifest["stimuli"] if item["id"] == ORIGINAL_ID)
    if original_text + "." != TOGGLE_TEXT:
        raise RuntimeError("fixed point-toggle text no longer matches source original")
    study = {
        "name": "offer_mechanics_msk_toggle_period_shadow_v1",
        "type": "claims_ranking",
        "question_scale": source_manifest["question_scale"],
        "stimuli": [{"id": TOGGLE_ID, "text": TOGGLE_TEXT}],
        "segments": list(source_manifest["segments"]),
        "respondents_per_segment": int(source_manifest["respondents_per_segment"]),
        "samples_per_respondent": int(source_manifest["samples_per_respondent"]),
        "controls": "off",
        "diagnostic_contract_version": "pan37-toggle-period-shadow-v1",
    }
    segments = run_study.load_segments(study["segments"], root)
    question, _ = ssr_core.load_anchor_sets(root / "references" / "anchors_ru.yaml", study["question_scale"])
    tasks = generate.build_tasks(study, segments, question, int(config["report"]["seed"]), 4)
    return study, tasks, question


def original_rows(source_manifest: dict, responses: list[dict]) -> list[dict]:
    blind_id = source_manifest["controls"]["decoy"]["blind_id"]
    rows = [row for row in responses if row["stimulus_id"] == blind_id]
    if len(rows) != EXPECTED_CALLS:
        raise RuntimeError(f"expected {EXPECTED_CALLS} original rows, got {len(rows)}")
    return rows


def validate_same_profiles(tasks: list[generate.ResponseTask], originals: list[dict]) -> list[dict]:
    source_personas: dict[tuple[str, int], str] = {}
    source_questions: set[str] = set()
    for row in originals:
        key = (row["segment"], int(row["respondent_idx"]))
        previous = source_personas.setdefault(key, row["persona"])
        if previous != row["persona"]:
            raise RuntimeError(f"source persona varies within profile {key}")
        source_questions.add(row["question"])
    for task in tasks:
        key = (task.segment, task.respondent_idx)
        if source_personas.get(key) != task.persona:
            raise RuntimeError(f"shadow persona differs from source profile {key}")
        if task.question not in source_questions:
            raise RuntimeError("shadow question differs from source run")
    return [
        {
            "profile_id": f"{segment}:{respondent_idx:03d}",
            "persona_sha256": hashlib.sha256(persona.encode("utf-8")).hexdigest(),
        }
        for (segment, respondent_idx), persona in sorted(source_personas.items())
    ]


def build_contract(root: Path, source_run: Path, run_dir: Path, config_path: Path) -> tuple[dict, list[generate.ResponseTask], dict]:
    source_manifest, responses, source_hashes = source_state(source_run)
    config = run_study.load_yaml(config_path)
    study, tasks, question = build_shadow_inputs(root, source_manifest, config)
    originals = original_rows(source_manifest, responses)
    profiles = validate_same_profiles(tasks, originals)
    if len(tasks) != EXPECTED_CALLS or len(profiles) != 30:
        raise RuntimeError(f"contract cardinality mismatch: tasks={len(tasks)}, profiles={len(profiles)}")

    llm = config["llm"]
    budget = config["budget"]
    prompt_builder = lambda task: generate.build_task_prompt(task.stimulus_text, task.question)
    proposed_manifest = durable_execution.build_input_manifest(
        tasks=tasks,
        prompt_builder=prompt_builder,
        provider_name=llm["provider"],
        model=llm["model"],
        max_tokens=int(llm["max_tokens"]),
        temperature=None,
        pricing=durable_execution.pricing_for(llm["model"]),
        run_cap_usd=float(budget["run_cap_usd"]),
        daily_cap_usd=float(budget["daily_cap_usd"]),
        study_snapshot=study,
        config_snapshot={
            "llm": llm,
            "budget": budget,
            "report_seed": config["report"]["seed"],
        },
    )
    generation_manifest = durable_execution.ensure_input_manifest(
        run_dir / durable_execution.INPUT_MANIFEST_NAME, proposed_manifest
    )
    estimate = float(generation_manifest["estimate"]["estimated_cost_usd"])
    run_cap = float(generation_manifest["caps"]["run_cap_usd"])
    if generation_manifest["call_count"] != EXPECTED_CALLS:
        raise RuntimeError("generation manifest is not exactly 120 calls")
    if generation_manifest["model"] != "claude-sonnet-5" or generation_manifest["temperature_in_payload"] is not None:
        raise RuntimeError("shadow must use claude-sonnet-5 without temperature")
    if estimate > MAX_ESTIMATE_USD or run_cap > MAX_RUN_CAP_USD:
        raise RuntimeError(f"money gate failed: estimate=${estimate:.6f}, run_cap=${run_cap:.6f}")
    immutable = {
        "version": "pan37-toggle-period-shadow-contract-v1",
        "diagnostic_only": True,
        "not_a_full_repeat": True,
        "source_run": str(source_run.resolve()),
        "source_hashes": source_hashes,
        "original_stimulus_id": ORIGINAL_ID,
        "original_text": next(item["text"] for item in source_manifest["stimuli"] if item["id"] == ORIGINAL_ID),
        "toggle_stimulus_id": TOGGLE_ID,
        "toggle_text_exact": TOGGLE_TEXT,
        "reuse_method": "reuse unchanged original SEMEYNAYA API response texts; generate only point-toggle responses",
        "profile_contract": profiles,
        "planned_calls": EXPECTED_CALLS,
        "samples_per_profile": 4,
        "model": generation_manifest["model"],
        "provider": generation_manifest["provider"],
        "temperature_in_payload": generation_manifest["temperature_in_payload"],
        "max_tokens": generation_manifest["max_tokens"],
        "generation_input_sha256": generation_manifest["input_sha256"],
        "study_snapshot_sha256": generation_manifest["study_snapshot_sha256"],
        "config_snapshot_sha256": generation_manifest["config_snapshot_sha256"],
        "estimate": generation_manifest["estimate"],
        "caps": generation_manifest["caps"],
        "pricing": generation_manifest["pricing"],
    }
    contract = {**immutable, "contract_sha256": sha256_value(immutable), "created_at": datetime.now(timezone.utc).isoformat()}
    path = run_dir / "shadow_contract.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if {k: v for k, v in existing.items() if k != "created_at"} != {k: v for k, v in contract.items() if k != "created_at"}:
            raise RuntimeError("immutable shadow contract differs on resume")
        contract = existing
    else:
        atomic_json(path, contract)
    return contract, tasks, config


def execute(root: Path, source_run: Path, run_dir: Path, config_path: Path, confirmed_sha: str) -> dict:
    contract, _, config = build_contract(root, source_run, run_dir, config_path)
    if confirmed_sha != contract["generation_input_sha256"]:
        raise RuntimeError("confirmation SHA does not match immutable generation input")
    source_manifest, _, _ = source_state(source_run)
    study, _, question = build_shadow_inputs(root, source_manifest, config)
    config.setdefault("_runtime", {})["confirm_input_sha256"] = confirmed_sha
    outcome = generate.generate_responses(
        study, config, run_study.load_segments(study["segments"], root), question, run_dir,
        "PAN-37:inline-shadow-v1",
    )
    value = {"status": outcome.status, "input_sha256": outcome.input_sha256, "execution": outcome.execution_summary}
    atomic_json(run_dir / "shadow_execution.json", value)
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return value


def prepare_mixed(source_run: Path, run_dir: Path, mixed_dir: Path) -> dict:
    source_manifest, source_responses, source_hashes = source_state(source_run)
    contract = json.loads((run_dir / "shadow_contract.json").read_text(encoding="utf-8"))
    if contract["source_hashes"] != source_hashes:
        raise RuntimeError("source run changed after shadow contract")
    execution = json.loads((run_dir / "shadow_execution.json").read_text(encoding="utf-8"))
    if execution["status"] != "completed" or execution["execution"]["completed"] != EXPECTED_CALLS:
        raise RuntimeError("shadow generation is incomplete and cannot be scored")
    toggle_path = run_dir / "responses.jsonl"
    toggles = read_jsonl(toggle_path)
    if len(toggles) != EXPECTED_CALLS or {row["stimulus_id"] for row in toggles} != {TOGGLE_ID}:
        raise RuntimeError("toggle response set is not exactly the contracted 120 rows")
    archived = run_dir / "toggle_responses.jsonl"
    if not archived.exists():
        shutil.copyfile(toggle_path, archived)
    elif sha256_file(archived) != sha256_file(toggle_path):
        raise RuntimeError("archived toggle responses differ")
    originals = original_rows(source_manifest, source_responses)
    by_key_original = {(r["segment"], r["respondent_idx"], r["sample_idx"]): r for r in originals}
    by_key_toggle = {(r["segment"], r["respondent_idx"], r["sample_idx"]): r for r in toggles}
    if set(by_key_original) != set(by_key_toggle):
        raise RuntimeError("original/toggle profile-sample keys differ")
    mixed_rows = []
    for key in sorted(by_key_original):
        original = dict(by_key_original[key])
        original["stimulus_id"] = ORIGINAL_ID
        original["stimulus_text"] = contract["original_text"]
        mixed_rows.extend((original, by_key_toggle[key]))
    mixed_dir.mkdir(parents=True, exist_ok=True)
    mixed_path = mixed_dir / "responses.jsonl"
    with mixed_path.open("w", encoding="utf-8") as fh:
        for row in mixed_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    method = {
        "version": "pan37-toggle-period-mixed-scoring-v1",
        "original_rows": EXPECTED_CALLS,
        "toggle_rows": EXPECTED_CALLS,
        "original_source_responses_sha256": source_hashes["responses.jsonl"],
        "toggle_responses_sha256": sha256_file(archived),
        "mixed_responses_sha256": sha256_file(mixed_path),
        "warning": "Mixed diagnostic comparison; unchanged accepted original responses plus new point-toggle responses. Not a full repeat.",
    }
    atomic_json(mixed_dir / "mixed_method.json", method)
    print(json.dumps(method, ensure_ascii=False, indent=2, sort_keys=True))
    return method


def summarize(source_run: Path, run_dir: Path, mixed_dir: Path, output: Path) -> dict:
    source_manifest, _, source_hashes = source_state(source_run)
    contract = json.loads((run_dir / "shadow_contract.json").read_text(encoding="utf-8"))
    if contract["source_hashes"] != source_hashes:
        raise RuntimeError("source run changed before summary")
    respondent_rows = report.read_pmf_by_respondent(mixed_dir / "pmf_by_respondent.csv")
    segment_rows = report.read_pmf_by_segment(mixed_dir / "pmf_by_segment.csv")
    seg_e = {(r["segment"], r["stimulus_id"]): r["e_value"] for r in segment_rows}
    per_segment = {}
    for segment in source_manifest["segments"]:
        matrix, respondent_ids = report.build_e_matrix(respondent_rows, segment, [ORIGINAL_ID, TOGGLE_ID])
        boot = ssr_core.joint_paired_bootstrap_means(matrix, n_iters=1000, seed=42)
        p_toggle = ssr_core.pairwise_win_probability(boot, 1, 0)
        p_original = ssr_core.pairwise_win_probability(boot, 0, 1)
        deltas = matrix[:, 1] - matrix[:, 0]
        positive = int((deltas > 1e-12).sum())
        negative = int((deltas < -1e-12).sum())
        ties = len(deltas) - positive - negative
        per_segment[segment] = {
            "original_e": seg_e[(segment, ORIGINAL_ID)],
            "toggle_e": seg_e[(segment, TOGGLE_ID)],
            "signed_gap_toggle_minus_original": seg_e[(segment, TOGGLE_ID)] - seg_e[(segment, ORIGINAL_ID)],
            "p_toggle_gt_original": p_toggle,
            "label": report.separability_label(max(p_toggle, p_original)),
            "passed": report.separability_label(max(p_toggle, p_original)) == "в пределах шума",
            "profile_signs": {"toggle_gt": positive, "toggle_lt": negative, "ties": ties},
            "profile_delta_mean": float(deltas.mean()),
            "profile_delta_median": float(__import__("numpy").median(deltas)),
            "respondent_ids": respondent_ids,
        }
    records = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((run_dir / "call_ledger").glob("*.json"))]
    execution = json.loads((run_dir / "shadow_execution.json").read_text(encoding="utf-8"))["execution"]
    result = {
        "version": "pan37-toggle-period-shadow-result-v1",
        "contract_sha256": contract["contract_sha256"],
        "generation_input_sha256": contract["generation_input_sha256"],
        "source_hashes_after": source_hashes,
        "method_warning": "Mixed diagnostic: original SEMEYNAYA responses reused unchanged; only point-toggle responses are new.",
        "per_segment": per_segment,
        "execution": execution,
        "first_started_at": min(r["started_at"] for r in records),
        "last_completed_at": max(r["completed_at"] for r in records),
        "toggle_responses_sha256": sha256_file(run_dir / "toggle_responses.jsonl"),
        "mixed_responses_sha256": sha256_file(mixed_dir / "responses.jsonl"),
        "pmf_by_respondent_sha256": sha256_file(mixed_dir / "pmf_by_respondent.csv"),
        "pmf_by_segment_sha256": sha256_file(mixed_dir / "pmf_by_segment.csv"),
    }
    atomic_json(output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preflight", "execute", "prepare-mixed", "summarize"))
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--mixed-dir", type=Path)
    parser.add_argument("--confirm-input-sha")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    if args.action in {"preflight", "execute"} and not args.config:
        parser.error("--config is required")
    if args.action == "preflight":
        contract, _, _ = build_contract(root, args.source_run, args.run_dir, args.config)
        print(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.action == "execute":
        if not args.confirm_input_sha:
            parser.error("--confirm-input-sha is required")
        execute(root, args.source_run, args.run_dir, args.config, args.confirm_input_sha)
    elif args.action == "prepare-mixed":
        if not args.mixed_dir:
            parser.error("--mixed-dir is required")
        prepare_mixed(args.source_run, args.run_dir, args.mixed_dir)
    else:
        if not args.mixed_dir or not args.output:
            parser.error("--mixed-dir and --output are required")
        summarize(args.source_run, args.run_dir, args.mixed_dir, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only PAN-37 diagnostics for an existing scored panel run.

The source run is never modified.  Results are written outside it and contain
the source hashes needed to prove that every bootstrap reseed used the same PMF.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import report
import run_study


OFFSET_MULTIPLIERS = (0, 1, 2, 3, 4)
TIE_EPSILON = 1e-12


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temporary, path)


def wilson_interval(successes: int, trials: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if trials == 0:
        return (0.0, 1.0)
    p = successes / trials
    denominator = 1.0 + z * z / trials
    centre = (p + z * z / (2.0 * trials)) / denominator
    radius = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * trials)) / trials) / denominator
    return (max(0.0, centre - radius), min(1.0, centre + radius))


def load_source(run_dir: Path) -> tuple[dict, list[dict], list[dict], dict]:
    manifest_path = run_dir / "manifest.json"
    respondent_path = run_dir / "pmf_by_respondent.csv"
    segment_path = run_dir / "pmf_by_segment.csv"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    controls = manifest.get("controls") or {}
    respondent_rows = run_study.unblind_rows(report.read_pmf_by_respondent(respondent_path), controls)
    segment_rows = run_study.unblind_rows(report.read_pmf_by_segment(segment_path), controls)
    source = {
        "run_dir": str(run_dir.resolve()),
        "manifest_sha256": sha256_file(manifest_path),
        "pmf_by_respondent_sha256": sha256_file(respondent_path),
        "pmf_by_segment_sha256": sha256_file(segment_path),
    }
    return manifest, respondent_rows, segment_rows, source


def bootstrap_diagnostic(manifest: dict, respondent_rows: list[dict], segment_rows: list[dict]) -> dict:
    report_config = manifest["config_snapshot"]["report"]
    base_seed = int(report_config["seed"])
    bootstrap_iters = int(report_config["bootstrap_iters"])
    seeds = [base_seed + multiplier * report.BOOTSTRAP_RESEED_OFFSET for multiplier in OFFSET_MULTIPLIERS]
    by_segment = {segment: [] for segment in manifest["segments"]}
    for seed in seeds:
        verdict = report.compute_controls_verdict(
            all_segment_rows=segment_rows,
            all_resp_rows=respondent_rows,
            controls_manifest=manifest["controls"],
            segments=manifest["segments"],
            bootstrap_iters=bootstrap_iters,
            seed=seed,
        )
        for detail in verdict["per_segment"]:
            by_segment[detail["segment"]].append(
                {
                    "seed": seed,
                    "p_decoy_gt_original": detail["p_decoy_gt_original"],
                    "label": detail["decoy_label"],
                    "passed": bool(detail["decoy_ok"]),
                }
            )
    return {
        "contract": {
            "base_seed": base_seed,
            "bootstrap_iters_per_seed": bootstrap_iters,
            "bootstrap_reseed_offset": report.BOOTSTRAP_RESEED_OFFSET,
            "offset_multipliers": list(OFFSET_MULTIPLIERS),
            "seeds": seeds,
        },
        "per_segment": by_segment,
        "interpretation_limit": (
            "This measures Monte Carlo jitter of the paired bootstrap on the same fixed respondent PMF. "
            "It does not measure external uncertainty or repeatability of model-generated responses."
        ),
    }


def profile_diagnostic(manifest: dict, respondent_rows: list[dict]) -> dict:
    decoy_id = manifest["controls"]["decoy"]["real_id"]
    original_id = manifest["controls"]["decoy"]["decoy_of"]
    output: dict[str, dict] = {}
    for segment in manifest["segments"]:
        values: dict[int, dict[str, float]] = {}
        for row in respondent_rows:
            if row["segment"] == segment and row["stimulus_id"] in {original_id, decoy_id}:
                values.setdefault(int(row["respondent_idx"]), {})[row["stimulus_id"]] = float(row["e_value"])
        incomplete = [idx for idx, pair in values.items() if set(pair) != {original_id, decoy_id}]
        if incomplete:
            raise ValueError(f"Incomplete original/decoy pairs for {segment}: {incomplete}")
        deltas = [pair[decoy_id] - pair[original_id] for _, pair in sorted(values.items())]
        positive = sum(delta > TIE_EPSILON for delta in deltas)
        negative = sum(delta < -TIE_EPSILON for delta in deltas)
        ties = len(deltas) - positive - negative
        non_ties = positive + negative
        interval = wilson_interval(positive, non_ties)
        ordered = sorted(deltas)
        midpoint = len(ordered) // 2
        median = (
            ordered[midpoint]
            if len(ordered) % 2
            else (ordered[midpoint - 1] + ordered[midpoint]) / 2.0
        )
        output[segment] = {
            "profiles": len(deltas),
            "decoy_gt_original": {"count": positive, "share": positive / len(deltas)},
            "decoy_lt_original": {"count": negative, "share": negative / len(deltas)},
            "ties": {"count": ties, "share": ties / len(deltas)},
            "paired_delta_e_mean": sum(deltas) / len(deltas),
            "paired_delta_e_median": median,
            "sign_test": {
                "method": "two-sided 95% Wilson interval for P(delta>0), excluding ties",
                "successes": positive,
                "trials_excluding_ties": non_ties,
                "interval": list(interval),
            },
            "profile_deltas": [
                {"respondent_idx": idx, "delta_e": values[idx][decoy_id] - values[idx][original_id]}
                for idx in sorted(values)
            ],
        }
    return {"tie_epsilon": TIE_EPSILON, "per_segment": output}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stage", choices=("bootstrap", "profiles", "all"), default="all")
    args = parser.parse_args()
    manifest, respondent_rows, segment_rows, source = load_source(args.run_dir)
    result = {
        "version": "pan37-existing-diagnostics-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
    }
    if args.stage in {"bootstrap", "all"}:
        result["bootstrap"] = bootstrap_diagnostic(manifest, respondent_rows, segment_rows)
    if args.stage in {"profiles", "all"}:
        result["profiles"] = profile_diagnostic(manifest, respondent_rows)
    atomic_write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

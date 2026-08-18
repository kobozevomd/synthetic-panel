#!/usr/bin/env python3
"""Independent CSV-only v3 calculation; deliberately imports no panel scoring code."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np


MARGIN = 0.20
SD_LIMIT = 0.40
TOL = 1.0e-12
ITERATIONS = 2000
SEGMENTS = ["nam_stalo_tesno", "kvartira_rebenku", "berut_na_kotlovane"]
ORIGINAL = "SEMEYNAYA"


def read_rows(path: Path, mapping: dict[str, str] | None = None) -> list[dict]:
    output = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            output.append(
                {
                    "segment": row["segment"],
                    "stimulus_id": (mapping or {}).get(row["stimulus_id"], row["stimulus_id"]),
                    "respondent_idx": int(row["respondent_idx"]),
                    "e": float(row["E"]),
                }
            )
    return output


def paired_deltas(rows: list[dict], decoy_id: str) -> dict[str, np.ndarray]:
    output = {}
    for segment in SEGMENTS:
        profiles: dict[int, dict[str, float]] = {}
        for row in rows:
            if row["segment"] == segment and row["stimulus_id"] in {ORIGINAL, decoy_id}:
                profiles.setdefault(row["respondent_idx"], {})[row["stimulus_id"]] = row["e"]
        complete = [idx for idx in sorted(profiles) if {ORIGINAL, decoy_id} <= profiles[idx].keys()]
        output[segment] = np.asarray(
            [profiles[idx][decoy_id] - profiles[idx][ORIGINAL] for idx in complete], dtype=float
        )
    return output


def calculate(deltas: dict[str, np.ndarray]) -> dict:
    k = len(SEGMENTS)
    alpha_segment = 0.05 / k
    rng = np.random.default_rng(42)
    details = []
    boot_segments = []
    sizes = []
    for segment in SEGMENTS:
        values = deltas[segment]
        indices = rng.integers(0, len(values), size=(ITERATIONS, len(values)))
        boot = values[indices].mean(axis=1)
        low, high = np.quantile(boot, [alpha_segment / 2, 1 - alpha_segment / 2])
        mean = float(values.mean())
        sd = float(values.std(ddof=1))
        rejects = bool(low > 0 or high < 0)
        guard = bool(abs(mean) - MARGIN > TOL and rejects)
        details.append(
            {
                "segment": segment, "n_decoy_pairs": int(len(values)), "mean_delta": mean,
                "sample_sd": sd, "guard_ci_low": float(low), "guard_ci_high": float(high),
                "guard_rejects_zero": rejects, "guard_fail": guard,
                "sd_out_of_range": bool(sd - SD_LIMIT > TOL),
            }
        )
        boot_segments.append(boot)
        sizes.append(len(values))
    weights = np.asarray(sizes, dtype=float) / sum(sizes)
    pooled_boot = sum(weight * boot for weight, boot in zip(weights, boot_segments))
    pooled_low, pooled_high = np.quantile(pooled_boot, [0.05, 0.95])
    pooled_gap = float(sum(weight * row["mean_delta"] for weight, row in zip(weights, details)))
    if pooled_low >= MARGIN or pooled_high <= -MARGIN:
        primary = "FAIL"
    elif pooled_low > -MARGIN and pooled_high < MARGIN:
        primary = "PASS"
    else:
        primary = "INCONCLUSIVE"
    guards = [row["segment"] for row in details if row["guard_fail"]]
    high_sd = [row["segment"] for row in details if row["sd_out_of_range"]]
    status = "FAIL" if guards else "INCONCLUSIVE" if high_sd else primary
    return {
        "status": status,
        "primary_status": primary,
        "pooled_gap": pooled_gap,
        "pooled_ci_low": float(pooled_low),
        "pooled_ci_high": float(pooled_high),
        "guard_fail_segments": guards,
        "sd_out_of_range_segments": high_sd,
        "power_met": all(row["n_decoy_pairs"] >= 24 and not row["sd_out_of_range"] for row in details),
        "per_segment": details,
    }


def standard(run_dir: Path) -> dict:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    mapping = (manifest.get("controls") or {}).get("blind_to_real")
    return calculate(paired_deltas(read_rows(run_dir / "pmf_by_respondent.csv", mapping), "__decoy__"))


def point(point_run_dir: Path, api_quotes_run_dir: Path) -> dict:
    """Pair the point decoy with the frozen original from the accepted API run.

    The shadow run is intentionally mixed: only its point-toggle decoy is new.
    Reading its copied original would silently change the preregistered comparison
    if that copy was rounded differently, so reconstruct the pair from the two
    authoritative inputs just like the primary diagnostic path does.
    """
    api_manifest = json.loads(
        (api_quotes_run_dir / "manifest.json").read_text(encoding="utf-8")
    )
    mapping = (api_manifest.get("controls") or {}).get("blind_to_real")
    original_rows = [
        row
        for row in read_rows(api_quotes_run_dir / "pmf_by_respondent.csv", mapping)
        if row["stimulus_id"] == ORIGINAL
    ]
    point_decoy_rows = [
        row
        for row in read_rows(point_run_dir / "pmf_by_respondent.csv")
        if row["stimulus_id"] == "__decoy_toggle_period__"
    ]
    return calculate(
        paired_deltas(original_rows + point_decoy_rows, "__decoy_toggle_period__")
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-run", required=True, type=Path)
    parser.add_argument("--api-quotes-run", required=True, type=Path)
    parser.add_argument("--api-point-run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = {
        "version": "pan37-controls-gate-v3-independent-csv-v1",
        "agent_quotes": standard(args.agent_run),
        "api_quotes": standard(args.api_quotes_run),
        "api_point": point(args.api_point_run, args.api_quotes_run),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

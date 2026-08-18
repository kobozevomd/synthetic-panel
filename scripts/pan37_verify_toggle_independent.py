#!/usr/bin/env python3
"""Independent second pass for PAN-37 toggle-period metrics and cost."""

from __future__ import annotations

import argparse
import csv
import glob
import json
from pathlib import Path

import numpy as np


def label(probability_either_direction: float) -> str:
    if probability_either_direction >= 0.9:
        return "уверенный разрыв"
    if probability_either_direction >= 0.7:
        return "на грани"
    return "в пределах шума"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--mixed-dir", required=True, type=Path)
    args = parser.parse_args()

    values: dict[tuple[str, int], dict[str, float]] = {}
    with (args.mixed_dir / "pmf_by_respondent.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["stimulus_id"] not in {"SEMEYNAYA", "__decoy_toggle_period__"}:
                continue
            values.setdefault((row["segment"], int(row["respondent_idx"])), {})[row["stimulus_id"]] = float(row["E"])

    segments = sorted({key[0] for key in values})
    per_segment = {}
    for segment in segments:
        respondent_ids = sorted(idx for seg, idx in values if seg == segment)
        matrix = np.array(
            [[values[(segment, idx)]["SEMEYNAYA"], values[(segment, idx)]["__decoy_toggle_period__"]] for idx in respondent_ids],
            dtype=np.float64,
        )
        rng = np.random.default_rng(42)
        indices = rng.integers(0, len(matrix), size=(1000, len(matrix)))
        bootstrap_means = matrix[indices].mean(axis=1)
        p_toggle = float(np.mean(bootstrap_means[:, 1] > bootstrap_means[:, 0]))
        p_original = float(np.mean(bootstrap_means[:, 0] > bootstrap_means[:, 1]))
        delta = matrix[:, 1] - matrix[:, 0]
        resulting_label = label(max(p_toggle, p_original))
        per_segment[segment] = {
            "original_e": float(matrix[:, 0].mean()),
            "toggle_e": float(matrix[:, 1].mean()),
            "signed_gap_toggle_minus_original": float(delta.mean()),
            "p_toggle_gt_original": p_toggle,
            "label": resulting_label,
            "passed": resulting_label == "в пределах шума",
            "profile_signs": {
                "toggle_gt": int(np.sum(delta > 1e-12)),
                "toggle_lt": int(np.sum(delta < -1e-12)),
                "ties": int(np.sum(np.abs(delta) <= 1e-12)),
            },
            "profile_delta_median": float(np.median(delta)),
        }

    ledgers = [json.loads(Path(path).read_text(encoding="utf-8")) for path in glob.glob(str(args.run_dir / "call_ledger" / "*.json"))]
    usage = {
        key: sum(int(row["usage"][key]) for row in ledgers)
        for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    }
    recomputed_cost = round(
        (usage["input_tokens"] * 2.0 + usage["output_tokens"] * 10.0 + usage["cache_creation_input_tokens"] * 2.5 + usage["cache_read_input_tokens"] * 0.2) / 1_000_000,
        10,
    )
    result = {
        "per_segment": per_segment,
        "ledger_records": len(ledgers),
        "statuses": sorted({row["status"] for row in ledgers}),
        "attempts": sum(int(row["attempt"]) for row in ledgers),
        "usage": usage,
        "cost_recomputed_usd": recomputed_cost,
        "cost_ledger_usd": round(sum(float(row["actual_cost_usd"]) for row in ledgers), 10),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

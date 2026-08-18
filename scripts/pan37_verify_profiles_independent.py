#!/usr/bin/env python3
"""Independent stdlib-only verification of PAN-37 profile pair diagnostics."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


def wilson(successes: int, trials: int) -> list[float]:
    if trials == 0:
        return [0.0, 1.0]
    z = 1.959963984540054
    p = successes / trials
    d = 1.0 + z * z / trials
    c = (p + z * z / (2 * trials)) / d
    r = z * math.sqrt((p * (1 - p) + z * z / (4 * trials)) / trials) / d
    return [max(0.0, c - r), min(1.0, c + r)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.run_dir / "manifest.json").read_text(encoding="utf-8"))
    controls = manifest["controls"]
    blind_to_real = controls["blind_to_real"]
    original = controls["decoy"]["decoy_of"]
    decoy = controls["decoy"]["real_id"]
    pairs: dict[tuple[str, int], dict[str, float]] = {}
    with (args.run_dir / "pmf_by_respondent.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            real_id = blind_to_real.get(row["stimulus_id"], row["stimulus_id"])
            if real_id not in {original, decoy}:
                continue
            key = (row["segment"], int(row["respondent_idx"]))
            pairs.setdefault(key, {})[real_id] = float(row["E"])
    result = {}
    for segment in manifest["segments"]:
        deltas = sorted(pair[decoy] - pair[original] for (seg, _), pair in pairs.items() if seg == segment)
        pos = sum(value > 1e-12 for value in deltas)
        neg = sum(value < -1e-12 for value in deltas)
        ties = len(deltas) - pos - neg
        mid = len(deltas) // 2
        median = deltas[mid] if len(deltas) % 2 else (deltas[mid - 1] + deltas[mid]) / 2
        result[segment] = {
            "profiles": len(deltas), "positive": pos, "negative": neg, "ties": ties,
            "mean": math.fsum(deltas) / len(deltas), "median": median,
            "wilson95": wilson(pos, pos + neg),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

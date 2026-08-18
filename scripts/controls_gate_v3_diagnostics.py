#!/usr/bin/env python3
"""Read-only gate-v3 rescoring of the three frozen PAN-37 runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import report


POINT_ID = "__decoy_toggle_period__"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_hashes(run_dir: Path) -> dict:
    return {
        path.name: sha256_file(path)
        for path in sorted(run_dir.iterdir())
        if path.is_file()
    }


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temporary, path)


def unblind(rows: list[dict], controls: dict) -> list[dict]:
    mapping = controls.get("blind_to_real") or {}
    return [
        {**row, "stimulus_id": mapping.get(row["stimulus_id"], row["stimulus_id"])}
        for row in rows
    ]


def load_standard(run_dir: Path) -> tuple[dict, list[dict], list[dict]]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    controls = manifest["controls"]
    respondent = unblind(report.read_pmf_by_respondent(run_dir / "pmf_by_respondent.csv"), controls)
    segment = unblind(report.read_pmf_by_segment(run_dir / "pmf_by_segment.csv"), controls)
    return manifest, respondent, segment


def v3_controls_copy(controls: dict, k: int) -> dict:
    copied = json.loads(json.dumps(controls))
    copied.update(
        gate_version=3,
        n_decoy_pairs={"planned_per_segment": 24, "actual_by_segment": {}},
        k_predeclared_segments=k,
        alpha_method="bonferroni_arbitrary_dependence",
        alpha_segment=0.05 / k,
        sd_limit_e=0.40,
        power_met=False,
    )
    return copied


def rescore_standard(run_dir: Path) -> dict:
    manifest, respondent, segment = load_standard(run_dir)
    segments = list(manifest["segments"])
    controls = v3_controls_copy(manifest["controls"], len(segments))
    verdict = report.compute_controls_verdict(
        all_segment_rows=segment,
        all_resp_rows=respondent,
        controls_manifest=controls,
        segments=segments,
        bootstrap_iters=2000,
        seed=42,
        controls_resp_rows=respondent,
    )
    return {"source_hashes": source_hashes(run_dir), "verdict_v3": verdict}


def rescore_point(point_run: Path, api_quotes_run: Path) -> dict:
    point_manifest = json.loads((point_run / "manifest.json").read_text(encoding="utf-8"))
    point_rows = report.read_pmf_by_respondent(point_run / "pmf_by_respondent.csv")
    point_segment_rows = report.read_pmf_by_segment(point_run / "pmf_by_segment.csv")
    api_manifest, api_rows, api_segment_rows = load_standard(api_quotes_run)
    point_decoy = [
        {**row, "stimulus_id": "__decoy__"}
        for row in point_rows
        if row["stimulus_id"] == POINT_ID
    ]
    point_decoy_segment = [
        {**row, "stimulus_id": "__decoy__"}
        for row in point_segment_rows
        if row["stimulus_id"] == POINT_ID
    ]
    combined_rows = [row for row in api_rows if row["stimulus_id"] != "__decoy__"] + point_decoy
    combined_segment = [
        row for row in api_segment_rows if row["stimulus_id"] != "__decoy__"
    ] + point_decoy_segment
    segments = list(api_manifest["segments"])
    controls = v3_controls_copy(api_manifest["controls"], len(segments))
    controls["decoy"]["text"] = next(
        stimulus["text"] for stimulus in point_manifest["stimuli"] if stimulus["id"] == POINT_ID
    )
    controls["decoy"]["construction_version"] = "period-toggle-shadow-v1"
    verdict = report.compute_controls_verdict(
        all_segment_rows=combined_segment,
        all_resp_rows=combined_rows,
        controls_manifest=controls,
        segments=segments,
        bootstrap_iters=2000,
        seed=42,
        controls_resp_rows=combined_rows,
    )
    return {
        "source_hashes": {
            "point_mixed": source_hashes(point_run),
            "api_quotes_placebo_and_original": source_hashes(api_quotes_run),
        },
        "method_warning": (
            "Read-only mixed diagnostic: point-toggle decoy PMF from the shadow run; "
            "unchanged original/placebo/research rows from the accepted API quotes run. "
            "This is not a full repeated panel run."
        ),
        "verdict_v3": verdict,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-run", required=True, type=Path)
    parser.add_argument("--api-quotes-run", required=True, type=Path)
    parser.add_argument("--api-point-run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = {
        "version": "pan37-controls-gate-v3-readonly-rescore-v1",
        "api_calls": 0,
        "historical_n_decoy_pairs_per_segment": 10,
        "historical_power_met": False,
        "agent_quotes": rescore_standard(args.agent_run),
        "api_quotes": rescore_standard(args.api_quotes_run),
        "api_point": rescore_point(args.api_point_run, args.api_quotes_run),
    }
    atomic_json(args.output, output)
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

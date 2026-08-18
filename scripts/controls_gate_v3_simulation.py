#!/usr/bin/env python3
"""Preregistered PAN-37 gate-v3 calibration/validation simulation (API-free)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import time
from pathlib import Path

import numpy as np
import yaml

import controls_gate


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temporary, path)


def exact_sd_gaussian(
    *, simulations: int, n: int, shifts: np.ndarray, sd: float, seed: int
) -> np.ndarray:
    """Literal prereg generator: random normal mean, exact observed ddof=1 SD."""
    rng = np.random.default_rng(seed)
    z = rng.normal(size=(simulations, len(shifts), n))
    zbar = z.mean(axis=2, keepdims=True)
    zsd = z.std(axis=2, ddof=1, keepdims=True)
    data = shifts[None, :, None] + sd * zbar + sd * (z - zbar) / zsd
    observed = data.std(axis=2, ddof=1)
    max_error = float(np.max(np.abs(observed - sd)))
    if max_error > controls_gate.SD_BOUNDARY_TOLERANCE:
        raise AssertionError(f"exact-SD construction error {max_error} exceeds tolerance")
    return data


def bootstrap_count_matrices(
    *, k: int, n: int, iterations: int, seed: int
) -> list[np.ndarray]:
    """Production-equivalent segment-order bootstrap draws, reused within a cell."""
    rng = np.random.default_rng(seed)
    matrices = []
    for _ in range(k):
        indices = rng.integers(0, n, size=(iterations, n))
        counts = np.zeros((iterations, n), dtype=np.float64)
        rows = np.repeat(np.arange(iterations), n)
        np.add.at(counts, (rows, indices.reshape(-1)), 1.0)
        matrices.append(counts / n)
    return matrices


def vectorized_cell(
    *, data: np.ndarray, bootstrap_iterations: int, bootstrap_seed: int
) -> tuple[np.ndarray, dict]:
    simulations, k, n = data.shape
    alpha_segment = controls_gate.FAMILYWISE_ALPHA / k
    counts = bootstrap_count_matrices(
        k=k, n=n, iterations=bootstrap_iterations, seed=bootstrap_seed
    )
    boot_by_segment = [data[:, idx, :] @ counts[idx].T for idx in range(k)]
    means = data.mean(axis=2)
    sample_sds = data.std(axis=2, ddof=1)
    guard_low = np.stack(
        [np.quantile(boot, alpha_segment / 2.0, axis=1) for boot in boot_by_segment],
        axis=1,
    )
    guard_high = np.stack(
        [np.quantile(boot, 1.0 - alpha_segment / 2.0, axis=1) for boot in boot_by_segment],
        axis=1,
    )
    rejects_zero = (guard_low > 0.0) | (guard_high < 0.0)
    mean_outside = np.abs(means) - controls_gate.EQUIV_MARGIN_E > controls_gate.MEAN_BOUNDARY_TOLERANCE
    guard_fail_by_segment = mean_outside & rejects_zero
    guard_fail = guard_fail_by_segment.any(axis=1)
    sd_out = (sample_sds - controls_gate.SD_LIMIT_E > controls_gate.SD_BOUNDARY_TOLERANCE).any(axis=1)

    pooled_boot = sum(boot_by_segment) / k
    pooled_low = np.quantile(pooled_boot, 0.05, axis=1)
    pooled_high = np.quantile(pooled_boot, 0.95, axis=1)
    primary_fail = (pooled_low >= controls_gate.EQUIV_MARGIN_E) | (
        pooled_high <= -controls_gate.EQUIV_MARGIN_E
    )
    primary_pass = (pooled_low > -controls_gate.EQUIV_MARGIN_E) & (
        pooled_high < controls_gate.EQUIV_MARGIN_E
    )
    primary = np.full(simulations, "INCONCLUSIVE", dtype="U12")
    primary[primary_fail] = "FAIL"
    primary[primary_pass] = "PASS"
    statuses = primary.copy()
    statuses[sd_out] = "INCONCLUSIVE"
    statuses[guard_fail] = "FAIL"
    diagnostics = {
        "guard_fail_by_segment_count": guard_fail_by_segment.sum(axis=0).astype(int).tolist(),
        "guard_rejects_zero_by_segment_count": rejects_zero.sum(axis=0).astype(int).tolist(),
        "sd_out_of_range_count": int(sd_out.sum()),
        "primary_status_counts": {
            status: int(np.count_nonzero(primary == status))
            for status in ("PASS", "FAIL", "INCONCLUSIVE")
        },
    }
    return statuses, diagnostics


def production_spot_check(
    *, data: np.ndarray, statuses: np.ndarray, bootstrap_iterations: int, bootstrap_seed: int,
    segments: list[str], checks: int = 5,
) -> None:
    for simulation_idx in range(min(checks, len(data))):
        result = controls_gate.compute_gate_v3_from_deltas(
            deltas_by_segment={
                segment: data[simulation_idx, idx] for idx, segment in enumerate(segments)
            },
            segments=segments,
            bootstrap_iters=bootstrap_iterations,
            seed=bootstrap_seed,
        )
        if result["status"] != statuses[simulation_idx]:
            raise AssertionError(
                f"vectorized/production mismatch at simulation {simulation_idx}: "
                f"{statuses[simulation_idx]} != {result['status']}"
            )


def run_v1_regression(contract: dict) -> dict:
    spec = contract["v1_null_regression"]
    segments = contract["analysis_design"]["segment_order"]
    simulations = int(spec["simulations"])
    n = int(spec["n_profiles_per_segment"])
    rng = np.random.default_rng(int(spec["data_seed"]))
    data = np.stack(
        [
            rng.normal(0.0, float(spec["empirical_segment_sds"][segment]), size=(simulations, n))
            for segment in segments
        ],
        axis=1,
    )
    passes = np.zeros((simulations, len(segments)), dtype=bool)
    for simulation_idx in range(simulations):
        for segment_idx in range(len(segments)):
            boot_rng = np.random.default_rng(int(spec["bootstrap_seed"]) + simulation_idx)
            indices = boot_rng.integers(0, n, size=(int(spec["bootstrap_iterations"]), n))
            boot = data[simulation_idx, segment_idx][indices].mean(axis=1)
            p_positive = float(np.mean(boot > 0.0))
            p_negative = float(np.mean(boot < 0.0))
            passes[simulation_idx, segment_idx] = max(p_positive, p_negative) < float(spec["v1_threshold"])
    actual_segment = {
        segment: int(passes[:, idx].sum()) for idx, segment in enumerate(segments)
    }
    actual_whole = int(passes.all(axis=1).sum())
    expected = spec["expected_exact_counts"]
    passed = (
        actual_segment == {key: int(value) for key, value in expected["per_segment_passes"].items()}
        and actual_whole == int(expected["whole_run_strict_three_segment_passes"])
    )
    return {
        "actual_per_segment_passes": actual_segment,
        "actual_whole_run_passes": actual_whole,
        "expected_exact_counts": expected,
        "passed": passed,
    }


def phase_seed(contract: dict, phase: str, sd_idx: int, scenario_idx: int) -> tuple[int, int]:
    if phase == "calibration":
        data_seed = 310000 + 100 * sd_idx + 2 * scenario_idx
    else:
        bank = int(contract["simulation"]["validation"]["current_untouched_seed_bank_index"])
        data_seed = 910000 + 1000000 * bank + 100 * sd_idx + 2 * scenario_idx
    return data_seed, data_seed + 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--phase", required=True, choices=("calibration", "validation"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    contract = yaml.safe_load(args.contract.read_text(encoding="utf-8"))
    simulation = contract["simulation"]
    phase_spec = simulation[args.phase]
    segments = list(contract["analysis_design"]["segment_order"])
    n = int(contract["analysis_design"]["n_decoy_pairs_per_segment"])
    simulations = int(phase_spec["simulations_per_cell"])
    bootstrap_iterations = int(phase_spec["bootstrap_iterations_per_simulation"])
    required_rate = float(simulation["acceptance_rate"])
    cells = []

    for sd_idx, sd in enumerate(simulation["sd_grid"]):
        for scenario_idx, scenario in enumerate(simulation["scenario_order"]):
            data_seed, bootstrap_seed = phase_seed(contract, args.phase, sd_idx, scenario_idx)
            shifts = np.asarray(simulation["scenario_shifts"][scenario], dtype=np.float64)
            data = exact_sd_gaussian(
                simulations=simulations, n=n, shifts=shifts, sd=float(sd), seed=data_seed
            )
            statuses, diagnostics = vectorized_cell(
                data=data,
                bootstrap_iterations=bootstrap_iterations,
                bootstrap_seed=bootstrap_seed,
            )
            production_spot_check(
                data=data,
                statuses=statuses,
                bootstrap_iterations=bootstrap_iterations,
                bootstrap_seed=bootstrap_seed,
                segments=segments,
            )
            expected = "PASS" if scenario == "zero_shift" else "FAIL"
            successes = int(np.count_nonzero(statuses == expected))
            rate = successes / simulations
            cells.append(
                {
                    "sd": float(sd),
                    "scenario": scenario,
                    "shifts": shifts.tolist(),
                    "data_seed": data_seed,
                    "bootstrap_seed": bootstrap_seed,
                    "expected_status": expected,
                    "successes": successes,
                    "simulations": simulations,
                    "success_rate": rate,
                    "passed": rate >= required_rate,
                    "status_counts": {
                        status: int(np.count_nonzero(statuses == status))
                        for status in ("PASS", "FAIL", "INCONCLUSIVE")
                    },
                    **diagnostics,
                }
            )

    v1_regression = run_v1_regression(contract) if args.phase == "calibration" else None
    all_cells_passed = all(cell["passed"] for cell in cells)
    all_passed = all_cells_passed and (v1_regression is None or v1_regression["passed"])
    output = {
        "version": "pan37-controls-gate-v3-simulation-result-v1",
        "phase": args.phase,
        "contract_version": contract["version"],
        "contract_sha256": sha256_file(args.contract),
        "n_decoy_pairs_per_segment": n,
        "simulations_per_cell": simulations,
        "bootstrap_iterations_per_simulation": bootstrap_iterations,
        "alpha_method": controls_gate.ALPHA_METHOD,
        "alpha_segment": controls_gate.FAMILYWISE_ALPHA / len(segments),
        "cells": cells,
        "v1_null_regression": v1_regression,
        "all_cells_passed": all_cells_passed,
        "all_required_gates_passed": all_passed,
        "duration_seconds": time.monotonic() - started,
        "max_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
    }
    atomic_json(args.output, output)
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if all_passed else 3


if __name__ == "__main__":
    raise SystemExit(main())

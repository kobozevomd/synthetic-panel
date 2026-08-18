#!/usr/bin/env python3
"""Controls gate v3: profile-level equivalence plus multiplicity-safe guard."""

from __future__ import annotations

from typing import Optional

import numpy as np


GATE_VERSION = 3
EQUIV_MARGIN_E = 0.20
TOST_CI_LEVEL = 0.90
FAMILYWISE_ALPHA = 0.05
ALPHA_METHOD = "bonferroni_arbitrary_dependence"
SD_LIMIT_E = 0.40
SD_BOUNDARY_TOLERANCE = 1.0e-12
MEAN_BOUNDARY_TOLERANCE = 1.0e-12
PLANNED_N_DECOY_PAIRS = 24
BOOTSTRAP_ITERATIONS = 2000

INCONCLUSIVE_POWER_REASON = (
    "недостаточно профилей для сертификации эквивалентности — "
    "увеличьте respondents_per_segment"
)
INCONCLUSIVE_SD_REASON = "дисперсия пары выше проектного диапазона гейта"
INCONCLUSIVE_DATA_REASON = "неполные парные данные контроля"


def profile_deltas_from_rows(
    *,
    rows: list[dict],
    segments: list[str],
    original_id: str,
    decoy_id: str,
) -> tuple[dict[str, np.ndarray], dict[str, list[int]], Optional[str]]:
    """Build exactly one paired respondent delta per complete profile."""
    deltas: dict[str, np.ndarray] = {}
    respondent_ids: dict[str, list[int]] = {}
    for segment in segments:
        values: dict[int, dict[str, float]] = {}
        for row in rows:
            if row["segment"] != segment or row["stimulus_id"] not in {original_id, decoy_id}:
                continue
            rid = int(row["respondent_idx"])
            values.setdefault(rid, {})[row["stimulus_id"]] = float(row["e_value"])
        complete = [rid for rid in sorted(values) if {original_id, decoy_id} <= values[rid].keys()]
        if len(complete) != len(values) or len(complete) < 2:
            return deltas, respondent_ids, (
                f"segment {segment!r}: complete_pairs={len(complete)}, "
                f"profiles_seen={len(values)}"
            )
        respondent_ids[segment] = complete
        deltas[segment] = np.asarray(
            [values[rid][decoy_id] - values[rid][original_id] for rid in complete],
            dtype=np.float64,
        )
    if len(deltas) != len(segments):
        return deltas, respondent_ids, "not all predeclared segments were available"
    return deltas, respondent_ids, None


def compute_gate_v3_from_deltas(
    *,
    deltas_by_segment: dict[str, np.ndarray],
    segments: list[str],
    bootstrap_iters: int = BOOTSTRAP_ITERATIONS,
    seed: int = 42,
    margin_e: float = EQUIV_MARGIN_E,
    ci_level: float = TOST_CI_LEVEL,
    familywise_alpha: float = FAMILYWISE_ALPHA,
    sd_limit_e: float = SD_LIMIT_E,
    planned_n: int = PLANNED_N_DECOY_PAIRS,
) -> dict:
    """Compute the frozen v3 tri-state on profile-level delta vectors.

    Segment guard FAIL has priority over high-SD INCONCLUSIVE, which has
    priority over the unchanged pooled v2 TOST tri-state.
    """
    if bootstrap_iters <= 0:
        raise ValueError("bootstrap_iters must be positive")
    if not segments:
        raise ValueError("segments must not be empty")
    if not (0.0 < ci_level < 1.0):
        raise ValueError("ci_level must be between zero and one")
    if not (0.0 < familywise_alpha < 1.0):
        raise ValueError("familywise_alpha must be between zero and one")
    if margin_e <= 0.0 or sd_limit_e <= 0.0 or planned_n <= 0:
        raise ValueError("margin, SD limit, and planned n must be positive")

    k = len(segments)
    alpha_segment = familywise_alpha / k
    rng = np.random.default_rng(seed)
    per_segment: list[dict] = []
    boot_means_by_segment: list[np.ndarray] = []
    sizes: list[int] = []
    incomplete: list[str] = []

    for segment in segments:
        values = np.asarray(deltas_by_segment.get(segment, []), dtype=np.float64)
        if values.ndim != 1 or values.size < 2 or not np.all(np.isfinite(values)):
            incomplete.append(f"{segment}: n={values.size}")
            continue
        n_profiles = int(values.size)
        indices = rng.integers(0, n_profiles, size=(bootstrap_iters, n_profiles))
        boot_means = values[indices].mean(axis=1)
        guard_low, guard_high = np.quantile(
            boot_means, [alpha_segment / 2.0, 1.0 - alpha_segment / 2.0]
        )
        mean_delta = float(values.mean())
        sample_sd = float(np.std(values, ddof=1))
        rejects_zero = bool(guard_low > 0.0 or guard_high < 0.0)
        mean_outside_margin = bool(
            abs(mean_delta) - margin_e > MEAN_BOUNDARY_TOLERANCE
        )
        guard_fail = bool(mean_outside_margin and rejects_zero)
        sd_out_of_range = bool(sample_sd - sd_limit_e > SD_BOUNDARY_TOLERANCE)
        per_segment.append(
            {
                "segment": segment,
                "n_decoy_pairs": n_profiles,
                "mean_delta": mean_delta,
                "sample_sd": sample_sd,
                "guard_ci_level": 1.0 - alpha_segment,
                "guard_ci_low": float(guard_low),
                "guard_ci_high": float(guard_high),
                "guard_rejects_zero": rejects_zero,
                "guard_mean_outside_margin": mean_outside_margin,
                "guard_fail": guard_fail,
                "sd_out_of_range": sd_out_of_range,
            }
        )
        boot_means_by_segment.append(boot_means)
        sizes.append(n_profiles)

    guard_fail_segments = [row["segment"] for row in per_segment if row["guard_fail"]]
    sd_out_segments = [row["segment"] for row in per_segment if row["sd_out_of_range"]]
    actual_by_segment = {row["segment"]: row["n_decoy_pairs"] for row in per_segment}
    power_met = bool(
        not incomplete
        and len(per_segment) == k
        and all(
            row["n_decoy_pairs"] >= planned_n and not row["sd_out_of_range"]
            for row in per_segment
        )
    )

    common = {
        "gate_version": GATE_VERSION,
        "margin_e": margin_e,
        "pooled_ci_level": ci_level,
        "k_predeclared_segments": k,
        "alpha_method": ALPHA_METHOD,
        "familywise_alpha": familywise_alpha,
        "alpha_segment": alpha_segment,
        "sd_limit_e": sd_limit_e,
        "planned_n_decoy_pairs_per_segment": planned_n,
        "actual_n_decoy_pairs_by_segment": actual_by_segment,
        "power_met": power_met,
        "guard_fail_segments": guard_fail_segments,
        "sd_out_of_range_segments": sd_out_segments,
        "per_segment": per_segment,
    }

    # Explicit guard failure cannot be softened by missing data. Pooled
    # diagnostics are unavailable only when the pairing itself is incomplete.
    if guard_fail_segments and incomplete:
        return {
            **common,
            "status": "FAIL",
            "reason": "segment guard v3 FAIL: " + ", ".join(guard_fail_segments),
            "pooled_gap": None,
            "pooled_ci_low": None,
            "pooled_ci_high": None,
            "primary_status": None,
            "diagnostic_error": "; ".join(incomplete) or None,
        }

    if incomplete or len(per_segment) != k:
        return {
            **common,
            "status": "INCONCLUSIVE",
            "reason": INCONCLUSIVE_DATA_REASON,
            "pooled_gap": None,
            "pooled_ci_low": None,
            "pooled_ci_high": None,
            "primary_status": None,
            "diagnostic_error": "; ".join(incomplete),
        }

    total = float(sum(sizes))
    weights = np.asarray(sizes, dtype=np.float64) / total
    pooled_boot = np.zeros(bootstrap_iters, dtype=np.float64)
    for weight, boot_means in zip(weights, boot_means_by_segment):
        pooled_boot += weight * boot_means
    alpha = 1.0 - ci_level
    pooled_low, pooled_high = np.quantile(
        pooled_boot, [alpha / 2.0, 1.0 - alpha / 2.0]
    )
    pooled_gap = float(
        sum(weight * row["mean_delta"] for weight, row in zip(weights, per_segment))
    )
    if pooled_low >= margin_e or pooled_high <= -margin_e:
        primary_status = "FAIL"
        primary_reason = "pooled 90% CI целиком с одной стороны допуска ±0.20"
    elif pooled_low > -margin_e and pooled_high < margin_e:
        primary_status = "PASS"
        primary_reason = "pooled 90% CI строго внутри допуска ±0.20"
    else:
        primary_status = "INCONCLUSIVE"
        primary_reason = INCONCLUSIVE_POWER_REASON

    if guard_fail_segments:
        status = "FAIL"
        reason = "segment guard v3 FAIL: " + ", ".join(guard_fail_segments)
    elif sd_out_segments:
        status = "INCONCLUSIVE"
        reason = INCONCLUSIVE_SD_REASON + ": " + ", ".join(sd_out_segments)
    else:
        status = primary_status
        reason = primary_reason

    return {
        **common,
        "status": status,
        "reason": reason,
        "pooled_gap": pooled_gap,
        "pooled_ci_low": float(pooled_low),
        "pooled_ci_high": float(pooled_high),
        "primary_status": primary_status,
        "primary_reason": primary_reason,
        "diagnostic_error": None,
    }


def compute_gate_v3(
    *,
    rows: list[dict],
    segments: list[str],
    original_id: str,
    decoy_id: str,
    bootstrap_iters: int = BOOTSTRAP_ITERATIONS,
    seed: int = 42,
) -> dict:
    deltas, respondent_ids, error = profile_deltas_from_rows(
        rows=rows,
        segments=segments,
        original_id=original_id,
        decoy_id=decoy_id,
    )
    result = compute_gate_v3_from_deltas(
        deltas_by_segment=deltas,
        segments=segments,
        bootstrap_iters=bootstrap_iters,
        seed=seed,
    )
    result["respondent_ids_by_segment"] = respondent_ids
    if error and not result.get("diagnostic_error"):
        result["diagnostic_error"] = error
    return result

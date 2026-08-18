#!/usr/bin/env python3
"""Focused API-free tests for PAN-37 controls gate v3."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import controls_gate
import controls_gate_v3_verify_independent as independent_verify
import generate
import report
import run_study


SEGMENTS = ["s1", "s2", "s3"]


class TestGateV3(unittest.TestCase):
    def _compute(self, vectors, *, seed=42):
        return controls_gate.compute_gate_v3_from_deltas(
            deltas_by_segment={segment: np.asarray(values, dtype=float) for segment, values in zip(SEGMENTS, vectors)},
            segments=SEGMENTS,
            bootstrap_iters=2000,
            seed=seed,
        )

    def test_null_passes_primary_tost(self):
        base = np.linspace(-0.08, 0.08, 24)
        result = self._compute([base, base[::-1], base])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["primary_status"], "PASS")
        self.assertEqual(result["alpha_segment"], 0.05 / 3)
        self.assertTrue(result["power_met"])

    def test_global_positive_and_negative_fail(self):
        for shift in (0.35, -0.35):
            result = self._compute([np.full(24, shift)] * 3)
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(set(result["guard_fail_segments"]), set(SEGMENTS))
            self.assertIsNotNone(result["pooled_ci_low"])

    def test_single_segment_guard_prevents_pooled_cancellation(self):
        result = self._compute(
            [np.full(24, 0.35), np.full(24, -0.175), np.full(24, -0.175)]
        )
        self.assertAlmostEqual(result["pooled_gap"], 0.0, places=12)
        self.assertEqual(result["primary_status"], "PASS")
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("s1", result["guard_fail_segments"])

    def test_high_sd_is_inconclusive_when_no_guard_fails(self):
        high_sd = np.linspace(-0.75, 0.75, 24)
        result = self._compute([high_sd, high_sd[::-1], high_sd])
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertIn(controls_gate.INCONCLUSIVE_SD_REASON, result["reason"])
        self.assertFalse(result["power_met"])

    def test_guard_fail_has_priority_over_high_sd(self):
        residual = np.linspace(-0.8, 0.8, 24)
        values = 0.50 + residual
        result = self._compute([values, np.zeros(24), np.zeros(24)])
        self.assertGreater(result["per_segment"][0]["sample_sd"], 0.40)
        self.assertTrue(result["per_segment"][0]["guard_fail"])
        self.assertEqual(result["status"], "FAIL")

    def test_guard_mean_boundary_is_strict(self):
        result = self._compute([np.full(24, 0.20)] * 3)
        self.assertFalse(any(row["guard_fail"] for row in result["per_segment"]))
        self.assertEqual(result["primary_status"], "FAIL")

    def test_historical_n10_is_scored_but_power_not_met(self):
        base = np.linspace(-0.05, 0.05, 10)
        result = self._compute([base, base, base])
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["power_met"])
        self.assertEqual(result["actual_n_decoy_pairs_by_segment"], {"s1": 10, "s2": 10, "s3": 10})


class TestControlOnlyGenerationAndIsolation(unittest.TestCase):
    def _study(self):
        return {
            "name": "gate_v3_test",
            "type": "claims_ranking",
            "question_scale": "purchase_intent",
            "segments": ["s1"],
            "respondents_per_segment": 10,
            "samples_per_respondent": 4,
            "stimuli": [{"id": "A", "text": "A."}, {"id": "B", "text": "B."}],
        }

    def test_runner_adds_14_profiles_for_only_original_and_decoy(self):
        study = self._study()
        controls = run_study.build_controls_manifest(study, run_study._SCRIPTS_DIR.parent, seed=42)
        effective = run_study.build_effective_study(study, controls)
        tasks = generate.build_tasks(
            effective,
            {"s1": {"id": "s1", "name": "S1"}},
            question="Q?",
            seed=42,
            samples_per_respondent=4,
        )
        main = [task for task in tasks if not task.control_only]
        extra = [task for task in tasks if task.control_only]
        self.assertEqual(len(main), 10 * 4 * 4)  # 2 real + placebo + decoy
        self.assertEqual(len(extra), 14 * 2 * 4)
        self.assertEqual({task.respondent_idx for task in extra}, set(range(11, 25)))
        self.assertEqual(
            {task.stimulus_id for task in extra},
            {
                controls["real_to_blind"][controls["decoy"]["decoy_of"]],
                controls["decoy"]["blind_id"],
            },
        )

    def test_control_only_original_cannot_leak_into_main_aggregates(self):
        controls = {
            "gate_version": 3,
            "decoy": {"decoy_of": "A", "blind_id": "BL2"},
            "real_to_blind": {"A": "BL1", "__decoy__": "BL2"},
        }
        rows = [
            {"segment": "s1", "stimulus_id": "BL1", "respondent_idx": 1, "control_only": False},
            {"segment": "s1", "stimulus_id": "BL2", "respondent_idx": 1, "control_only": False},
            {"segment": "s1", "stimulus_id": "BL3", "respondent_idx": 1, "control_only": False},
            {"segment": "s1", "stimulus_id": "BL1", "respondent_idx": 11, "control_only": True},
            {"segment": "s1", "stimulus_id": "BL2", "respondent_idx": 11, "control_only": True},
        ]
        pmfs = np.asarray(
            [
                [0, 0, 0, 0, 1], [0, 0, 0, 1, 0], [0, 0, 1, 0, 0],
                [1, 0, 0, 0, 0], [0, 1, 0, 0, 0],
            ],
            dtype=float,
        )
        main_rows, main_pmfs, pair_rows, pair_pmfs, extra_count = run_study.partition_scored_responses(
            rows, pmfs, controls
        )
        self.assertEqual(extra_count, 2)
        self.assertEqual({row["respondent_idx"] for row in main_rows}, {1})
        self.assertEqual({row["respondent_idx"] for row in pair_rows}, {1, 11})
        main_respondents = run_study.aggregate_respondent_pmfs(main_rows, main_pmfs)
        pair_respondents = run_study.aggregate_respondent_pmfs(pair_rows, pair_pmfs)
        self.assertEqual(max(row["respondent_idx"] for row in main_respondents), 1)
        self.assertEqual(max(row["respondent_idx"] for row in pair_respondents), 11)


class TestReportServiceIntegration(unittest.TestCase):
    def test_gate_v3_controls_verdict_uses_controls_only_pair_rows(self):
        manifest = {
            "enabled": True,
            "gate_version": 3,
            "placebo": {"real_id": "__placebo__", "kind": "irrelevant"},
            "decoy": {"real_id": "__decoy__", "decoy_of": "A"},
        }
        main_rows = []
        segment_rows = []
        controls_rows = []
        delta = np.linspace(-0.05, 0.05, 24)
        for segment in SEGMENTS:
            segment_rows.extend(
                {"segment": segment, "stimulus_id": sid, "e_value": e}
                for sid, e in {"A": 4.0, "B": 3.0, "__decoy__": 4.0, "__placebo__": 1.0}.items()
            )
            for idx in range(1, 11):
                for sid, e in {"A": 4.0, "B": 3.0, "__decoy__": 4.0, "__placebo__": 1.0}.items():
                    main_rows.append(
                        {"segment": segment, "stimulus_id": sid, "respondent_idx": idx, "e_value": e}
                    )
            for idx in range(1, 25):
                controls_rows.extend(
                    [
                        {"segment": segment, "stimulus_id": "A", "respondent_idx": idx, "e_value": 4.0},
                        {
                            "segment": segment, "stimulus_id": "__decoy__", "respondent_idx": idx,
                            "e_value": 4.0 + float(delta[idx - 1]),
                        },
                    ]
                )
        verdict = report.compute_controls_verdict(
            all_segment_rows=segment_rows,
            all_resp_rows=main_rows,
            controls_manifest=manifest,
            segments=SEGMENTS,
            bootstrap_iters=100,
            seed=42,
            controls_resp_rows=controls_rows,
        )
        self.assertEqual(verdict["gate_version"], 3)
        self.assertEqual(verdict["decoy_status"], "PASS")
        self.assertTrue(verdict["placebo_passed"])
        self.assertTrue(verdict["power_met"])
        self.assertFalse(verdict["controls_failed"])
        rendered = report.render_controls_verdict_detail(verdict)
        self.assertIn("gate v3: PASS", rendered)
        self.assertIn("power_met=true", rendered)


class TestIndependentHistoricalVerifier(unittest.TestCase):
    def test_point_shadow_uses_frozen_api_original_not_shadow_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            api_quotes = root / "api_quotes"
            point_shadow = root / "point_shadow"
            api_quotes.mkdir()
            point_shadow.mkdir()
            (api_quotes / "manifest.json").write_text(
                json.dumps({"controls": {"blind_to_real": {}}}), encoding="utf-8"
            )
            fields = ["segment", "stimulus_id", "respondent_idx", "E"]
            with (api_quotes / "pmf_by_respondent.csv").open(
                "w", encoding="utf-8", newline=""
            ) as fh:
                writer = csv.DictWriter(fh, fieldnames=fields)
                writer.writeheader()
                for segment in independent_verify.SEGMENTS:
                    for idx in range(1, 25):
                        writer.writerow(
                            {
                                "segment": segment,
                                "stimulus_id": "SEMEYNAYA",
                                "respondent_idx": idx,
                                "E": 4.0,
                            }
                        )
            with (point_shadow / "pmf_by_respondent.csv").open(
                "w", encoding="utf-8", newline=""
            ) as fh:
                writer = csv.DictWriter(fh, fieldnames=fields)
                writer.writeheader()
                for segment in independent_verify.SEGMENTS:
                    for idx in range(1, 25):
                        writer.writerow(
                            {
                                "segment": segment,
                                "stimulus_id": "SEMEYNAYA",
                                "respondent_idx": idx,
                                "E": 0.0,
                            }
                        )
                        writer.writerow(
                            {
                                "segment": segment,
                                "stimulus_id": "__decoy_toggle_period__",
                                "respondent_idx": idx,
                                "E": 4.0,
                            }
                        )
            result = independent_verify.point(point_shadow, api_quotes)
            self.assertEqual(result["status"], "PASS")
            self.assertAlmostEqual(result["pooled_gap"], 0.0, places=12)


if __name__ == "__main__":
    unittest.main()

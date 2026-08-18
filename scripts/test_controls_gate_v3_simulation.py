#!/usr/bin/env python3
"""Focused tests for the frozen v3 simulation runner."""

import unittest

import numpy as np

import controls_gate_v3_simulation as simulation


class TestGateV3Simulation(unittest.TestCase):
    def test_exact_sd_generator_keeps_random_mean_and_exact_sample_sd(self):
        data = simulation.exact_sd_gaussian(
            simulations=20,
            n=24,
            shifts=np.asarray([0.0, 0.35, -0.35]),
            sd=0.40,
            seed=123,
        )
        np.testing.assert_allclose(data.std(axis=2, ddof=1), 0.40, atol=1e-12)
        self.assertGreater(float(np.std(data[:, 0, :].mean(axis=1))), 0.0)

    def test_vectorized_path_matches_production_core(self):
        segments = ["s1", "s2", "s3"]
        data = simulation.exact_sd_gaussian(
            simulations=8,
            n=24,
            shifts=np.asarray([0.35, -0.175, -0.175]),
            sd=0.28,
            seed=321,
        )
        statuses, _ = simulation.vectorized_cell(
            data=data, bootstrap_iterations=200, bootstrap_seed=555
        )
        simulation.production_spot_check(
            data=data,
            statuses=statuses,
            bootstrap_iterations=200,
            bootstrap_seed=555,
            segments=segments,
            checks=8,
        )

    def test_seed_namespaces_are_frozen_and_disjoint(self):
        contract = {
            "simulation": {"validation": {"current_untouched_seed_bank_index": 0}}
        }
        calibration = simulation.phase_seed(contract, "calibration", 4, 14)
        validation = simulation.phase_seed(contract, "validation", 4, 14)
        self.assertEqual(calibration, (310428, 310429))
        self.assertEqual(validation, (910428, 910429))
        self.assertNotEqual(calibration, validation)


if __name__ == "__main__":
    unittest.main()

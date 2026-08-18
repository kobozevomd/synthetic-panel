import unittest

import pan37_existing_diagnostics as diagnostics


class Pan37ExistingDiagnosticsTest(unittest.TestCase):
    def test_offsets_are_fixed_multiples_of_contract_constant(self):
        self.assertEqual(diagnostics.OFFSET_MULTIPLIERS, (0, 1, 2, 3, 4))

    def test_wilson_known_case(self):
        low, high = diagnostics.wilson_interval(8, 10)
        self.assertAlmostEqual(low, 0.490162, places=6)
        self.assertAlmostEqual(high, 0.943318, places=6)

    def test_wilson_empty_is_uninformative(self):
        self.assertEqual(diagnostics.wilson_interval(0, 0), (0.0, 1.0))


if __name__ == "__main__":
    unittest.main()

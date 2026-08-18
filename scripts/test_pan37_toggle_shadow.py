import unittest

import pan37_toggle_shadow as shadow


class ToggleShadowSourceSelectionTest(unittest.TestCase):
    def test_original_rows_selects_real_semeynaya_blind_id_not_decoy(self):
        manifest = {
            "controls": {
                "real_to_blind": {"SEMEYNAYA": "BL2", "__decoy__": "BL4"},
                "decoy": {"blind_id": "BL4"},
            },
            "stimuli": [{"id": "SEMEYNAYA", "text": "original"}],
        }
        responses = [
            {"stimulus_id": "BL2", "stimulus_text": "original", "rid": f"o-{i}"}
            for i in range(shadow.EXPECTED_CALLS)
        ] + [
            {"stimulus_id": "BL4", "stimulus_text": "«original»", "rid": f"d-{i}"}
            for i in range(shadow.EXPECTED_CALLS)
        ]
        selected = shadow.original_rows(manifest, responses)
        self.assertEqual(len(selected), shadow.EXPECTED_CALLS)
        self.assertEqual({row["stimulus_id"] for row in selected}, {"BL2"})

    def test_original_rows_rejects_wrong_text(self):
        manifest = {
            "controls": {"real_to_blind": {"SEMEYNAYA": "BL2"}},
            "stimuli": [{"id": "SEMEYNAYA", "text": "original"}],
        }
        responses = [
            {"stimulus_id": "BL2", "stimulus_text": "wrong"}
            for _ in range(shadow.EXPECTED_CALLS)
        ]
        with self.assertRaisesRegex(RuntimeError, "exact SEMEYNAYA"):
            shadow.original_rows(manifest, responses)


if __name__ == "__main__":
    unittest.main()

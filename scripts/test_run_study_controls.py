#!/usr/bin/env python3
"""
test_run_study_controls.py — юнит-тесты негативных контролей (§1.4, фикс Д5) и
режима/модели (§1.5, фикс Д4) в scripts/run_study.py.

Работает БЕЗ сети/embedding-модели: покрывает CLI-оркестрацию/манифест-логику
(построение controls-манифеста, слепые id, обратная совместимость, устойчивость
manifest к повторной инициализации) — не сам SSR-скоринг (тот в test_ssr.py) и не
статистику отчёта (та в test_report.py). Реальный сквозной прогон end-to-end с
embedding-моделью — ручная проверка (см. F2/DoD spec_synthetic-panel_v1.3.md), не
юнит-тест (проект не требует sentence-transformers для тестов, см. test_ssr.py
докстринг).

Покрытие:
    - controls_requested: on/off токены, обратная совместимость (поле отсутствует).
    - load_placebo_bank: реальный references/placebo_bank_ru.yaml валиден.
    - pick_placebo/pick_decoy_source: детерминированность от (seed, study_name).
    - make_decoy_text: результат ВСЕГДА отличается от оригинала.
    - build_controls_manifest: структура, id-биекция слепых меток, конфликт id,
      отключение через controls: off.
    - build_effective_study: слепые id на ВСЕХ стимулах (реальные+плацебо+ловушка);
      passthrough при отключённых контролях.
    - unblind_rows/split_real_and_control_rows: круговой обход (round-trip),
      identity-фоллбэк для прогонов без controls.
    - find_sibling_rankings: находит прогон-сосед, игнорирует себя и несовместимые
      наборы стимулов.
    - compute_run_mode: матрица provider/validated_embedding/controls_ok.
    - load_or_init_manifest: устойчивость controls-блока к повторной инициализации
      (НЕ пересчитывается, даже если seed в конфиге между вызовами изменился).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_SKILL_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import run_study  # noqa: E402


def _fake_study(name: str = "demo", controls=None) -> dict:
    study = {
        "name": name,
        "type": "claims_ranking",
        "question_scale": "purchase_intent",
        "stimuli": [{"id": "A", "text": "Клейм А про продукт."}, {"id": "B", "text": "Клейм Б про продукт."}],
        "segments": ["seg1"],
        "respondents_per_segment": 5,
        "samples_per_respondent": 2,
    }
    if controls is not None:
        study["controls"] = controls
    return study


class TestControlsRequested(unittest.TestCase):
    def test_absent_field_means_enabled(self):
        self.assertTrue(run_study.controls_requested(_fake_study()))

    def test_off_variants_disable(self):
        for token in ("off", "OFF", "false", "False", "no", "0", "disabled", "нет", "выкл"):
            self.assertFalse(run_study.controls_requested(_fake_study(controls=token)), token)

    def test_on_keeps_enabled(self):
        self.assertTrue(run_study.controls_requested(_fake_study(controls="on")))


class TestLoadPlaceboBank(unittest.TestCase):
    def test_real_bank_has_at_least_three_entries_with_id_and_text(self):
        bank = run_study.load_placebo_bank(_SKILL_ROOT)
        self.assertGreaterEqual(len(bank), 3)
        for entry in bank:
            self.assertIn("id", entry)
            self.assertIn("text", entry)
            self.assertTrue(entry["text"].strip())


class TestPickDeterminism(unittest.TestCase):
    def test_pick_placebo_deterministic_same_inputs(self):
        bank = run_study.load_placebo_bank(_SKILL_ROOT)
        p1 = run_study.pick_placebo(bank, seed=42, study_name="demo")
        p2 = run_study.pick_placebo(bank, seed=42, study_name="demo")
        self.assertEqual(p1, p2)

    def test_pick_decoy_source_deterministic_same_inputs(self):
        stimuli = _fake_study()["stimuli"]
        s1 = run_study.pick_decoy_source(stimuli, seed=1, study_name="demo")
        s2 = run_study.pick_decoy_source(stimuli, seed=1, study_name="demo")
        self.assertEqual(s1, s2)


class TestMakeDecoyText(unittest.TestCase):
    def test_result_always_differs_from_original(self):
        import random

        texts = [
            "Продукт помогает за 4 недели.",
            "Без точки на конце",
            "«Уже в кавычках»",
            "!",
            "?",
        ]
        for text in texts:
            for seed in range(5):
                rng = random.Random(seed)
                decoy = run_study.make_decoy_text(text, rng)
                self.assertNotEqual(decoy, text, f"decoy совпал с оригиналом: {text!r}")

    def test_toggles_period(self):
        import random

        rng = random.Random(0)
        # Гоняем оба детерминированных варианта (rng.choice выбирает один из двух) -
        # проверяем, что РЕЗУЛЬТАТ - один из ожидаемых типов правки.
        decoy = run_study.make_decoy_text("Без точки", rng)
        self.assertIn(decoy, ("Без точки.", "«Без точки»"))


class TestBuildControlsManifest(unittest.TestCase):
    def test_enabled_structure_and_blind_bijection(self):
        study = _fake_study()
        cm = run_study.build_controls_manifest(study, _SKILL_ROOT, seed=42)
        self.assertTrue(cm["enabled"])
        self.assertEqual(cm["placebo"]["real_id"], run_study.PLACEBO_REAL_ID)
        self.assertEqual(cm["decoy"]["real_id"], run_study.DECOY_REAL_ID)
        self.assertIn(cm["decoy"]["decoy_of"], {"A", "B"})

        expected_real_ids = {"A", "B", run_study.PLACEBO_REAL_ID, run_study.DECOY_REAL_ID}
        self.assertEqual(set(cm["blind_to_real"].values()), expected_real_ids)
        self.assertEqual(len(cm["blind_to_real"]), 4)
        # Биекция: real_to_blind - точный обратный словарь blind_to_real.
        for blind_id, real_id in cm["blind_to_real"].items():
            self.assertEqual(cm["real_to_blind"][real_id], blind_id)
        self.assertEqual(len(set(cm["blind_to_real"].keys())), 4)  # все метки различны

    def test_disabled_via_controls_off(self):
        study = _fake_study(controls="off")
        cm = run_study.build_controls_manifest(study, _SKILL_ROOT, seed=42)
        self.assertEqual(cm, {"enabled": False, "reason": "study.yaml: controls: off"})

    def test_stimulus_id_clash_with_reserved_id_raises(self):
        study = _fake_study()
        study["stimuli"].append({"id": run_study.PLACEBO_REAL_ID, "text": "случайное совпадение id"})
        with self.assertRaises(ValueError):
            run_study.build_controls_manifest(study, _SKILL_ROOT, seed=42)

    def test_deterministic_across_calls(self):
        study = _fake_study()
        cm1 = run_study.build_controls_manifest(study, _SKILL_ROOT, seed=7)
        cm2 = run_study.build_controls_manifest(study, _SKILL_ROOT, seed=7)
        self.assertEqual(cm1, cm2)


class TestBuildEffectiveStudy(unittest.TestCase):
    def test_blinds_all_stimuli_including_controls(self):
        study = _fake_study()
        cm = run_study.build_controls_manifest(study, _SKILL_ROOT, seed=42)
        effective = run_study.build_effective_study(study, cm)

        self.assertEqual(len(effective["stimuli"]), 4)  # 2 реальных + плацебо + ловушка
        blind_ids_used = {s["id"] for s in effective["stimuli"]}
        self.assertEqual(blind_ids_used, set(cm["blind_to_real"].keys()))
        # Реальные id (A/B/__placebo__/__decoy__) НЕ должны утекать в effective.stimuli id.
        for s in effective["stimuli"]:
            self.assertNotIn(s["id"], {"A", "B", run_study.PLACEBO_REAL_ID, run_study.DECOY_REAL_ID})

        texts_by_blind = {s["id"]: s["text"] for s in effective["stimuli"]}
        placebo_blind = cm["placebo"]["blind_id"]
        self.assertEqual(texts_by_blind[placebo_blind], cm["placebo"]["text"])

    def test_passthrough_when_disabled(self):
        study = _fake_study(controls="off")
        cm = {"enabled": False, "reason": "x"}
        effective = run_study.build_effective_study(study, cm)
        self.assertEqual(effective["stimuli"], study["stimuli"])


class TestUnblindAndSplit(unittest.TestCase):
    def test_roundtrip_through_blind_and_unblind(self):
        study = _fake_study()
        cm = run_study.build_controls_manifest(study, _SKILL_ROOT, seed=42)
        effective = run_study.build_effective_study(study, cm)

        # Симулируем "результат скоринга" — по одной строке на КАЖДЫЙ слепой стимул.
        rows = [{"stimulus_id": s["id"], "e_value": 3.0} for s in effective["stimuli"]]
        unblinded = run_study.unblind_rows(rows, cm)
        real_ids_seen = {r["stimulus_id"] for r in unblinded}
        self.assertEqual(real_ids_seen, {"A", "B", run_study.PLACEBO_REAL_ID, run_study.DECOY_REAL_ID})

    def test_identity_when_controls_manifest_is_none(self):
        rows = [{"stimulus_id": "BL1", "e_value": 1.0}]
        self.assertEqual(run_study.unblind_rows(rows, None), rows)

    def test_identity_when_controls_disabled(self):
        rows = [{"stimulus_id": "A", "e_value": 1.0}]
        result = run_study.unblind_rows(rows, {"enabled": False})
        self.assertEqual(result, rows)

    def test_split_real_and_control_rows(self):
        rows = [
            {"stimulus_id": "A", "e_value": 4.0},
            {"stimulus_id": "B", "e_value": 3.0},
            {"stimulus_id": run_study.PLACEBO_REAL_ID, "e_value": 1.0},
            {"stimulus_id": run_study.DECOY_REAL_ID, "e_value": 3.9},
        ]
        real_rows, control_rows = run_study.split_real_and_control_rows(rows, {"A", "B"})
        self.assertEqual({r["stimulus_id"] for r in real_rows}, {"A", "B"})
        self.assertEqual(
            {r["stimulus_id"] for r in control_rows},
            {run_study.PLACEBO_REAL_ID, run_study.DECOY_REAL_ID},
        )


class TestFindSiblingRankings(unittest.TestCase):
    def test_finds_sibling_and_skips_self_and_mismatched_sets(self):
        with tempfile.TemporaryDirectory() as td:
            runs_root = Path(td)
            current = runs_root / "demo_20260101-0000"
            sibling_ok = runs_root / "demo_20260102-0000"
            sibling_mismatched = runs_root / "demo_20260103-0000"
            for d in (current, sibling_ok, sibling_mismatched):
                d.mkdir()

            def _write_run(run_dir: Path, stimulus_ids: list[str], e_values: list[float]):
                manifest = {"controls": {"enabled": False}}
                (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                lines = ["segment,stimulus_id,n_respondents,P1,P2,P3,P4,P5,E,ci_low,ci_high"]
                for sid, e in zip(stimulus_ids, e_values):
                    lines.append(f"seg1,{sid},5,0.1,0.1,0.1,0.1,0.6,{e},{e - 0.2},{e + 0.2}")
                (run_dir / "pmf_by_segment.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

            _write_run(sibling_ok, ["A", "B"], [4.0, 3.0])
            _write_run(sibling_mismatched, ["A", "C"], [4.0, 2.0])  # набор стимулов другой - пропускается

            result = run_study.find_sibling_rankings(current, "demo", {"A", "B"})
            self.assertIn("seg1", result)
            self.assertEqual(result["seg1"], [["A", "B"]])  # только sibling_ok учтён

    def test_no_siblings_gives_empty_dict(self):
        with tempfile.TemporaryDirectory() as td:
            runs_root = Path(td)
            current = runs_root / "demo_20260101-0000"
            current.mkdir()
            result = run_study.find_sibling_rankings(current, "demo", {"A", "B"})
            self.assertEqual(result, {})


class TestComputeRunMode(unittest.TestCase):
    def _manifest(self, provider: str, validated_embedding: bool) -> dict:
        return {
            "stages": {
                "generate": {"provider": provider},
                "score": {"embedding_validated_stack": validated_embedding},
            }
        }

    def test_validated_requires_all_three_conditions(self):
        manifest = self._manifest("anthropic", True)
        controls_ok = {"applicable": True, "controls_failed": False}
        self.assertEqual(run_study.compute_run_mode(manifest, controls_ok), "validated")

    def test_agent_provider_is_never_validated(self):
        manifest = self._manifest("agent", True)
        controls_ok = {"applicable": True, "controls_failed": False}
        self.assertEqual(run_study.compute_run_mode(manifest, controls_ok), "exploratory")

    def test_unvalidated_embedding_is_exploratory(self):
        manifest = self._manifest("anthropic", False)
        controls_ok = {"applicable": True, "controls_failed": False}
        self.assertEqual(run_study.compute_run_mode(manifest, controls_ok), "exploratory")

    def test_failed_controls_is_exploratory(self):
        manifest = self._manifest("anthropic", True)
        controls_failed = {"applicable": True, "controls_failed": True}
        self.assertEqual(run_study.compute_run_mode(manifest, controls_failed), "exploratory")

    def test_controls_not_applicable_is_exploratory(self):
        manifest = self._manifest("anthropic", True)
        controls_off = {"applicable": False}
        self.assertEqual(run_study.compute_run_mode(manifest, controls_off), "exploratory")


class TestLoadOrInitManifestStability(unittest.TestCase):
    def test_controls_block_not_recomputed_on_reinit(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            study = _fake_study()
            study_path = _SKILL_ROOT / "studies" / "demo_claims.yaml"  # путь не читается повторно, только для поля

            manifest1 = run_study.load_or_init_manifest(
                run_dir, study, {"report": {"seed": 1}}, study_path, _SKILL_ROOT
            )
            run_study.save_manifest(run_dir, manifest1)

            # Второй вызов - ДРУГОЙ seed в конфиге; controls-блок обязан остаться
            # ТЕМ ЖЕ (прочитан с диска, не пересчитан), иначе слепые метки разошлись
            # бы с уже "сгенерированными" (в этом тесте гипотетическими) responses.
            manifest2 = run_study.load_or_init_manifest(
                run_dir, study, {"report": {"seed": 999}}, study_path, _SKILL_ROOT
            )
            self.assertEqual(manifest1["controls"], manifest2["controls"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

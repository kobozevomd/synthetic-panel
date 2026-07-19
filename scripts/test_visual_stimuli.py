#!/usr/bin/env python3
"""
test_visual_stimuli.py — юнит-тесты визуальных стимулов и пробы зрения
(scripts/run_study.py, spec_synthetic-panel_v1.4.md §1.1/§1.2/§2.2, Модуль 1).

Требование общего правила проекта: тесты зелёные БЕЗ сети и без embedding-модели.
Изображения-фикстуры генерируются PIL НА ЛЕТУ (tempfile.TemporaryDirectory) — ни
одного бинарного файла в репозитории. API-vision (generate.describe_image_via_provider)
покрыт МОК-провайдером (FakeVisionProvider, ниже) — реальных вызовов сети/API нет.

Покрытие:
    - resolve_image_path: абсолютный путь; относительно study.yaml; относительно
      skill_root; файл не найден (ValueError со списком путей); неподдерживаемый
      формат (ValueError).
    - check_image_parallelism: чистая функция на голых (id, w, h) — нет предупреждения
      при похожих размерах; предупреждение при разных пропорциях/площади; None
      при < 2 записей.
    - compute_stimulus_kind: text/image/mixed — все три ветки.
    - validate_and_resolve_stimuli: text-only (без изменений v1.3); image-only с
      обязательным label; смешанный; ошибка — ни text, ни image; ошибка — image-only
      без label; ошибка — файл не найден; мутация IN PLACE (image -> абсолютный путь);
      реальный PIL-warning о непараллельности на сгенерированных изображениях разного
      размера.
    - Проба зрения (§1.2): build_vision_check_targets (дедуп по пути, key_element per
      stimulus); keyword_recognized (эвристика, пустой key_element -> True); compute_
      vision_verdicts (явный key_element_recognized приоритетнее эвристики; агрегаты
      vision_failed/failed_stimulus_ids/n_stimuli_with_image); vision_check_is_pending;
      write_vision_check_yaml/load_vision_check (roundtrip); render_vision_check_markdown
      (смоук).
    - pick_placebo (§2.2): контрастные kind предпочитаются по умолчанию; банк без kind
      (обратная совместимость) даёт ПОБИТОВО тот же выбор, что и в v1.3.
    - build_controls_manifest: поле kind в placebo (из банка / фолбэк "neutral").
    - compute_stimulus_kind_line/compute_vision_check_section/compute_vision_check_status_line/
      compute_vision_check_failed_banner: контракт report_template.md ([B3], v1.4)
      — пусто для "text"; корректные тексты для "image"/"mixed", пройдена/не пройдена;
      НЕТ эмодзи 🟢/🔴 в STIMULUS_KIND_LINE (зарезервированы cjm_lint.py trust-map).
    - run_generate_stage (agent-режим, end-to-end): исследование с визуальным стимулом
      останавливается (exit 2) до заполнения 00_vision_check.yaml; после заполнения —
      останавливается на vision_failed без confirmed_despite_failures; после
      confirmed_despite_failures — продолжает (с предупреждением) и проставляет
      manifest["vision_check"]; исследование БЕЗ образов ведёт себя как раньше (no-op).
    - run_generate_stage (gigachat + образы): честная ошибка ДО попытки вызова.
    - run_generate_stage (API-режим, anthropic): вызывает generate.get_provider +
      generate.fill_vision_check_descriptions (мок), автоматически заполняет и
      продолжает без остановки, если key_element распознан.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

_SCRIPTS_DIR = Path(__file__).resolve().parent
_SKILL_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import generate  # noqa: E402
import run_study  # noqa: E402


def _make_png(path: Path, size: tuple[int, int] = (100, 100), color: tuple[int, int, int] = (5, 5, 5)) -> Path:
    Image.new("RGB", size, color).save(path, "PNG")
    return path


# ============================================================================
# §1.1 — схема/валидация изображений
# ============================================================================


class TestResolveImagePath(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp_path = Path(self._tmpdir.name)
        self.study_dir = self.tmp_path / "studies"
        self.study_dir.mkdir()
        self.study_path = self.study_dir / "demo.yaml"
        self.study_path.write_text("name: demo\n", encoding="utf-8")
        self.skill_root = self.tmp_path / "skill"
        self.skill_root.mkdir()

    def test_absolute_path_used_as_is(self):
        img = _make_png(self.tmp_path / "abs.png")
        resolved = run_study.resolve_image_path(str(img), self.study_path, self.skill_root)
        self.assertEqual(resolved, img)

    def test_relative_to_study_dir(self):
        img = _make_png(self.study_dir / "rel.png")
        resolved = run_study.resolve_image_path("rel.png", self.study_path, self.skill_root)
        self.assertEqual(resolved, img)

    def test_relative_to_skill_root_when_not_next_to_study(self):
        assets_dir = self.skill_root / "assets"
        assets_dir.mkdir()
        img = _make_png(assets_dir / "rel.png")
        resolved = run_study.resolve_image_path("assets/rel.png", self.study_path, self.skill_root)
        self.assertEqual(resolved, img)

    def test_missing_file_raises_value_error_listing_paths(self):
        with self.assertRaises(ValueError) as ctx:
            run_study.resolve_image_path("nope.png", self.study_path, self.skill_root)
        self.assertIn("nope.png", str(ctx.exception))

    def test_unsupported_extension_raises(self):
        bogus = self.study_dir / "bogus.bmp"
        bogus.write_bytes(b"not a real bmp")
        with self.assertRaises(ValueError) as ctx:
            run_study.resolve_image_path("bogus.bmp", self.study_path, self.skill_root)
        self.assertIn("формат", str(ctx.exception))

    def test_case_insensitive_extension(self):
        img = _make_png(self.study_dir / "upper.PNG")
        resolved = run_study.resolve_image_path("upper.PNG", self.study_path, self.skill_root)
        self.assertEqual(resolved, img)


class TestCheckImageParallelism(unittest.TestCase):
    def test_none_when_fewer_than_two(self):
        self.assertIsNone(run_study.check_image_parallelism([]))
        self.assertIsNone(run_study.check_image_parallelism([("A", 100, 100)]))

    def test_none_when_sizes_similar(self):
        dims = [("A", 400, 300), ("B", 410, 305), ("C", 395, 298)]
        self.assertIsNone(run_study.check_image_parallelism(dims))

    def test_warns_on_aspect_ratio_difference(self):
        dims = [("A", 400, 400), ("B", 800, 300)]  # 1.0 vs 2.67 - явно разные пропорции
        warning = run_study.check_image_parallelism(dims)
        self.assertIsNotNone(warning)
        self.assertIn("соотношение сторон", warning)

    def test_warns_on_area_difference(self):
        dims = [("A", 200, 200), ("B", 200, 500)]  # одинаковая ширина, площадь x2.5
        warning = run_study.check_image_parallelism(dims)
        self.assertIsNotNone(warning)
        self.assertIn("разрешение", warning)

    def test_does_not_block_just_warns(self):
        """Контракт §1.1: непараллельность - предупреждение, НЕ исключение."""
        dims = [("A", 100, 100), ("B", 1000, 100)]
        try:
            run_study.check_image_parallelism(dims)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"check_image_parallelism не должен бросать исключение: {exc}")


class TestComputeStimulusKind(unittest.TestCase):
    def test_text_when_no_image(self):
        self.assertEqual(run_study.compute_stimulus_kind(any_image=False, all_image=True), "text")

    def test_image_when_all_have_image(self):
        self.assertEqual(run_study.compute_stimulus_kind(any_image=True, all_image=True), "image")

    def test_mixed_when_some_have_image(self):
        self.assertEqual(run_study.compute_stimulus_kind(any_image=True, all_image=False), "mixed")


class TestValidateAndResolveStimuli(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp_path = Path(self._tmpdir.name)
        self.study_path = self.tmp_path / "study.yaml"
        self.study_path.write_text("name: demo\n", encoding="utf-8")
        self.skill_root = self.tmp_path / "skill"
        self.skill_root.mkdir()

    def test_text_only_study_unaffected(self):
        study = {"stimuli": [{"id": "A", "text": "Текст А"}, {"id": "B", "text": "Текст Б"}]}
        info = run_study.validate_and_resolve_stimuli(study, self.study_path, self.skill_root)
        self.assertEqual(info["stimulus_kind"], "text")
        self.assertIsNone(info["image_parallelism_warning"])
        self.assertEqual(study["stimuli"][0]["text"], "Текст А")  # не тронуто

    def test_image_only_requires_label(self):
        img = _make_png(self.tmp_path / "a.png")
        study = {"stimuli": [{"id": "A", "image": str(img)}, {"id": "B", "text": "Текст Б"}]}
        with self.assertRaises(ValueError) as ctx:
            run_study.validate_and_resolve_stimuli(study, self.study_path, self.skill_root)
        self.assertIn("label", str(ctx.exception))

    def test_image_only_with_label_succeeds_and_resolves_absolute_path(self):
        img = _make_png(self.tmp_path / "a.png")
        study = {
            "stimuli": [
                {"id": "A", "image": "a.png", "label": "Вариант А"},
                {"id": "B", "image": "a.png", "label": "Вариант Б"},
            ]
        }
        info = run_study.validate_and_resolve_stimuli(study, self.study_path, self.skill_root)
        self.assertEqual(info["stimulus_kind"], "image")
        self.assertEqual(study["stimuli"][0]["image"], str(img))
        self.assertTrue(Path(study["stimuli"][0]["image"]).is_absolute())

    def test_mixed_stimuli_kind(self):
        img = _make_png(self.tmp_path / "a.png")
        study = {"stimuli": [{"id": "A", "text": "Текст", "image": str(img)}, {"id": "B", "text": "Только текст"}]}
        info = run_study.validate_and_resolve_stimuli(study, self.study_path, self.skill_root)
        self.assertEqual(info["stimulus_kind"], "mixed")

    def test_no_text_and_no_image_raises(self):
        study = {"stimuli": [{"id": "A"}, {"id": "B", "text": "Текст Б"}]}
        with self.assertRaises(ValueError) as ctx:
            run_study.validate_and_resolve_stimuli(study, self.study_path, self.skill_root)
        self.assertIn("A", str(ctx.exception))

    def test_missing_image_file_raises(self):
        study = {"stimuli": [{"id": "A", "image": "missing.png", "label": "L"}, {"id": "B", "text": "T"}]}
        with self.assertRaises(ValueError):
            run_study.validate_and_resolve_stimuli(study, self.study_path, self.skill_root)

    def test_parallelism_warning_surfaces_from_real_pil_images(self):
        img_a = _make_png(self.tmp_path / "a.png", size=(300, 300))
        img_b = _make_png(self.tmp_path / "b.png", size=(900, 200))
        study = {
            "stimuli": [
                {"id": "A", "image": str(img_a), "label": "A"},
                {"id": "B", "image": str(img_b), "label": "B"},
            ]
        }
        info = run_study.validate_and_resolve_stimuli(study, self.study_path, self.skill_root)
        self.assertIsNotNone(info["image_parallelism_warning"])

    def test_no_warning_for_similarly_sized_real_images(self):
        img_a = _make_png(self.tmp_path / "a.png", size=(400, 300))
        img_b = _make_png(self.tmp_path / "b.png", size=(410, 305))
        study = {
            "stimuli": [
                {"id": "A", "image": str(img_a), "label": "A"},
                {"id": "B", "image": str(img_b), "label": "B"},
            ]
        }
        info = run_study.validate_and_resolve_stimuli(study, self.study_path, self.skill_root)
        self.assertIsNone(info["image_parallelism_warning"])


# ============================================================================
# §1.2 — проба зрения
# ============================================================================


class TestBuildVisionCheckTargets(unittest.TestCase):
    def test_empty_for_text_only_study(self):
        study = {"stimuli": [{"id": "A", "text": "T"}, {"id": "B", "text": "T2"}]}
        self.assertEqual(run_study.build_vision_check_targets(study), [])

    def test_dedups_by_image_path_and_collects_key_elements(self):
        study = {
            "stimuli": [
                {"id": "A", "image": "/x/shared.png", "label": "A", "key_element": "логотип"},
                {"id": "B", "image": "/x/shared.png", "label": "B"},
                {"id": "C", "image": "/x/other.png", "label": "C", "key_element": "кружка"},
            ]
        }
        targets = run_study.build_vision_check_targets(study)
        self.assertEqual(len(targets), 2)
        shared = next(t for t in targets if t["image_path"] == "/x/shared.png")
        self.assertEqual(set(shared["stimulus_ids"]), {"A", "B"})
        self.assertEqual(shared["key_elements"], {"A": "логотип"})  # B без key_element - не попадает
        other = next(t for t in targets if t["image_path"] == "/x/other.png")
        self.assertEqual(other["key_elements"], {"C": "кружка"})


class TestKeywordRecognized(unittest.TestCase):
    def test_empty_key_element_always_true(self):
        self.assertTrue(run_study.keyword_recognized("что угодно", ""))

    def test_recognized_when_all_significant_words_present(self):
        self.assertTrue(
            run_study.keyword_recognized("На макете белая кружка с логотипом бренда.", "белая кружка")
        )

    def test_not_recognized_when_word_missing(self):
        self.assertFalse(
            run_study.keyword_recognized("На макете просто пустой фон без деталей.", "белая кружка")
        )

    def test_case_insensitive(self):
        self.assertTrue(run_study.keyword_recognized("КРУЖКА на столе", "кружка"))


class TestComputeVisionVerdicts(unittest.TestCase):
    def _vc(self, description: str, key_element: str | None, explicit_recognized=None, confirmed=False) -> dict:
        return {
            "confirmed_despite_failures": confirmed,
            "images": [
                {
                    "image_path": "/x/a.png",
                    "stimulus_ids": ["A"],
                    "key_element_by_stimulus": {"A": key_element} if key_element else {},
                    "description": description,
                    "key_element_recognized": explicit_recognized,
                }
            ],
        }

    def test_ok_when_no_key_element(self):
        vc = self._vc("любое описание", key_element=None)
        result = run_study.compute_vision_verdicts(vc)
        self.assertFalse(result["vision_failed"])
        self.assertEqual(result["per_image"][0]["per_stimulus_verdict"]["A"], "ok")

    def test_heuristic_recognizes_when_words_present(self):
        vc = self._vc("Кружка с логотипом на столе.", key_element="кружка")
        result = run_study.compute_vision_verdicts(vc)
        self.assertFalse(result["vision_failed"])

    def test_heuristic_fails_when_words_absent(self):
        vc = self._vc("Пустой белый фон.", key_element="кружка")
        result = run_study.compute_vision_verdicts(vc)
        self.assertTrue(result["vision_failed"])
        self.assertEqual(result["failed_stimulus_ids"], ["A"])

    def test_explicit_recognized_true_overrides_heuristic_failure(self):
        """Явное key_element_recognized (агент сам видел изображение) приоритетнее
        эвристики по словам описания - даже если слова не совпали."""
        vc = self._vc("Пустой белый фон.", key_element="кружка", explicit_recognized=True)
        result = run_study.compute_vision_verdicts(vc)
        self.assertFalse(result["vision_failed"])

    def test_explicit_recognized_false_overrides_heuristic_success(self):
        vc = self._vc("Кружка с логотипом.", key_element="кружка", explicit_recognized=False)
        result = run_study.compute_vision_verdicts(vc)
        self.assertTrue(result["vision_failed"])

    def test_confirmed_despite_failures_passthrough(self):
        vc = self._vc("Пустой фон.", key_element="кружка", confirmed=True)
        result = run_study.compute_vision_verdicts(vc)
        self.assertTrue(result["confirmed_despite_failures"])

    def test_n_stimuli_with_image_counts_all_stimulus_ids(self):
        vc = {
            "confirmed_despite_failures": False,
            "images": [
                {"image_path": "/a.png", "stimulus_ids": ["A", "B"], "key_element_by_stimulus": {}, "description": "d"},
                {"image_path": "/c.png", "stimulus_ids": ["C"], "key_element_by_stimulus": {}, "description": "d"},
            ],
        }
        result = run_study.compute_vision_verdicts(vc)
        self.assertEqual(result["n_stimuli_with_image"], 3)

    def test_vision_check_source_is_propagated_into_per_image(self):
        """v1.4 fix (docs/review_v1.4.md находка №3): vision_check_source —
        честная запись источника описания (agent_self_reported/api_vision),
        не проверка — просто обязана дойти до per_image без изменений."""
        vc = {
            "confirmed_despite_failures": False,
            "images": [
                {
                    "image_path": "/a.png", "stimulus_ids": ["A"], "key_element_by_stimulus": {},
                    "description": "d", "vision_check_source": "api_vision",
                },
                {
                    "image_path": "/b.png", "stimulus_ids": ["B"], "key_element_by_stimulus": {},
                    "description": "d", "vision_check_source": "agent_self_reported",
                },
                {"image_path": "/c.png", "stimulus_ids": ["C"], "key_element_by_stimulus": {}, "description": "d"},
            ],
        }
        result = run_study.compute_vision_verdicts(vc)
        sources = {img["image_path"]: img["vision_check_source"] for img in result["per_image"]}
        self.assertEqual(sources["/a.png"], "api_vision")
        self.assertEqual(sources["/b.png"], "agent_self_reported")
        self.assertIsNone(sources["/c.png"])  # не задано в исходном vc -> None, не падает


class TestVisionCheckPending(unittest.TestCase):
    def test_pending_when_any_description_empty(self):
        vc = {"images": [{"description": "заполнено"}, {"description": ""}]}
        self.assertTrue(run_study.vision_check_is_pending(vc))

    def test_not_pending_when_all_filled(self):
        vc = {"images": [{"description": "заполнено"}, {"description": "тоже заполнено"}]}
        self.assertFalse(run_study.vision_check_is_pending(vc))

    def test_not_pending_when_no_images(self):
        self.assertFalse(run_study.vision_check_is_pending({"images": []}))


class TestVisionCheckYamlIO(unittest.TestCase):
    def test_write_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            targets = [{"image_path": "/a.png", "stimulus_ids": ["A"], "key_elements": {"A": "логотип"}}]
            stub = run_study._vision_check_stub(targets)
            run_study.write_vision_check_yaml(stub, run_dir)
            loaded = run_study.load_vision_check(run_dir)
            self.assertEqual(loaded["images"][0]["image_path"], "/a.png")
            self.assertEqual(loaded["images"][0]["key_element_by_stimulus"], {"A": "логотип"})
            self.assertEqual(loaded["images"][0]["description"], "")
            self.assertFalse(loaded["confirmed_despite_failures"])

    def test_stub_has_vision_check_source_none_before_fill(self):
        """v1.4 fix: стаб ДО заполнения (ни агентом, ни API-вызовом) обязан
        честно нести vision_check_source=None, а не молчать про отсутствие поля."""
        targets = [{"image_path": "/a.png", "stimulus_ids": ["A"], "key_elements": {}}]
        stub = run_study._vision_check_stub(targets)
        self.assertIsNone(stub["images"][0]["vision_check_source"])

    def test_load_returns_none_when_absent(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(run_study.load_vision_check(Path(td)))


class TestRenderVisionCheckMarkdown(unittest.TestCase):
    def test_smoke_passed(self):
        verdicts = run_study.compute_vision_verdicts(
            {
                "confirmed_despite_failures": False,
                "images": [
                    {
                        "image_path": "/a.png", "stimulus_ids": ["A"],
                        "key_element_by_stimulus": {"A": "кружка"}, "description": "Кружка на столе.",
                    }
                ],
            }
        )
        md = run_study.render_vision_check_markdown(verdicts)
        self.assertIn("/a.png", md)
        self.assertIn("OK", md)

    def test_smoke_failed_shows_confirmation_status(self):
        verdicts = run_study.compute_vision_verdicts(
            {
                "confirmed_despite_failures": False,
                "images": [
                    {
                        "image_path": "/a.png", "stimulus_ids": ["A"],
                        "key_element_by_stimulus": {"A": "кружка"}, "description": "Пустой фон.",
                    }
                ],
            }
        )
        md = run_study.render_vision_check_markdown(verdicts)
        self.assertIn("vision_failed", md)
        self.assertIn("НЕТ", md)

    def test_vision_check_source_line_present_for_each_mode(self):
        """v1.4 fix (docs/review_v1.4.md находка №3): 00_vision_check.md обязан
        честно называть источник описания — agent-самоотчёт, API vision-вызов,
        или "не указан" для прогонов до этой правки/незаполненного поля."""
        verdicts = run_study.compute_vision_verdicts(
            {
                "confirmed_despite_failures": False,
                "images": [
                    {
                        "image_path": "/agent.png", "stimulus_ids": ["A"], "key_element_by_stimulus": {},
                        "description": "d", "vision_check_source": "agent_self_reported",
                    },
                    {
                        "image_path": "/api.png", "stimulus_ids": ["B"], "key_element_by_stimulus": {},
                        "description": "d", "vision_check_source": "api_vision",
                    },
                    {
                        "image_path": "/old.png", "stimulus_ids": ["C"], "key_element_by_stimulus": {},
                        "description": "d",
                    },
                ],
            }
        )
        md = run_study.render_vision_check_markdown(verdicts)
        self.assertIn("Источник описания:", md)
        self.assertIn("агент (самоотчёт", md)
        self.assertIn("API vision-вызов", md)
        self.assertIn("не указан", md)


# ============================================================================
# §2.2 — контрастные плацебо
# ============================================================================


class TestPickPlaceboContrastive(unittest.TestCase):
    def test_prefers_contrastive_kinds_when_present(self):
        bank = [
            {"id": "n1", "kind": "neutral", "text": "Нейтрально 1"},
            {"id": "n2", "kind": "neutral", "text": "Нейтрально 2"},
            {"id": "i1", "kind": "irrelevant", "text": "Не по теме"},
            {"id": "e1", "kind": "empty_promise", "text": "Пустое обещание"},
        ]
        for seed in range(20):
            chosen = run_study.pick_placebo(bank, seed=seed, study_name=f"study_{seed}")
            self.assertIn(chosen["kind"], run_study.CONTRASTIVE_PLACEBO_KINDS)

    def test_bank_without_kind_field_behaves_like_v13(self):
        """Обратная совместимость: банк БЕЗ поля kind вовсе -> pool == весь bank,
        тот же rng.choice(bank), что и до v1.4 (побитово тот же выбор)."""
        import random

        bank = [{"id": "p1", "text": "A"}, {"id": "p2", "text": "B"}, {"id": "p3", "text": "C"}]
        chosen = run_study.pick_placebo(bank, seed=42, study_name="demo")
        rng = generate.make_rng(42, "demo", "controls_placebo")
        expected = rng.choice(bank)
        self.assertEqual(chosen, expected)

    def test_deterministic_across_calls(self):
        bank = [
            {"id": "n1", "kind": "neutral", "text": "N"},
            {"id": "i1", "kind": "irrelevant", "text": "I"},
        ]
        c1 = run_study.pick_placebo(bank, seed=7, study_name="demo")
        c2 = run_study.pick_placebo(bank, seed=7, study_name="demo")
        self.assertEqual(c1, c2)


class TestBuildControlsManifestPlaceboKind(unittest.TestCase):
    def _study(self) -> dict:
        return {
            "name": "demo",
            "stimuli": [{"id": "A", "text": "Клейм А"}, {"id": "B", "text": "Клейм Б"}],
        }

    def test_kind_field_present_from_real_bank(self):
        cm = run_study.build_controls_manifest(self._study(), _SKILL_ROOT, seed=1)
        self.assertIn(cm["placebo"]["kind"], run_study.CONTRASTIVE_PLACEBO_KINDS | {"neutral"})

    def test_kind_defaults_to_neutral_when_bank_entry_lacks_it(self):
        with mock.patch.object(
            run_study, "load_placebo_bank", return_value=[{"id": "p1", "text": "Без kind"}]
        ):
            cm = run_study.build_controls_manifest(self._study(), _SKILL_ROOT, seed=1)
        self.assertEqual(cm["placebo"]["kind"], "neutral")


# ============================================================================
# report_template.md v1.4 контракт — compute_stimulus_kind_line/compute_vision_check_*
# ============================================================================


class TestReportTemplateContractHelpers(unittest.TestCase):
    def test_stimulus_kind_line_empty_for_text(self):
        self.assertEqual(run_study.compute_stimulus_kind_line("text", None), "")

    def test_stimulus_kind_line_image_passed(self):
        line = run_study.compute_stimulus_kind_line("image", {"vision_failed": False})
        self.assertIn("🖼️ ВИЗУАЛЬНЫЕ", line)
        self.assertIn("пройдена", line)
        self.assertNotIn("🟢", line)
        self.assertNotIn("🔴", line)

    def test_stimulus_kind_line_mixed_failed(self):
        line = run_study.compute_stimulus_kind_line("mixed", {"vision_failed": True})
        self.assertIn("📝🖼️ СМЕШАННЫЕ", line)
        self.assertIn("НЕ пройдена", line)

    def test_vision_check_status_line_empty_when_no_verdict(self):
        self.assertEqual(run_study.compute_vision_check_status_line(None), "")

    def test_vision_check_status_line_passed(self):
        line = run_study.compute_vision_check_status_line({"vision_failed": False})
        self.assertIn("пройдена", line)
        self.assertNotIn("НЕ пройдена", line)

    def test_vision_check_status_line_failed_shows_counts(self):
        line = run_study.compute_vision_check_status_line(
            {"vision_failed": True, "failed_stimulus_ids": ["A"], "n_stimuli_with_image": 3}
        )
        self.assertIn("1 из 3", line)

    def test_vision_check_section_empty_when_no_verdict(self):
        self.assertEqual(run_study.compute_vision_check_section(None), "")

    def test_vision_check_section_mentions_key_contract_phrases(self):
        section = run_study.compute_vision_check_section({"vision_failed": False})
        self.assertIn("**Проба зрения:**", section)
        self.assertIn("00_vision_check.md", section)

    def test_vision_check_failed_banner_empty_when_not_failed(self):
        self.assertEqual(run_study.compute_vision_check_failed_banner(None), "")
        self.assertEqual(run_study.compute_vision_check_failed_banner({"vision_failed": False}), "")

    def test_vision_check_failed_banner_contains_contract_marker(self):
        banner = run_study.compute_vision_check_failed_banner(
            {"vision_failed": True, "failed_stimulus_ids": ["A"], "confirmed_despite_failures": True}
        )
        self.assertIn("проба зрения не пройдена", banner)


# ============================================================================
# run_generate_stage — сквозной сценарий гейта (agent/API/gigachat)
# ============================================================================


class _FakeVisionProvider(generate.BaseProvider):
    """Конкретная (не абстрактная) реализация BaseProvider для мока в тестах
    API-режима — generate.BaseProvider абстрактен, инстанцировать напрямую
    нельзя даже под mock.patch."""

    name = "fake"

    def __init__(self, canned_text: str = "На макете видна кружка и логотип бренда."):
        self.canned_text = canned_text
        self.calls: list[dict] = []

    def generate(self, system_prompt, user_prompt, temperature, image_path=None):
        self.calls.append(
            {"system_prompt": system_prompt, "user_prompt": user_prompt, "image_path": image_path}
        )
        return generate.GenerationResult(text=self.canned_text, model="fake-model", request_id="r1")


class TestRunGenerateStageVisionGate(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.run_dir = Path(self._tmpdir.name)
        self.img_path = str(_make_png(self.run_dir / "stim.png"))
        self.study = {
            "name": "demo_visual",
            "segments": ["seg1"],
            "respondents_per_segment": 1,
            "samples_per_respondent": 1,
            "stimuli": [
                {"id": "A", "image": self.img_path, "label": "Вариант А", "key_element": "кружка"},
                {"id": "B", "text": "Текстовый стимул"},
            ],
        }
        self.segments = {"seg1": {"id": "seg1", "name": "Сегмент"}}
        self.manifest = {"controls": {"enabled": False}, "stages": {}}

    def _config(self, provider: str = "agent") -> dict:
        return {"llm": {"provider": provider}}

    def test_agent_mode_stops_until_vision_check_filled(self):
        with self.assertRaises(SystemExit) as ctx:
            run_study.run_generate_stage(
                self.run_dir, self.study, self._config("agent"), self.segments, "Вопрос?",
                self.run_dir / "study.yaml", self.manifest,
            )
        self.assertEqual(ctx.exception.code, 2)
        vc_path = self.run_dir / run_study.VISION_CHECK_YAML_NAME
        self.assertTrue(vc_path.exists())
        self.assertFalse((self.run_dir / "responses_todo.jsonl").exists())

    def test_agent_mode_proceeds_after_description_recognizes_key_element(self):
        # Первый вызов создаёт стаб.
        with self.assertRaises(SystemExit):
            run_study.run_generate_stage(
                self.run_dir, self.study, self._config("agent"), self.segments, "Вопрос?",
                self.run_dir / "study.yaml", self.manifest,
            )
        vc = run_study.load_vision_check(self.run_dir)
        vc["images"][0]["description"] = "На макете видна белая кружка с логотипом."
        run_study.write_vision_check_yaml(vc, self.run_dir)

        outcome = run_study.run_generate_stage(
            self.run_dir, self.study, self._config("agent"), self.segments, "Вопрос?",
            self.run_dir / "study.yaml", self.manifest,
        )
        self.assertEqual(outcome.status, "todo")
        self.assertFalse(self.manifest["vision_check"]["vision_failed"])
        self.assertTrue((self.run_dir / "responses_todo.jsonl").exists())
        # Строка стимула A обязана нести image_path (agent читает файл сам).
        import json

        rows = [json.loads(line) for line in (self.run_dir / "responses_todo.jsonl").read_text().splitlines()]
        row_a = next(r for r in rows if r["stimulus_id"] == "A")
        self.assertEqual(row_a["image_path"], self.img_path)
        row_b = next(r for r in rows if r["stimulus_id"] == "B")
        self.assertIsNone(row_b["image_path"])
        # AGENT_TASK.md обязан упомянуть визуальные стимулы.
        task_md = (self.run_dir / "AGENT_TASK.md").read_text(encoding="utf-8")
        self.assertIn("Визуальные стимулы", task_md)

    def test_agent_mode_stops_on_vision_failed_without_confirmation(self):
        with self.assertRaises(SystemExit):
            run_study.run_generate_stage(
                self.run_dir, self.study, self._config("agent"), self.segments, "Вопрос?",
                self.run_dir / "study.yaml", self.manifest,
            )
        vc = run_study.load_vision_check(self.run_dir)
        vc["images"][0]["description"] = "Пустой фон, ничего не видно."  # НЕ содержит "кружка"
        run_study.write_vision_check_yaml(vc, self.run_dir)

        with self.assertRaises(SystemExit) as ctx:
            run_study.run_generate_stage(
                self.run_dir, self.study, self._config("agent"), self.segments, "Вопрос?",
                self.run_dir / "study.yaml", self.manifest,
            )
        self.assertEqual(ctx.exception.code, 2)
        self.assertFalse((self.run_dir / "responses_todo.jsonl").exists())

    def test_agent_mode_proceeds_after_explicit_confirmation_despite_failure(self):
        with self.assertRaises(SystemExit):
            run_study.run_generate_stage(
                self.run_dir, self.study, self._config("agent"), self.segments, "Вопрос?",
                self.run_dir / "study.yaml", self.manifest,
            )
        vc = run_study.load_vision_check(self.run_dir)
        vc["images"][0]["description"] = "Пустой фон, ничего не видно."
        vc["confirmed_despite_failures"] = True
        run_study.write_vision_check_yaml(vc, self.run_dir)

        outcome = run_study.run_generate_stage(
            self.run_dir, self.study, self._config("agent"), self.segments, "Вопрос?",
            self.run_dir / "study.yaml", self.manifest,
        )
        self.assertEqual(outcome.status, "todo")
        self.assertTrue(self.manifest["vision_check"]["vision_failed"])
        self.assertTrue(self.manifest["vision_check"]["confirmed_despite_failures"])

    def test_text_only_study_is_unaffected_no_op(self):
        text_study = dict(self.study)
        text_study["stimuli"] = [{"id": "A", "text": "T1"}, {"id": "B", "text": "T2"}]
        outcome = run_study.run_generate_stage(
            self.run_dir, text_study, self._config("agent"), self.segments, "Вопрос?",
            self.run_dir / "study.yaml", {"controls": {"enabled": False}, "stages": {}},
        )
        self.assertEqual(outcome.status, "todo")
        self.assertFalse((self.run_dir / run_study.VISION_CHECK_YAML_NAME).exists())

    def test_gigachat_provider_refuses_before_any_call(self):
        with self.assertRaises(SystemExit) as ctx:
            run_study.run_generate_stage(
                self.run_dir, self.study, self._config("gigachat"), self.segments, "Вопрос?",
                self.run_dir / "study.yaml", self.manifest,
            )
        self.assertEqual(ctx.exception.code, 1)
        self.assertFalse((self.run_dir / run_study.VISION_CHECK_YAML_NAME).exists())

    def test_api_mode_auto_fills_and_proceeds_when_recognized(self):
        fake_provider = _FakeVisionProvider("На макете видна кружка и логотип бренда.")
        fake_outcome = generate.GenerateOutcome(
            status="completed", responses_path=self.run_dir / "responses.jsonl",
            todo_path=None, n_tasks=2, provider="anthropic", temperature_control=True,
        )
        with mock.patch.object(generate, "get_provider", return_value=fake_provider) as get_provider_mock:
            with mock.patch.object(generate, "generate_responses", return_value=fake_outcome):
                result = run_study.run_generate_stage(
                    self.run_dir, self.study, self._config("anthropic"), self.segments, "Вопрос?",
                    self.run_dir / "study.yaml", self.manifest,
                )
        self.assertEqual(result.status, "completed")
        self.assertFalse(self.manifest["vision_check"]["vision_failed"])
        self.assertGreaterEqual(get_provider_mock.call_count, 1)
        self.assertEqual(len(fake_provider.calls), 1)  # ровно один vision-вызов (1 изображение)
        vc = run_study.load_vision_check(self.run_dir)
        self.assertFalse(run_study.vision_check_is_pending(vc))


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""
test_segments_export.py — юнит-тесты scripts/segments_export.py, фокус на
задании [B2] п.4 "passthrough полей gender и name_original"
(spec_synthetic-panel_v1.2.md §Модуль 1 п.2, §Модуль 2 п.1).

Запуск:
    python scripts/test_segments_export.py
    (или: python -m unittest scripts.test_segments_export -v из корня скилла)

Покрытие:
    - name_original: пробрасывается, если есть во входной записи; НЕ
      синтезируется из `name`, если отсутствует во входе;
    - persona_jitter.gender: пробрасывается насквозь (как часть persona_jitter
      целиком, без отдельного кода) — есть/нет во входе, оба случая;
    - check_persona_jitter_vocab: НЕ ругается на легитимные значения gender
      (список строк, включая описательные вроде «преимущественно ж»), но
      предупреждает (не бросает исключение), если gender задан НЕ списком строк;
    - end-to-end export_segments() на временной файловой системе: оба поля
      корректно долетают до итогового panel/segments/<slug>/<id>.yaml.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import segments_export as se  # noqa: E402


# ----------------------------------------------------------------------------
# name_original
# ----------------------------------------------------------------------------


class TestNameOriginalPassthrough(unittest.TestCase):
    def test_name_original_present_is_passed_through(self):
        seg = {"id": "seg1", "name": "Те, кто читает составы", "name_original": "Ингредиент-ориентированные рутинщики"}
        doc = se.build_segment_yaml(seg)
        self.assertEqual(doc["name_original"], "Ингредиент-ориентированные рутинщики")
        self.assertEqual(doc["name"], "Те, кто читает составы")

    def test_name_original_absent_is_not_synthesized(self):
        """Старые сегменты (до правила «Редактор-гуманизатор») не несут
        name_original — НЕ должен появляться в выходе из ничего."""
        seg = {"id": "seg1", "name": "Старое имя без правки"}
        doc = se.build_segment_yaml(seg)
        self.assertNotIn("name_original", doc)

    def test_name_original_empty_string_is_not_passed_through(self):
        """Пустая строка — не значение, а её отсутствие (falsy) — не должно
        создавать бессмысленное `name_original: ''` в выходном YAML."""
        seg = {"id": "seg1", "name": "Имя", "name_original": ""}
        doc = se.build_segment_yaml(seg)
        self.assertNotIn("name_original", doc)


# ----------------------------------------------------------------------------
# persona_jitter.gender — passthrough
# ----------------------------------------------------------------------------


class TestGenderPassthrough(unittest.TestCase):
    def test_gender_present_flows_through_persona_jitter(self):
        seg = {"id": "seg1", "name": "Сегмент", "persona_jitter": {"age": [20, 38], "gender": ["ж", "м"]}}
        doc = se.build_segment_yaml(seg)
        self.assertEqual(doc["persona_jitter"]["gender"], ["ж", "м"])

    def test_gender_absent_leaves_persona_jitter_without_it(self):
        seg = {"id": "seg1", "name": "Сегмент", "persona_jitter": {"age": [20, 38]}}
        doc = se.build_segment_yaml(seg)
        self.assertNotIn("gender", doc["persona_jitter"])

    def test_descriptive_gender_value_is_passed_through_verbatim(self):
        """«преимущественно ж» — легитимный пример из спецификации, не
        закрытый словарь (в отличие от income_level/city_tier)."""
        seg = {"id": "seg1", "name": "Сегмент", "persona_jitter": {"gender": ["преимущественно ж"]}}
        doc = se.build_segment_yaml(seg)
        self.assertEqual(doc["persona_jitter"]["gender"], ["преимущественно ж"])


class TestGenderVocabWarning(unittest.TestCase):
    def test_valid_gender_list_produces_no_warning(self):
        seg = {"id": "seg1", "name": "Сегмент", "persona_jitter": {"gender": ["ж", "м"]}}
        warnings = se.check_persona_jitter_vocab(seg)
        self.assertEqual(warnings, [])

    def test_missing_gender_produces_no_warning(self):
        """Отсутствие gender у старого сегмента — ожидаемо, не повод предупреждать
        на этом этапе (это забота targeting_export.py: «уточнить», не сборки сегмента)."""
        seg = {"id": "seg1", "name": "Сегмент", "persona_jitter": {"age": [20, 38]}}
        warnings = se.check_persona_jitter_vocab(seg)
        self.assertEqual(warnings, [])

    def test_gender_as_bare_string_produces_warning(self):
        seg = {"id": "seg1", "name": "Сегмент", "persona_jitter": {"gender": "ж"}}
        warnings = se.check_persona_jitter_vocab(seg)
        self.assertTrue(any("gender" in w for w in warnings), warnings)

    def test_gender_with_non_string_element_produces_warning(self):
        seg = {"id": "seg1", "name": "Сегмент", "persona_jitter": {"gender": ["ж", 42]}}
        warnings = se.check_persona_jitter_vocab(seg)
        self.assertTrue(any("gender" in w for w in warnings), warnings)

    def test_gender_vocab_warning_is_soft_not_an_exception(self):
        """check_persona_jitter_vocab возвращает список — никогда не бросает
        исключение, даже на явно некорректном gender."""
        seg = {"id": "seg1", "name": "Сегмент", "persona_jitter": {"gender": 123}}
        warnings = se.check_persona_jitter_vocab(seg)  # не бросает
        self.assertIsInstance(warnings, list)


# ----------------------------------------------------------------------------
# End-to-end: export_segments() на временной файловой системе
# ----------------------------------------------------------------------------


class TestExportSegmentsEndToEnd(unittest.TestCase):
    def test_gender_and_name_original_reach_final_yaml(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            merged_path = tmp / "01_segments_merged.yaml"
            merged_path.write_text(
                yaml.safe_dump(
                    {
                        "cjm_study": "cjm_test_demo",
                        "segments": [
                            {
                                "id": "seg1",
                                "name": "Те, кто читает составы",
                                "name_original": "Ингредиент-ориентированные рутинщики",
                                "stability": "3/3",
                                "persona_jitter": {"age": [20, 38], "gender": ["ж", "м"], "income_level": ["average"]},
                            }
                        ],
                        "unstable_segments": [],
                    },
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
            out_root = tmp / "panel" / "segments"
            result = se.export_segments(merged_path, skill_root=tmp, out_root=out_root)
            self.assertEqual(result["warnings"], [])
            written = result["written"]
            self.assertEqual(len(written), 1)
            doc = yaml.safe_load(written[0].read_text(encoding="utf-8"))
            self.assertEqual(doc["name_original"], "Ингредиент-ориентированные рутинщики")
            self.assertEqual(doc["persona_jitter"]["gender"], ["ж", "м"])

    def test_segment_without_gender_or_name_original_still_exports_cleanly(self):
        """Регресс: старые прогоны без этих двух НОВЫХ полей v1.2 продолжают
        экспортироваться без ошибок и без синтетических значений."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            merged_path = tmp / "01_segments_merged.yaml"
            merged_path.write_text(
                yaml.safe_dump(
                    {
                        "cjm_study": "cjm_test_demo",
                        "segments": [
                            {
                                "id": "seg1",
                                "name": "Старый сегмент",
                                "stability": "3/3",
                                "persona_jitter": {"age": [20, 38], "income_level": ["average"]},
                            }
                        ],
                        "unstable_segments": [],
                    },
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
            out_root = tmp / "panel" / "segments"
            result = se.export_segments(merged_path, skill_root=tmp, out_root=out_root)
            self.assertEqual(result["warnings"], [])
            doc = yaml.safe_load(result["written"][0].read_text(encoding="utf-8"))
            self.assertNotIn("name_original", doc)
            self.assertNotIn("gender", doc["persona_jitter"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

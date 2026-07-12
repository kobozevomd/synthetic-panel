#!/usr/bin/env python3
"""
test_targeting_export.py — юнит-тесты scripts/targeting_export.py
(spec_synthetic-panel_v1.2.md §Модуль 2, задание [B2] п.2/п.5).

Запуск:
    python scripts/test_targeting_export.py
    (или: python -m unittest scripts.test_targeting_export -v из корня скилла)

Покрытие:
    - валидная схема (4 оси, все обязательные поля) -> без исключений;
    - битая схема — по фикстуре на КАЖДЫЙ тип: отсутствующий segment_id,
      неизвестный segment_id, отсутствующая ось, неверный trust-маркер,
      пустой/не-строковый values, запрещённые термины (охват/cpm/размер
      аудитории — включая контрольный НЕ-ложноположительный тест на «размер
      упаковки» в purchases), словоформы «доля» (включая контроль на
      «долго»/«должен»/«доллар»/«долина», которые НЕ должны ловиться), процент
      цифрой и словом;
    - «Правило для gender» (check_gender_honesty) — сегмент с полем/без поля,
      честное «уточнить»+🔴 проходит, выдуманный пол без «уточнить» И/ИЛИ без
      🔴 отклоняется;
    - сборка markdown-таблицы (формат — references/cjm_report_template.md §7
      буквально: заголовки "Демография 🟢 | Доход 🟢 | Контент 🟡 | Покупки 🟡",
      значения через "; ") и targeting_matrix.yaml;
    - CLI end-to-end (subprocess): happy path (--run + --segments-root),
      битая схема -> exit 1, отсутствие 05_targeting_*.yaml -> exit 1.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import targeting_export as te  # noqa: E402

TARGETING_EXPORT_PATH = _SCRIPTS_DIR / "targeting_export.py"


def _write_yaml(path: Path, doc: dict) -> Path:
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _valid_record(segment_id: str = "seg1") -> dict:
    return {
        "segment_id": segment_id,
        "segment_name": "Тестовый сегмент",
        "demographics": {"values": ["мужчины и женщины, 20-38 лет"], "trust": "🟢", "source": "persona_jitter"},
        "income": {"values": ["средний"], "trust": "🟢", "source": "persona_jitter.income_level"},
        "content": {"values": ["разборы блогеров", "запрос: пример"], "trust": "🟡", "source": "02_cjm_seg1.md"},
        "purchases": {"values": ["аптечные средства"], "trust": "🟡", "source": "02_cjm_seg1.md"},
    }


# ----------------------------------------------------------------------------
# Валидация схемы
# ----------------------------------------------------------------------------


class TestValidRecordPasses(unittest.TestCase):
    def test_valid_record_passes_without_exception(self):
        doc = _valid_record()
        te.validate_targeting_record(doc, Path("05_targeting_seg1.yaml"), known_segment_ids={"seg1"})  # не бросает


class TestBrokenSchemaFixtures(unittest.TestCase):
    def test_missing_segment_id_fails(self):
        doc = _valid_record()
        del doc["segment_id"]
        with self.assertRaises(ValueError) as cm:
            te.validate_targeting_record(doc, Path("x.yaml"))
        self.assertIn("segment_id", str(cm.exception))

    def test_unknown_segment_id_fails(self):
        doc = _valid_record(segment_id="does_not_exist")
        with self.assertRaises(ValueError) as cm:
            te.validate_targeting_record(doc, Path("x.yaml"), known_segment_ids={"seg1", "seg2"})
        self.assertIn("does_not_exist", str(cm.exception))

    def test_missing_axis_fails(self):
        doc = _valid_record()
        del doc["purchases"]
        with self.assertRaises(ValueError) as cm:
            te.validate_targeting_record(doc, Path("x.yaml"))
        self.assertIn("purchases", str(cm.exception))

    def test_invalid_trust_marker_fails(self):
        doc = _valid_record()
        doc["income"]["trust"] = "green"
        with self.assertRaises(ValueError) as cm:
            te.validate_targeting_record(doc, Path("x.yaml"))
        self.assertIn("trust", str(cm.exception))

    def test_empty_values_list_fails(self):
        doc = _valid_record()
        doc["content"]["values"] = []
        with self.assertRaises(ValueError):
            te.validate_targeting_record(doc, Path("x.yaml"))

    def test_non_string_value_in_list_fails(self):
        doc = _valid_record()
        doc["content"]["values"] = [42]
        with self.assertRaises(ValueError):
            te.validate_targeting_record(doc, Path("x.yaml"))

    def test_missing_source_fails(self):
        doc = _valid_record()
        del doc["income"]["source"]
        with self.assertRaises(ValueError):
            te.validate_targeting_record(doc, Path("x.yaml"))

    def test_axis_not_a_dict_fails(self):
        doc = _valid_record()
        doc["demographics"] = "просто строка"
        with self.assertRaises(ValueError):
            te.validate_targeting_record(doc, Path("x.yaml"))


class TestForbiddenTargetingContent(unittest.TestCase):
    def test_reach_word_fails(self):
        doc = _valid_record()
        doc["content"]["values"] = ["большой охват в соцсетях"]
        with self.assertRaises(ValueError) as cm:
            te.validate_targeting_record(doc, Path("x.yaml"))
        self.assertIn("охват", str(cm.exception))

    def test_cpm_fails(self):
        doc = _valid_record()
        doc["content"]["source"] = "оценка CPM по площадке"
        with self.assertRaises(ValueError):
            te.validate_targeting_record(doc, Path("x.yaml"))

    def test_million_word_fails(self):
        doc = _valid_record()
        doc["purchases"]["values"] = ["покупает у миллион продавцов"]
        with self.assertRaises(ValueError):
            te.validate_targeting_record(doc, Path("x.yaml"))

    def test_audience_size_combo_fails(self):
        doc = _valid_record()
        doc["content"]["values"] = ["размер аудитории площадки большой"]
        with self.assertRaises(ValueError) as cm:
            te.validate_targeting_record(doc, Path("x.yaml"))
        self.assertIn("аудитор", str(cm.exception).lower())

    def test_package_size_in_purchases_is_not_false_flagged(self):
        """Контрольный тест: «размер» без «аудитор» рядом — легитимное описание
        товара (упаковки), НЕ аудитории — не должно ловиться."""
        doc = _valid_record()
        doc["purchases"]["values"] = ["предпочитают средний размер упаковки"]
        te.validate_targeting_record(doc, Path("x.yaml"))  # не бросает

    def test_share_word_dolya_rynka_fails(self):
        doc = _valid_record()
        doc["income"]["values"] = ["доля аудитории с высоким доходом"]
        with self.assertRaises(ValueError):
            te.validate_targeting_record(doc, Path("x.yaml"))

    def test_share_word_false_positives_are_avoided(self):
        """«долго»/«должен»/«доллар»/«долина» НЕ являются словоформами «доля» —
        не должны ловиться SHARE_WORD_RE (см. докстринг targeting_export.py)."""
        doc = _valid_record()
        doc["content"]["values"] = ["должен искать в долларах цену", "читает про Силиконовую долину", "долго выбирает"]
        te.validate_targeting_record(doc, Path("x.yaml"))  # не бросает

    def test_percent_sign_fails(self):
        doc = _valid_record()
        doc["purchases"]["values"] = ["30% покупок в аптеке"]
        with self.assertRaises(ValueError):
            te.validate_targeting_record(doc, Path("x.yaml"))

    def test_percent_word_form_without_sign_fails(self):
        doc = _valid_record()
        doc["purchases"]["values"] = ["тридцать процентов покупок в аптеке"]
        with self.assertRaises(ValueError):
            te.validate_targeting_record(doc, Path("x.yaml"))


# ----------------------------------------------------------------------------
# «Правило для gender» (check_gender_honesty)
# ----------------------------------------------------------------------------


class TestGenderHonesty(unittest.TestCase):
    def test_segment_with_gender_any_demographics_passes(self):
        doc = _valid_record()
        segment_doc = {"persona_jitter": {"age": [20, 38], "gender": ["ж", "м"]}}
        te.check_gender_honesty(doc, segment_doc, Path("x.yaml"))  # не бросает

    def test_segment_without_gender_missing_utochnit_fails(self):
        doc = _valid_record()
        doc["demographics"] = {"values": ["преимущественно женщины"], "trust": "🟢", "source": "предположение"}
        segment_doc = {"persona_jitter": {"age": [20, 38]}}  # без gender
        with self.assertRaises(ValueError) as cm:
            te.check_gender_honesty(doc, segment_doc, Path("x.yaml"))
        self.assertIn("уточнить", str(cm.exception))

    def test_segment_without_gender_with_utochnit_but_wrong_trust_fails(self):
        doc = _valid_record()
        doc["demographics"] = {"values": ["пол: уточнить; 20-38 лет"], "trust": "🟢", "source": "persona_jitter.age"}
        segment_doc = {"persona_jitter": {"age": [20, 38]}}
        with self.assertRaises(ValueError) as cm:
            te.check_gender_honesty(doc, segment_doc, Path("x.yaml"))
        self.assertIn("🔴", str(cm.exception))

    def test_segment_without_gender_with_utochnit_and_red_trust_passes(self):
        doc = _valid_record()
        doc["demographics"] = {"values": ["пол: уточнить; 20-38 лет"], "trust": "🔴", "source": "persona_jitter.age"}
        segment_doc = {"persona_jitter": {"age": [20, 38]}}
        te.check_gender_honesty(doc, segment_doc, Path("x.yaml"))  # не бросает

    def test_segment_without_persona_jitter_at_all_is_treated_as_no_gender(self):
        doc = _valid_record()
        doc["demographics"] = {"values": ["пол: уточнить"], "trust": "🔴", "source": "нет данных"}
        te.check_gender_honesty(doc, {}, Path("x.yaml"))  # не бросает (пустой сегмент -> нет gender -> честно)


# ----------------------------------------------------------------------------
# Сборка таблицы/матрицы
# ----------------------------------------------------------------------------


class TestRenderOutputs(unittest.TestCase):
    def _record(self, segment_id="seg1", segment_name="Тестовый") -> te.SegmentTargeting:
        return te.SegmentTargeting(
            segment_id=segment_id,
            segment_name=segment_name,
            source_path=Path(f"05_targeting_{segment_id}.yaml"),
            demographics={"values": ["ж, м, 20-38"], "trust": "🟢", "source": "s"},
            income={"values": ["средний"], "trust": "🟢", "source": "s"},
            content={"values": ["тема1", "тема2"], "trust": "🟡", "source": "s"},
            purchases={"values": ["категория1"], "trust": "🟡", "source": "s"},
        )

    def test_markdown_table_header_matches_report_template_contract(self):
        table = te.render_markdown_table([self._record()])
        self.assertIn("| Сегмент | Демография 🟢 | Доход 🟢 | Контент 🟡 | Покупки 🟡 |", table)

    def test_markdown_table_joins_multiple_values_with_semicolon(self):
        table = te.render_markdown_table([self._record()])
        self.assertIn("тема1; тема2", table)

    def test_markdown_table_row_includes_name_and_id(self):
        table = te.render_markdown_table([self._record(segment_id="seg1", segment_name="Тестовый")])
        self.assertIn("Тестовый (`seg1`)", table)

    def test_targeting_matrix_structure(self):
        matrix = te.build_targeting_matrix([self._record()], "cjm_demo_20260101-0000")
        self.assertEqual(matrix["cjm_run"], "cjm_demo_20260101-0000")
        self.assertEqual(len(matrix["segments"]), 1)
        seg = matrix["segments"][0]
        self.assertEqual(seg["segment_id"], "seg1")
        self.assertEqual(seg["demographics"]["values"], ["ж, м, 20-38"])


# ----------------------------------------------------------------------------
# Полный пайплайн на файловой системе (build_segment_index/resolve/load)
# ----------------------------------------------------------------------------


class TestFullPipelineOnDisk(unittest.TestCase):
    def test_missing_segment_file_raises(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            segments_root = tmp / "panel" / "segments"
            segments_root.mkdir(parents=True)
            index = te.build_segment_index(segments_root)
            with self.assertRaises(ValueError) as cm:
                te.resolve_segment_yaml("ghost_segment", index, segments_root)
            self.assertIn("ghost_segment", str(cm.exception))

    def test_conflicting_segment_id_raises(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            segments_root = tmp / "panel" / "segments"
            (segments_root / "cat_a").mkdir(parents=True)
            (segments_root / "cat_b").mkdir(parents=True)
            (segments_root / "cat_a" / "dup.yaml").write_text("id: dup\nname: A\n", encoding="utf-8")
            (segments_root / "cat_b" / "dup.yaml").write_text("id: dup\nname: B\n", encoding="utf-8")
            index = te.build_segment_index(segments_root)
            with self.assertRaises(ValueError) as cm:
                te.resolve_segment_yaml("dup", index, segments_root)
            self.assertIn("конфликт", str(cm.exception).lower())

    def test_end_to_end_load_segment_targeting_with_gender_missing(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            segments_root = tmp / "panel" / "segments" / "demo"
            segments_root.mkdir(parents=True)
            _write_yaml(
                segments_root / "seg_no_gender.yaml",
                {"id": "seg_no_gender", "name": "Без пола", "persona_jitter": {"age": [25, 40]}},
            )
            targeting_path = tmp / "05_targeting_seg_no_gender.yaml"
            _write_yaml(
                targeting_path,
                {
                    "segment_id": "seg_no_gender",
                    "demographics": {"values": ["пол: уточнить; 25-40 лет"], "trust": "🔴", "source": "age"},
                    "income": {"values": ["средний"], "trust": "🟢", "source": "income_level"},
                    "content": {"values": ["форумы"], "trust": "🟡", "source": "cjm"},
                    "purchases": {"values": ["аптека"], "trust": "🟡", "source": "cjm"},
                },
            )
            index = te.build_segment_index(segments_root)
            record = te.load_segment_targeting(targeting_path, segments_root, index)
            self.assertEqual(record.segment_id, "seg_no_gender")
            self.assertEqual(record.segment_name, "Без пола")

    def test_end_to_end_fabricated_gender_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            segments_root = tmp / "panel" / "segments" / "demo"
            segments_root.mkdir(parents=True)
            _write_yaml(
                segments_root / "seg_no_gender.yaml",
                {"id": "seg_no_gender", "name": "Без пола", "persona_jitter": {"age": [25, 40]}},
            )
            targeting_path = tmp / "05_targeting_seg_no_gender.yaml"
            _write_yaml(
                targeting_path,
                {
                    "segment_id": "seg_no_gender",
                    "demographics": {"values": ["преимущественно женщины 25-40"], "trust": "🟢", "source": "предположение"},
                    "income": {"values": ["средний"], "trust": "🟢", "source": "income_level"},
                    "content": {"values": ["форумы"], "trust": "🟡", "source": "cjm"},
                    "purchases": {"values": ["аптека"], "trust": "🟡", "source": "cjm"},
                },
            )
            index = te.build_segment_index(segments_root)
            with self.assertRaises(ValueError) as cm:
                te.load_segment_targeting(targeting_path, segments_root, index)
            self.assertIn("выдуманные", str(cm.exception))


# ----------------------------------------------------------------------------
# CLI end-to-end (subprocess)
# ----------------------------------------------------------------------------


class TestCliEndToEnd(unittest.TestCase):
    def _make_fixture(self, tmp: Path) -> tuple[Path, Path]:
        segments_root = tmp / "panel" / "segments" / "demo"
        segments_root.mkdir(parents=True)
        _write_yaml(
            segments_root / "seg1.yaml",
            {"id": "seg1", "name": "Сегмент 1", "persona_jitter": {"age": [20, 38], "gender": ["ж", "м"]}},
        )
        run_dir = tmp / "run"
        run_dir.mkdir()
        _write_yaml(run_dir / "05_targeting_seg1.yaml", _valid_record(segment_id="seg1"))
        return run_dir, segments_root

    def test_happy_path_creates_table_and_matrix_exit_zero(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run_dir, segments_root = self._make_fixture(tmp)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(TARGETING_EXPORT_PATH),
                    "--run",
                    str(run_dir),
                    "--segments-root",
                    str(segments_root),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertTrue((run_dir / "05_targeting_table.md").exists())
            self.assertTrue((run_dir / "targeting_matrix.yaml").exists())
            table_text = (run_dir / "05_targeting_table.md").read_text(encoding="utf-8")
            self.assertIn("Демография 🟢", table_text)

    def test_broken_schema_exits_one(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run_dir, segments_root = self._make_fixture(tmp)
            bad = _valid_record(segment_id="seg1")
            del bad["income"]
            _write_yaml(run_dir / "05_targeting_seg1.yaml", bad)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(TARGETING_EXPORT_PATH),
                    "--run",
                    str(run_dir),
                    "--segments-root",
                    str(segments_root),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("income", proc.stderr)

    def test_no_targeting_files_found_exits_one(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run_dir = tmp / "run"
            run_dir.mkdir()
            proc = subprocess.run(
                [sys.executable, str(TARGETING_EXPORT_PATH), "--run", str(run_dir)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)

    def test_missing_run_dir_exits_one(self):
        proc = subprocess.run(
            [sys.executable, str(TARGETING_EXPORT_PATH), "--run", "/nonexistent/run/dir"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)

    def test_no_arguments_exits_one(self):
        proc = subprocess.run(
            [sys.executable, str(TARGETING_EXPORT_PATH)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

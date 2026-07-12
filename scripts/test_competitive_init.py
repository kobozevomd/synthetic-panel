#!/usr/bin/env python3
"""
test_competitive_init.py — юнит-тесты scripts/competitive_init.py
(spec_synthetic-panel_v1.2.md §Модуль 3, задание [B2] п.3/п.5: "минимум:
happy path + 3 отказа").

Запуск:
    python scripts/test_competitive_init.py
    (или: python -m unittest scripts.test_competitive_init -v из корня скилла)

Покрытие (превышает минимум задания — happy path + 6 отказов):
    - happy path: валидный studies/comp_<имя>.yaml -> exit 0, manifest.json
      создан с ожидаемыми полями, план стадий C0-C4 напечатан с ожидаемыми
      именами файлов (00_brand_knowledge.*, 01_perception_<id>.md, ...);
    - отказ 1: `type` != competitive_positioning;
    - отказ 2: `segments` содержит id, которого нет в panel/segments/**;
    - отказ 3: `competitors` вне диапазона 3-6;
    - отказ 4: `test_segments` НЕ подмножество `segments`;
    - отказ 5: отсутствует обязательное скалярное поле;
    - отказ 6: data_inputs.* путь не существует на диске.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import competitive_init as ci  # noqa: E402

COMPETITIVE_INIT_PATH = _SCRIPTS_DIR / "competitive_init.py"

MINIMAL_CONFIG_YAML = "llm:\n  provider: agent\n  model: claude-sonnet-5\n"


def _write_yaml(path: Path, doc: dict) -> Path:
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _valid_study(**overrides) -> dict:
    doc = {
        "name": "comp_test_demo",
        "type": "competitive_positioning",
        "category": "средства от акне",
        "our_brand": "Азелик",
        "competitors": ["Скинорен", "Базирон АС", "Дифферин"],
        "segments": ["seg_a", "seg_b"],
        "territories_hint": None,
        "messages_per_territory": 4,
        "test_segments": ["seg_a", "seg_b"],
        "data_inputs": {"brand_cards": None, "social_listening": None},
    }
    doc.update(overrides)
    return doc


class _FixtureMixin:
    def make_fixture(self, tmp: Path) -> tuple[Path, Path, Path]:
        """Возвращает (study_path, segments_root, config_path) с 2 существующими сегментами."""
        segments_root = tmp / "panel" / "segments" / "demo"
        segments_root.mkdir(parents=True)
        _write_yaml(segments_root / "seg_a.yaml", {"id": "seg_a", "name": "Сегмент А"})
        _write_yaml(segments_root / "seg_b.yaml", {"id": "seg_b", "name": "Сегмент Б"})

        study_path = tmp / "comp_test_demo.yaml"
        _write_yaml(study_path, _valid_study())

        config_path = tmp / "config.yaml"
        config_path.write_text(MINIMAL_CONFIG_YAML, encoding="utf-8")

        return study_path, segments_root, config_path

    def run_cli(self, study_path: Path, segments_root: Path, config_path: Path, run_dir: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                str(COMPETITIVE_INIT_PATH),
                "--study",
                str(study_path),
                "--segments-root",
                str(segments_root),
                "--config",
                str(config_path),
                "--run-dir",
                str(run_dir),
            ],
            capture_output=True,
            text=True,
        )


# ----------------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------------


class TestHappyPath(_FixtureMixin, unittest.TestCase):
    def test_valid_study_exits_zero_and_creates_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            study_path, segments_root, config_path = self.make_fixture(tmp)
            run_dir = tmp / "runs" / "comp_test_demo_20260101-0000"
            proc = self.run_cli(study_path, segments_root, config_path, run_dir)
            self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("OK", proc.stdout)

            manifest_path = run_dir / "manifest.json"
            self.assertTrue(manifest_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["study_type"], "competitive_positioning")
            self.assertEqual(manifest["our_brand"], "Азелик")
            self.assertEqual(manifest["competitors"], ["Скинорен", "Базирон АС", "Дифферин"])
            self.assertEqual(manifest["segments"], ["seg_a", "seg_b"])
            self.assertEqual(manifest["test_segments"], ["seg_a", "seg_b"])
            self.assertEqual(manifest["comp_spec_version"], "1.2")
            self.assertIn("stages", manifest)

    def test_stage_plan_mentions_expected_filenames(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            study_path, segments_root, config_path = self.make_fixture(tmp)
            run_dir = tmp / "runs" / "comp_test_demo_20260101-0000"
            proc = self.run_cli(study_path, segments_root, config_path, run_dir)
            self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")
            for expected in (
                "00_brand_knowledge.md",
                "00_brand_knowledge.yaml",
                "01_perception_seg_a.md",
                "01_perception_seg_b.md",
                "02_territory_map.md",
                "03_switch_barriers.md",
                "04_messages.yaml",
                "comp_to_study.py",
                "comp_report.md",
            ):
                self.assertIn(expected, proc.stdout, f"план стадий должен упоминать {expected!r}")

    def test_rerun_reuses_existing_manifest_stages(self):
        """Повторный вызов на тот же run_dir не должен терять уже записанные stages
        (симметрично cjm_init.py::load_or_init_manifest)."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            study_path, segments_root, config_path = self.make_fixture(tmp)
            run_dir = tmp / "runs" / "comp_test_demo_20260101-0000"
            self.run_cli(study_path, segments_root, config_path, run_dir)
            manifest_path = run_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["stages"]["c0_done"] = {"note": "проба знания брендов завершена"}
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

            proc = self.run_cli(study_path, segments_root, config_path, run_dir)
            self.assertEqual(proc.returncode, 0)
            manifest_after = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertIn("c0_done", manifest_after["stages"])


# ----------------------------------------------------------------------------
# Отказы (минимум 3 по заданию — здесь 6)
# ----------------------------------------------------------------------------


class TestRefusals(_FixtureMixin, unittest.TestCase):
    def test_wrong_type_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            study_path, segments_root, config_path = self.make_fixture(tmp)
            _write_yaml(study_path, _valid_study(type="segment_map"))
            run_dir = tmp / "runs" / "x"
            proc = self.run_cli(study_path, segments_root, config_path, run_dir)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("competitive_positioning", proc.stderr)
            self.assertFalse((run_dir / "manifest.json").exists())

    def test_unknown_segment_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            study_path, segments_root, config_path = self.make_fixture(tmp)
            _write_yaml(study_path, _valid_study(segments=["seg_a", "ghost_segment"], test_segments=["seg_a"]))
            run_dir = tmp / "runs" / "x"
            proc = self.run_cli(study_path, segments_root, config_path, run_dir)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("ghost_segment", proc.stderr)

    def test_competitors_out_of_range_too_few_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            study_path, segments_root, config_path = self.make_fixture(tmp)
            _write_yaml(study_path, _valid_study(competitors=["Скинорен", "Базирон АС"]))
            run_dir = tmp / "runs" / "x"
            proc = self.run_cli(study_path, segments_root, config_path, run_dir)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("competitors", proc.stderr)

    def test_competitors_out_of_range_too_many_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            study_path, segments_root, config_path = self.make_fixture(tmp)
            _write_yaml(study_path, _valid_study(competitors=[f"Бренд{i}" for i in range(7)]))
            run_dir = tmp / "runs" / "x"
            proc = self.run_cli(study_path, segments_root, config_path, run_dir)
            self.assertEqual(proc.returncode, 1)

    def test_test_segments_not_subset_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            study_path, segments_root, config_path = self.make_fixture(tmp)
            _write_yaml(study_path, _valid_study(test_segments=["seg_a", "seg_not_in_segments"]))
            run_dir = tmp / "runs" / "x"
            proc = self.run_cli(study_path, segments_root, config_path, run_dir)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("seg_not_in_segments", proc.stderr)

    def test_missing_required_field_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            study_path, segments_root, config_path = self.make_fixture(tmp)
            doc = _valid_study()
            del doc["our_brand"]
            _write_yaml(study_path, doc)
            run_dir = tmp / "runs" / "x"
            proc = self.run_cli(study_path, segments_root, config_path, run_dir)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("our_brand", proc.stderr)

    def test_bad_data_input_path_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            study_path, segments_root, config_path = self.make_fixture(tmp)
            _write_yaml(
                study_path,
                _valid_study(data_inputs={"brand_cards": "/nonexistent/brand_cards.yaml", "social_listening": None}),
            )
            run_dir = tmp / "runs" / "x"
            proc = self.run_cli(study_path, segments_root, config_path, run_dir)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("data_inputs.brand_cards", proc.stderr)

    def test_missing_study_file_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _, segments_root, config_path = self.make_fixture(tmp)
            run_dir = tmp / "runs" / "x"
            proc = self.run_cli(tmp / "does_not_exist.yaml", segments_root, config_path, run_dir)
            self.assertEqual(proc.returncode, 1)


# ----------------------------------------------------------------------------
# Валидация на уровне функций (быстрее, без subprocess)
# ----------------------------------------------------------------------------


class TestValidateTestSegmentsSubsetUnit(unittest.TestCase):
    def test_subset_passes(self):
        study = _valid_study(segments=["a", "b", "c"], test_segments=["a", "b"])
        ci.validate_test_segments_subset(study, Path("x.yaml"))  # не бросает SystemExit


if __name__ == "__main__":
    unittest.main(verbosity=2)

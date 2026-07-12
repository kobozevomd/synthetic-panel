#!/usr/bin/env python3
"""
test_comp_to_study.py — юнит-тесты scripts/comp_to_study.py (мост стадии C4
режима competitive_positioning -> studies/*.yaml, spec_synthetic-panel_v1.2.md
§Модуль 3 п.2, задание [B2] п.4).

Запуск:
    python scripts/test_comp_to_study.py
    (или: python -m unittest scripts.test_comp_to_study -v из корня скилла)

Покрытие:
    - извлечение/форматирование метаданных сообщений (territory/
      targeted_competitor/targeted_barrier/rtb_type) — с метаданными и без;
    - валидация сегментов против manifest["test_segments"] (НЕ against
      manifest["segments"]) — проходит на подмножестве, отказывает на чужом id;
    - build_studies: форма А (shared, `messages`+`segments`) и форма Б
      (`by_segment`) — happy path; обе формы сразу -> отказ; ни одной -> отказ;
    - CLI end-to-end (subprocess): полный цикл на фикстуре, имитирующей
      manifest.json от competitive_init.py + 04_messages.yaml обеих форм;
      отсутствующий manifest.json/04_messages.yaml -> exit 1; сегмент вне
      test_segments -> exit 1.
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

import comp_to_study as cts  # noqa: E402

COMP_TO_STUDY_PATH = _SCRIPTS_DIR / "comp_to_study.py"


def _write_yaml(path: Path, doc: dict) -> Path:
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _fake_manifest(**overrides) -> dict:
    doc = {
        "comp_spec_version": "1.2",
        "study_name": "comp_test_demo",
        "study_type": "competitive_positioning",
        "our_brand": "Азелик",
        "competitors": ["Скинорен", "Базирон АС", "Дифферин"],
        "segments": ["seg_a", "seg_b"],
        "test_segments": ["seg_a", "seg_b"],
        "stages": {},
    }
    doc.update(overrides)
    return doc


# ----------------------------------------------------------------------------
# Метаданные сообщений
# ----------------------------------------------------------------------------


class TestExtractMetadata(unittest.TestCase):
    def test_metadata_extracted_by_position(self):
        raw = [
            {"id": "msg1", "text": "текст 1", "territory": "territory_1", "targeted_competitor": "Скинорен"},
            {"id": "msg2", "text": "текст 2"},
        ]
        meta = cts.extract_comp_metadata(raw, ["msg1", "msg2"])
        self.assertEqual(meta[0]["territory"], "territory_1")
        self.assertEqual(meta[0]["targeted_competitor"], "Скинорен")
        self.assertIsNone(meta[1]["territory"])

    def test_format_note_without_any_metadata_says_dash_fallback(self):
        meta = [{"id": "msg1", "territory": None, "targeted_competitor": None, "targeted_barrier": None, "rtb_type": None}]
        note = cts.format_comp_metadata_note(meta)
        self.assertIn("не указаны", note)

    def test_format_note_with_metadata_lists_each_message(self):
        meta = [
            {"id": "msg1", "territory": "territory_1", "targeted_competitor": "Скинорен", "targeted_barrier": "цена", "rtb_type": "механизм"}
        ]
        note = cts.format_comp_metadata_note(meta)
        self.assertIn("msg1", note)
        self.assertIn("territory_1", note)
        self.assertIn("Скинорен", note)


# ----------------------------------------------------------------------------
# Валидация сегментов против manifest["test_segments"]
# ----------------------------------------------------------------------------


class TestValidateSegmentsAgainstManifest(unittest.TestCase):
    def test_subset_of_test_segments_passes(self):
        manifest = _fake_manifest(test_segments=["seg_a", "seg_b"])
        cts.validate_segments_against_manifest(["seg_a"], manifest, Path("x.yaml"), Path("run"))  # не бросает

    def test_segment_not_in_test_segments_fails(self):
        manifest = _fake_manifest(test_segments=["seg_a"])
        with self.assertRaises(SystemExit):
            cts.validate_segments_against_manifest(["seg_a", "seg_b"], manifest, Path("x.yaml"), Path("run"))

    def test_manifest_without_test_segments_fails(self):
        manifest = _fake_manifest(test_segments=[])
        with self.assertRaises(SystemExit):
            cts.validate_segments_against_manifest(["seg_a"], manifest, Path("x.yaml"), Path("run"))


# ----------------------------------------------------------------------------
# build_studies — обе формы
# ----------------------------------------------------------------------------


class TestBuildStudiesSharedForm(unittest.TestCase):
    def test_shared_form_happy_path(self):
        manifest = _fake_manifest()
        messages_data = {
            "segments": ["seg_a", "seg_b"],
            "messages": [
                {"id": "msg1", "text": "Сообщение 1", "territory": "territory_1"},
                {"id": "msg2", "text": "Сообщение 2", "territory": "territory_1"},
            ],
        }
        studies = cts.build_studies(messages_data, "comp_test_demo", manifest, Path("04_messages.yaml"), Path("run"), 10, 2, "purchase_intent")
        self.assertEqual(len(studies), 1)
        self.assertEqual(studies[0]["name"], "comp_test_demo_c4")
        self.assertEqual(studies[0]["type"], "claims_ranking")
        self.assertEqual(studies[0]["segments"], ["seg_a", "seg_b"])
        self.assertEqual(len(studies[0]["stimuli"]), 2)
        self.assertIn("territory_1", studies[0]["notes"])

    def test_shared_form_without_segments_key_fails(self):
        manifest = _fake_manifest()
        messages_data = {"messages": [{"id": "msg1", "text": "т1"}, {"id": "msg2", "text": "т2"}]}
        with self.assertRaises(SystemExit):
            cts.build_studies(messages_data, "comp_test_demo", manifest, Path("x.yaml"), Path("run"), 10, 2, "purchase_intent")

    def test_shared_form_with_unknown_segment_fails(self):
        manifest = _fake_manifest(test_segments=["seg_a"])
        messages_data = {
            "segments": ["seg_a", "seg_unknown"],
            "messages": [{"id": "msg1", "text": "т1"}, {"id": "msg2", "text": "т2"}],
        }
        with self.assertRaises(SystemExit):
            cts.build_studies(messages_data, "comp_test_demo", manifest, Path("x.yaml"), Path("run"), 10, 2, "purchase_intent")


class TestBuildStudiesBySegmentForm(unittest.TestCase):
    def test_by_segment_form_happy_path(self):
        manifest = _fake_manifest()
        messages_data = {
            "by_segment": {
                "seg_a": [{"id": "msg1", "text": "Для А 1"}, {"id": "msg2", "text": "Для А 2"}],
                "seg_b": [{"id": "msg1", "text": "Для Б 1"}, {"id": "msg2", "text": "Для Б 2"}],
            }
        }
        studies = cts.build_studies(messages_data, "comp_test_demo", manifest, Path("x.yaml"), Path("run"), 10, 2, "appeal")
        self.assertEqual(len(studies), 2)
        names = {s["name"] for s in studies}
        self.assertEqual(names, {"comp_test_demo_c4_seg_a", "comp_test_demo_c4_seg_b"})
        for s in studies:
            self.assertEqual(s["question_scale"], "appeal")
            self.assertEqual(len(s["segments"]), 1)


class TestBuildStudiesFormConflicts(unittest.TestCase):
    def test_both_forms_present_fails(self):
        manifest = _fake_manifest()
        messages_data = {
            "segments": ["seg_a"],
            "messages": [{"id": "msg1", "text": "т1"}, {"id": "msg2", "text": "т2"}],
            "by_segment": {"seg_a": [{"id": "msg1", "text": "т1"}, {"id": "msg2", "text": "т2"}]},
        }
        with self.assertRaises(SystemExit):
            cts.build_studies(messages_data, "comp_test_demo", manifest, Path("x.yaml"), Path("run"), 10, 2, "purchase_intent")

    def test_neither_form_present_fails(self):
        manifest = _fake_manifest()
        with self.assertRaises(SystemExit):
            cts.build_studies({}, "comp_test_demo", manifest, Path("x.yaml"), Path("run"), 10, 2, "purchase_intent")

    def test_fewer_than_two_messages_fails(self):
        """Переиспользованный cjm_to_study.normalize_candidates отказывает
        с <2 стимулами — comp_to_study.py должен пробрасывать этот же отказ."""
        manifest = _fake_manifest()
        messages_data = {"segments": ["seg_a"], "messages": [{"id": "msg1", "text": "только один"}]}
        with self.assertRaises(SystemExit):
            cts.build_studies(messages_data, "comp_test_demo", manifest, Path("x.yaml"), Path("run"), 10, 2, "purchase_intent")


# ----------------------------------------------------------------------------
# CLI end-to-end (subprocess)
# ----------------------------------------------------------------------------


class TestCliEndToEnd(unittest.TestCase):
    def _make_run_dir(self, tmp: Path, manifest_overrides: dict | None = None) -> Path:
        run_dir = tmp / "runs" / "comp_test_demo_20260101-0000"
        run_dir.mkdir(parents=True)
        manifest = _fake_manifest(**(manifest_overrides or {}))
        (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return run_dir

    def test_shared_form_cli_happy_path(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run_dir = self._make_run_dir(tmp)
            _write_yaml(
                run_dir / "04_messages.yaml",
                {
                    "segments": ["seg_a", "seg_b"],
                    "messages": [
                        {"id": "msg1", "text": "Сообщение 1", "territory": "territory_1", "targeted_competitor": "Скинорен"},
                        {"id": "msg2", "text": "Сообщение 2", "territory": "territory_1"},
                    ],
                },
            )
            out_dir = tmp / "studies"
            proc = subprocess.run(
                [sys.executable, str(COMP_TO_STUDY_PATH), "--run", str(run_dir), "--out-dir", str(out_dir)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")
            study_path = out_dir / "comp_test_demo_c4.yaml"
            self.assertTrue(study_path.exists())
            study = yaml.safe_load(study_path.read_text(encoding="utf-8"))
            self.assertEqual(study["type"], "claims_ranking")
            self.assertEqual(study["question_scale"], "purchase_intent")
            self.assertEqual(len(study["stimuli"]), 2)

            copy_path = run_dir / "04_messages_study.yaml"
            self.assertTrue(copy_path.exists())

            manifest_after = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("c4_study_generated", manifest_after["stages"])

    def test_by_segment_form_cli_happy_path(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run_dir = self._make_run_dir(tmp)
            _write_yaml(
                run_dir / "04_messages.yaml",
                {
                    "by_segment": {
                        "seg_a": [{"id": "msg1", "text": "Для А 1"}, {"id": "msg2", "text": "Для А 2"}],
                        "seg_b": [{"id": "msg1", "text": "Для Б 1"}, {"id": "msg2", "text": "Для Б 2"}],
                    }
                },
            )
            out_dir = tmp / "studies"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(COMP_TO_STUDY_PATH),
                    "--run",
                    str(run_dir),
                    "--out-dir",
                    str(out_dir),
                    "--question-scale",
                    "appeal",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertTrue((out_dir / "comp_test_demo_c4_seg_a.yaml").exists())
            self.assertTrue((out_dir / "comp_test_demo_c4_seg_b.yaml").exists())
            self.assertTrue((run_dir / "04_messages_study_seg_a.yaml").exists())
            self.assertTrue((run_dir / "04_messages_study_seg_b.yaml").exists())

    def test_missing_manifest_exits_one(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run_dir = tmp / "runs" / "empty_run"
            run_dir.mkdir(parents=True)
            proc = subprocess.run(
                [sys.executable, str(COMP_TO_STUDY_PATH), "--run", str(run_dir)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)

    def test_missing_messages_file_exits_one_with_helpful_message(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run_dir = self._make_run_dir(tmp)
            proc = subprocess.run(
                [sys.executable, str(COMP_TO_STUDY_PATH), "--run", str(run_dir)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("04_messages", proc.stderr)

    def test_segment_outside_test_segments_exits_one(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run_dir = self._make_run_dir(tmp, {"test_segments": ["seg_a"]})
            _write_yaml(
                run_dir / "04_messages.yaml",
                {
                    "segments": ["seg_a", "seg_b"],
                    "messages": [{"id": "msg1", "text": "т1"}, {"id": "msg2", "text": "т2"}],
                },
            )
            proc = subprocess.run(
                [sys.executable, str(COMP_TO_STUDY_PATH), "--run", str(run_dir)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("seg_b", proc.stderr)

    def test_missing_run_dir_exits_one(self):
        proc = subprocess.run(
            [sys.executable, str(COMP_TO_STUDY_PATH), "--run", "/nonexistent/run/dir"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

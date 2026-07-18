#!/usr/bin/env python3
"""
test_report.py — юнит-тесты честной статистики report.py (spec_synthetic-panel_v1.3.md
§1.3 "честная статистика сравнения", фикс Д3, и §1.4 "негативные контроли", фикс Д5).

Требование задания B1: паирный бутстреп ОБЯЗАН быть проверен на фикстуре с ИЗВЕСТНЫМ
ответом — не "выглядит правдоподобно", а результат, посчитанный заранее по построению
данных (общий сдвиг средних, точная симметрия и т.п.), как и в test_ssr.py. Работает
БЕЗ сети/embedding-модели — report.py вообще не импортирует sentence_transformers.

Покрытие:
    - separability_label: три уровня, ровно на границах порогов.
    - build_e_matrix: happy path + явная ошибка на неполных парах респондент×стимул.
    - compute_segment_pairwise_stats: явный разрыв -> "уверенный разрыв"; симметричная
      конструкция (см. test_ssr.py) -> "в пределах шума".
    - split_half_by_samples / check_split_half_stability: сплит невозможен (1 сэмпл)
      -> None; согласие/несогласие половин.
    - check_bootstrap_reseed_stability: стабильный ярлык при явном разрыве.
    - compute_sample_instability: точный подсчёт расхождений по сконструированным данным.
    - compute_reliability_summary: интеграция всех проверок, X/Y корректны.
    - compute_controls_verdict: плацебо внизу/наверху (Д5, известный ответ по рангу);
      ловушка неразличима/явно отличается от оригинала.
    - render_ranking_section/render_appendix_table_section/render_controls_verdict_detail:
      дымовые тесты рендера (нет падения, ожидаемые маркеры присутствуют в тексте).
    - render_report/write_report сквозь РЕАЛЬНЫЙ references/report_template.md +
      disclaimers.md (не мок) — ловит рассинхрон между report.py и живым файлом
      шаблона ([B3]), который юниты на синтетических фикстурах не видят
      (самый ценный тест этого файла — именно он поймал на практике реальный
      баг при интеграции с v1.3-шаблоном: обрезание секции "## Приложение" при
      старой логике сплайса дисклеймеров).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

_SCRIPTS_DIR = Path(__file__).resolve().parent
_SKILL_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import report  # noqa: E402


def _dummy_pmf(e_value: float) -> list[float]:
    """PMF-заглушка, правдоподобная форма вокруг round(e_value) — для тестов
    статистики/агрегации, где ВАЖНО e_value, а не точная форма PMF (та уже
    покрыта test_ssr.py на реальной SSR-математике)."""
    idx = min(4, max(0, round(e_value) - 1))
    pmf = [0.05] * 5
    pmf[idx] += 0.8
    total = sum(pmf)
    return [p / total for p in pmf]


def _resp_rows(segment_id: str, e_by_stimulus: dict[str, list[float]], n_samples: int = 2) -> list[dict]:
    """e_by_stimulus: {stimulus_id: [e_respondent_1, e_respondent_2, ...]} — списки ОДНОЙ
    длины, respondent_idx = позиция в списке + 1 (пары "тот же респондент, разные стимулы")."""
    lengths = {len(v) for v in e_by_stimulus.values()}
    assert len(lengths) == 1, "все списки e_by_stimulus обязаны быть одной длины (paired)"
    rows = []
    for stim_id, values in e_by_stimulus.items():
        for i, e in enumerate(values, start=1):
            rows.append(
                {
                    "segment": segment_id,
                    "stimulus_id": stim_id,
                    "respondent_idx": i,
                    "n_samples": n_samples,
                    "pmf": _dummy_pmf(e),
                    "e_value": e,
                }
            )
    return rows


def _segment_rows(segment_id: str, e_by_stimulus_scalar: dict[str, float], n_respondents: int = 10) -> list[dict]:
    """Строки pmf_by_segment.csv-вида: один E на (segment, stimulus_id)."""
    rows = []
    for stim_id, e in e_by_stimulus_scalar.items():
        rows.append(
            {
                "segment": segment_id,
                "stimulus_id": stim_id,
                "n_respondents": n_respondents,
                "pmf": _dummy_pmf(e),
                "e_value": e,
                "ci_low": max(1.0, e - 0.3),
                "ci_high": min(5.0, e + 0.3),
            }
        )
    return rows


def _sample_rows_from_resp(resp_rows: list[dict], n_samples: int, jitter: float = 0.0, seed: int = 0) -> list[dict]:
    """Разворачивает respondent-level e_value в n_samples sample-level строк
    (опционально с небольшим детерминированным джиттером на сэмпл)."""
    rng = np.random.default_rng(seed)
    rows = []
    for r in resp_rows:
        for sample_idx in range(1, n_samples + 1):
            e = r["e_value"] + (rng.normal(0, jitter) if jitter else 0.0)
            rows.append(
                {
                    "segment": r["segment"],
                    "stimulus_id": r["stimulus_id"],
                    "respondent_idx": r["respondent_idx"],
                    "sample_idx": sample_idx,
                    "pmf": _dummy_pmf(e),
                    "e_value": e,
                }
            )
    return rows


class TestSeparabilityLabel(unittest.TestCase):
    def test_boundaries_exact(self):
        self.assertEqual(report.separability_label(0.9), "уверенный разрыв")
        self.assertEqual(report.separability_label(0.95), "уверенный разрыв")
        self.assertEqual(report.separability_label(1.0), "уверенный разрыв")
        self.assertEqual(report.separability_label(0.89999), "на грани")
        self.assertEqual(report.separability_label(0.7), "на грани")
        self.assertEqual(report.separability_label(0.8), "на грани")
        self.assertEqual(report.separability_label(0.69999), "в пределах шума")
        self.assertEqual(report.separability_label(0.5), "в пределах шума")
        self.assertEqual(report.separability_label(0.0), "в пределах шума")


class TestBuildEMatrix(unittest.TestCase):
    def test_happy_path_shape_and_order(self):
        rows = _resp_rows("seg1", {"A": [4.0, 4.2, 3.9], "B": [3.0, 3.1, 2.9]})
        matrix, respondent_ids = report.build_e_matrix(rows, "seg1", ["A", "B"])
        self.assertEqual(matrix.shape, (3, 2))
        self.assertEqual(respondent_ids, [1, 2, 3])
        np.testing.assert_allclose(matrix[:, 0], [4.0, 4.2, 3.9])
        np.testing.assert_allclose(matrix[:, 1], [3.0, 3.1, 2.9])

    def test_column_order_follows_stimulus_ids_argument(self):
        rows = _resp_rows("seg1", {"A": [4.0], "B": [3.0]})
        matrix, _ = report.build_e_matrix(rows, "seg1", ["B", "A"])
        self.assertEqual(matrix[0, 0], 3.0)
        self.assertEqual(matrix[0, 1], 4.0)

    def test_incomplete_pairing_raises_value_error(self):
        rows = _resp_rows("seg1", {"A": [4.0, 4.2], "B": [3.0, 3.1]})
        # Респондент 2 не ответил на C вовсе (C есть только у респондента 1).
        rows.append(
            {"segment": "seg1", "stimulus_id": "C", "respondent_idx": 1, "n_samples": 2, "pmf": _dummy_pmf(2.0), "e_value": 2.0}
        )
        with self.assertRaises(ValueError):
            report.build_e_matrix(rows, "seg1", ["A", "B", "C"])

    def test_no_data_for_segment_raises(self):
        rows = _resp_rows("seg1", {"A": [4.0]})
        with self.assertRaises(ValueError):
            report.build_e_matrix(rows, "seg_missing", ["A"])


class TestComputeSegmentPairwiseStats(unittest.TestCase):
    def test_clear_gap_gives_confident_separation_and_dominant_place_probability(self):
        rng = np.random.default_rng(0)
        shared = rng.normal(0, 0.2, size=20)
        rows = _resp_rows(
            "seg1",
            {
                "A": list(4.5 + shared),
                "B": list(3.0 + shared),
                "C": list(1.5 + shared),
            },
        )
        stats = report.compute_segment_pairwise_stats(rows, "seg1", ["A", "B", "C"], bootstrap_iters=3000, seed=42)
        labels = {(p.higher_id, p.lower_id): p.label for p in stats["pairwise"]}
        self.assertEqual(labels[("A", "B")], "уверенный разрыв")
        self.assertEqual(labels[("B", "C")], "уверенный разрыв")
        self.assertGreater(stats["place_probabilities"]["A"], 0.95)
        total = sum(stats["place_probabilities"].values())
        self.assertAlmostEqual(total, 1.0, places=6)

    def test_symmetric_construction_gives_within_noise_label(self):
        """c/d конструкция из test_ssr.py (d[i] = 11 - c[i]) -> P(c>d) ~ 0.5 по
        точной симметрии -> ярлык обязан быть "в пределах шума"."""
        c = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        d = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
        rows = _resp_rows("seg1", {"C": [float(x) for x in c], "D": [float(x) for x in d]})
        stats = report.compute_segment_pairwise_stats(rows, "seg1", ["C", "D"], bootstrap_iters=20000, seed=123)
        self.assertEqual(stats["pairwise"][0].label, "в пределах шума")


class TestSplitHalfBySamples(unittest.TestCase):
    def test_single_sample_index_returns_none(self):
        resp_rows = _resp_rows("seg1", {"A": [4.0, 3.0], "B": [3.5, 2.5]})
        sample_rows = _sample_rows_from_resp(resp_rows, n_samples=1)
        self.assertIsNone(report.split_half_by_samples(sample_rows, "seg1"))

    def test_two_sample_indices_split_disjoint(self):
        resp_rows = _resp_rows("seg1", {"A": [4.0, 3.0], "B": [3.5, 2.5]})
        sample_rows = _sample_rows_from_resp(resp_rows, n_samples=2)
        halves = report.split_half_by_samples(sample_rows, "seg1")
        self.assertIsNotNone(halves)
        half_a, half_b = halves
        sample_idx_a = {r["sample_idx"] for r in half_a}
        sample_idx_b = {r["sample_idx"] for r in half_b}
        self.assertEqual(sample_idx_a, {1})
        self.assertEqual(sample_idx_b, {2})
        self.assertEqual(len(half_a) + len(half_b), len(sample_rows))


class TestCheckSplitHalfStability(unittest.TestCase):
    def test_agreeing_halves_return_true(self):
        rng = np.random.default_rng(1)
        shared = rng.normal(0, 0.1, size=10)
        resp_rows = _resp_rows("seg1", {"A": list(4.5 + shared), "B": list(3.0 + shared)})
        sample_rows = _sample_rows_from_resp(resp_rows, n_samples=2, jitter=0.05, seed=1)
        result = report.check_split_half_stability(sample_rows, "seg1", {"A"}, ["A", "B"], top_n=1)
        self.assertTrue(result)

    def test_none_when_split_impossible(self):
        resp_rows = _resp_rows("seg1", {"A": [4.0], "B": [3.0]})
        sample_rows = _sample_rows_from_resp(resp_rows, n_samples=1)
        result = report.check_split_half_stability(sample_rows, "seg1", {"A"}, ["A", "B"], top_n=1)
        self.assertIsNone(result)

    def test_disagreeing_half_returns_false(self):
        # Собираем sample-level строки ВРУЧНУЮ: в половине сэмплов B > A (переворот).
        rows = []
        for respondent_idx in range(1, 6):
            rows.append({"segment": "seg1", "stimulus_id": "A", "respondent_idx": respondent_idx, "sample_idx": 1, "pmf": _dummy_pmf(4.5), "e_value": 4.5})
            rows.append({"segment": "seg1", "stimulus_id": "B", "respondent_idx": respondent_idx, "sample_idx": 1, "pmf": _dummy_pmf(3.0), "e_value": 3.0})
            # sample_idx=2: B обгоняет A - другая "половина" не подтверждает лидера A.
            rows.append({"segment": "seg1", "stimulus_id": "A", "respondent_idx": respondent_idx, "sample_idx": 2, "pmf": _dummy_pmf(2.0), "e_value": 2.0})
            rows.append({"segment": "seg1", "stimulus_id": "B", "respondent_idx": respondent_idx, "sample_idx": 2, "pmf": _dummy_pmf(4.0), "e_value": 4.0})
        result = report.check_split_half_stability(rows, "seg1", {"A"}, ["A", "B"], top_n=1)
        self.assertFalse(result)


class TestCheckBootstrapReseedStability(unittest.TestCase):
    def test_clear_gap_stable_across_reseed(self):
        rng = np.random.default_rng(5)
        shared = rng.normal(0, 0.2, size=20)
        e_matrix = np.stack([4.5 + shared, 3.0 + shared], axis=1)
        stable = report.check_bootstrap_reseed_stability(e_matrix, ["A", "B"], bootstrap_iters=2000, seed=42)
        self.assertTrue(stable)

    def test_single_stimulus_trivially_stable(self):
        e_matrix = np.array([[4.0], [4.1], [3.9]])
        self.assertTrue(report.check_bootstrap_reseed_stability(e_matrix, ["A"], bootstrap_iters=100, seed=1))


class TestComputeSampleInstability(unittest.TestCase):
    def test_exact_count_of_disagreeing_respondents(self):
        rows = []
        # Респонденты 1-3: оба сэмпла согласны, что A лидирует.
        for rid in (1, 2, 3):
            rows.append({"segment": "seg1", "stimulus_id": "A", "respondent_idx": rid, "sample_idx": 1, "pmf": _dummy_pmf(4.5), "e_value": 4.5})
            rows.append({"segment": "seg1", "stimulus_id": "B", "respondent_idx": rid, "sample_idx": 1, "pmf": _dummy_pmf(2.0), "e_value": 2.0})
            rows.append({"segment": "seg1", "stimulus_id": "A", "respondent_idx": rid, "sample_idx": 2, "pmf": _dummy_pmf(4.4), "e_value": 4.4})
            rows.append({"segment": "seg1", "stimulus_id": "B", "respondent_idx": rid, "sample_idx": 2, "pmf": _dummy_pmf(2.1), "e_value": 2.1})
        # Респондент 4: сэмпл 1 -> A лидирует, сэмпл 2 -> B лидирует (расхождение).
        rows.append({"segment": "seg1", "stimulus_id": "A", "respondent_idx": 4, "sample_idx": 1, "pmf": _dummy_pmf(4.5), "e_value": 4.5})
        rows.append({"segment": "seg1", "stimulus_id": "B", "respondent_idx": 4, "sample_idx": 1, "pmf": _dummy_pmf(2.0), "e_value": 2.0})
        rows.append({"segment": "seg1", "stimulus_id": "A", "respondent_idx": 4, "sample_idx": 2, "pmf": _dummy_pmf(1.5), "e_value": 1.5})
        rows.append({"segment": "seg1", "stimulus_id": "B", "respondent_idx": 4, "sample_idx": 2, "pmf": _dummy_pmf(4.8), "e_value": 4.8})

        result = report.compute_sample_instability(rows, "seg1", ["A", "B"])
        self.assertEqual(result, {"n_unstable": 1, "n_total": 4})

    def test_none_when_only_one_sample_index(self):
        rows = [{"segment": "seg1", "stimulus_id": "A", "respondent_idx": 1, "sample_idx": 1, "pmf": _dummy_pmf(4.0), "e_value": 4.0}]
        self.assertIsNone(report.compute_sample_instability(rows, "seg1", ["A"]))


class TestComputeReliabilitySummary(unittest.TestCase):
    def test_counts_applicable_checks_and_builds_summary_text(self):
        rng = np.random.default_rng(9)
        shared = rng.normal(0, 0.1, size=12)
        resp_rows = _resp_rows("seg1", {"A": list(4.5 + shared), "B": list(3.0 + shared)})
        e_matrix, _ = report.build_e_matrix(resp_rows, "seg1", ["A", "B"])
        sample_rows = _sample_rows_from_resp(resp_rows, n_samples=2, jitter=0.02, seed=2)

        result = report.compute_reliability_summary(
            e_matrix=e_matrix,
            stimulus_ids_by_e_desc=["A", "B"],
            sample_rows=sample_rows,
            segment_id="seg1",
            bootstrap_iters=1500,
            seed=42,
            sibling_rankings=[["A", "B"], ["B", "A"]],
        )
        # 4 применимые проверки: split-half + reseed + 2 sibling ranking'а.
        self.assertEqual(result["y"], 4)
        self.assertIn(f"Топ-{result['top_n']}", result["summary_text"])
        self.assertIn("из", result["summary_text"])

    def test_only_reseed_applicable_when_no_split_half_or_siblings(self):
        """
        Бутстреп-reseed ВСЕГДА применим (точечная оценка не зависит от seed — см.
        докстринг check_bootstrap_reseed_stability), поэтому Y никогда не бывает
        РОВНО 0, если e_matrix валидна: минимум эта одна проверка засчитывается.
        Здесь samples_per_respondent=1 (сплит-half недоступен) и sibling'ов нет ->
        Y должен быть РОВНО 1 (только reseed), не 0 и не больше.
        """
        resp_rows = _resp_rows("seg1", {"A": [4.0], "B": [3.0]})
        e_matrix, _ = report.build_e_matrix(resp_rows, "seg1", ["A", "B"])
        sample_rows = _sample_rows_from_resp(resp_rows, n_samples=1)
        result = report.compute_reliability_summary(
            e_matrix=e_matrix,
            stimulus_ids_by_e_desc=["A", "B"],
            sample_rows=sample_rows,
            segment_id="seg1",
            bootstrap_iters=100,
            seed=1,
            sibling_rankings=[],
        )
        self.assertEqual(result["y"], 1)
        self.assertIn("из 1 проверок", result["summary_text"])


class TestTopNSetsAgree(unittest.TestCase):
    def test_agrees_when_same_top_set_different_internal_order(self):
        self.assertTrue(report.top_n_sets_agree(["A", "B", "C"], ["B", "A", "C"], top_n=2))

    def test_disagrees_when_different_top_set(self):
        self.assertFalse(report.top_n_sets_agree(["A", "B", "C"], ["C", "B", "A"], top_n=1))

    def test_mismatched_id_sets_raise(self):
        with self.assertRaises(ValueError):
            report.top_n_sets_agree(["A", "B"], ["A", "C"], top_n=1)


class TestComputeControlsVerdict(unittest.TestCase):
    def _base_manifest(self):
        return {
            "enabled": True,
            "placebo": {"real_id": "__placebo__"},
            "decoy": {"real_id": "__decoy__", "decoy_of": "A"},
        }

    def test_not_applicable_when_controls_disabled(self):
        result = report.compute_controls_verdict(
            all_segment_rows=[], all_resp_rows=[], controls_manifest={"enabled": False},
            segments=["seg1"], bootstrap_iters=100, seed=1,
        )
        self.assertEqual(result, {"applicable": False})

    def test_passes_when_placebo_bottom_and_decoy_indistinguishable(self):
        seg_rows = _segment_rows("seg1", {"A": 4.5, "B": 3.0, "__placebo__": 1.2, "__decoy__": 4.4})
        rng = np.random.default_rng(0)
        shared = rng.normal(0, 0.15, size=10)
        resp_rows = _resp_rows(
            "seg1",
            {
                "A": list(4.5 + shared),
                "B": list(3.0 + shared),
                "__decoy__": list(4.5 + shared),  # неотличима от A (та же общая часть)
                "__placebo__": list(1.2 + shared),
            },
        )
        verdict = report.compute_controls_verdict(
            all_segment_rows=seg_rows,
            all_resp_rows=resp_rows,
            controls_manifest=self._base_manifest(),
            segments=["seg1"],
            bootstrap_iters=3000,
            seed=42,
        )
        self.assertTrue(verdict["applicable"])
        self.assertTrue(verdict["placebo_passed"])
        self.assertTrue(verdict["decoy_passed"])
        self.assertFalse(verdict["controls_failed"])

    def test_fails_when_placebo_scores_at_the_top(self):
        seg_rows = _segment_rows("seg1", {"A": 3.0, "B": 2.0, "__placebo__": 4.8, "__decoy__": 2.9})
        rng = np.random.default_rng(1)
        shared = rng.normal(0, 0.1, size=10)
        resp_rows = _resp_rows(
            "seg1",
            {
                "A": list(3.0 + shared),
                "B": list(2.0 + shared),
                "__decoy__": list(3.0 + shared),
                "__placebo__": list(4.8 + shared),
            },
        )
        verdict = report.compute_controls_verdict(
            all_segment_rows=seg_rows,
            all_resp_rows=resp_rows,
            controls_manifest=self._base_manifest(),
            segments=["seg1"],
            bootstrap_iters=2000,
            seed=42,
        )
        self.assertFalse(verdict["placebo_passed"])
        self.assertTrue(verdict["controls_failed"])

    def test_fails_when_placebo_beats_one_real_stimulus_mid_pack(self):
        """РЕШЕНО [review v1.3, находка №1 CRITICAL]: при n_real=2 старое ранговое
        правило «нижней трети» пропускало плацебо, уверенно обыгравший реальный
        стимул (ранг 3 из 4 формально в «нижней трети»). Новое вероятностное
        правило обязано провалить такой прогон."""
        seg_rows = _segment_rows("seg1", {"A": 4.5, "B": 1.5, "__placebo__": 3.0, "__decoy__": 4.4})
        rng = np.random.default_rng(7)
        shared = rng.normal(0, 0.12, size=10)
        resp_rows = _resp_rows(
            "seg1",
            {
                "A": list(4.5 + shared),
                "B": list(1.5 + shared),
                "__decoy__": list(4.5 + shared),
                "__placebo__": list(3.0 + shared),  # уверенно выше реального B
            },
        )
        verdict = report.compute_controls_verdict(
            all_segment_rows=seg_rows,
            all_resp_rows=resp_rows,
            controls_manifest=self._base_manifest(),
            segments=["seg1"],
            bootstrap_iters=3000,
            seed=42,
        )
        self.assertFalse(verdict["placebo_passed"])
        self.assertTrue(verdict["controls_failed"])
        detail = verdict["per_segment"][0]
        self.assertIn("B", detail["placebo_beats"])
        self.assertNotIn("A", detail["placebo_beats"])

    def test_fails_when_decoy_clearly_differs_from_original(self):
        seg_rows = _segment_rows("seg1", {"A": 4.5, "B": 3.0, "__placebo__": 1.0, "__decoy__": 1.5})
        rng = np.random.default_rng(2)
        shared_a = rng.normal(0, 0.1, size=10)
        resp_rows = _resp_rows(
            "seg1",
            {
                "A": list(4.5 + shared_a),
                "B": list(3.0 + shared_a),
                "__placebo__": list(1.0 + shared_a),
                # Ловушка НЕ разделяет общий шум оригинала и оценена намного ниже —
                # явно отличима, а не "в пределах шума".
                "__decoy__": list(1.5 + rng.normal(0, 0.1, size=10)),
            },
        )
        verdict = report.compute_controls_verdict(
            all_segment_rows=seg_rows,
            all_resp_rows=resp_rows,
            controls_manifest=self._base_manifest(),
            segments=["seg1"],
            bootstrap_iters=2000,
            seed=42,
        )
        self.assertFalse(verdict["decoy_passed"])
        self.assertTrue(verdict["controls_failed"])


class TestRenderSmoke(unittest.TestCase):
    """
    Дымовые тесты: рендер не падает и содержит ожидаемые контрактные маркеры.

    v1.3 (после согласования с report_template.md, см. run_study.py::run_report_stage):
    render_ranking_section — ТОЛЬКО клиентский слой (ярлыки + устойчивость), БЕЗ
    вердикта контролей (тот теперь в {{CONTROLS_STATUS_LINE}}/{{CONTROLS_FAILED_BANNER}}
    шаблона, считают run_study.py::compute_controls_status_line/
    compute_controls_failed_banner — см. test_run_study_controls.py); сырые
    E/CI/PMF/P(A>B) и построчная детализация контролей — в
    render_appendix_table_section/render_controls_verdict_detail.
    """

    def _study_and_segments(self):
        study = {
            "stimuli": [{"id": "A", "text": "Стимул А"}, {"id": "B", "text": "Стимул Б"}],
            "segments": ["seg1"],
        }
        segments = {"seg1": {"name": "Сегмент 1"}}
        return study, segments

    def test_ranking_section_renders_client_layer_only(self):
        rng = np.random.default_rng(0)
        shared = rng.normal(0, 0.15, size=10)
        resp_rows = _resp_rows("seg1", {"A": list(4.5 + shared), "B": list(3.0 + shared)})
        seg_rows = _segment_rows("seg1", {"A": 4.5, "B": 3.0})
        sample_rows = _sample_rows_from_resp(resp_rows, n_samples=2, jitter=0.05, seed=1)
        study, segments = self._study_and_segments()

        md = report.render_ranking_section(
            seg_rows, resp_rows, sample_rows, study, segments, "Готовность купить",
            "purchase_intent", 1500, 42,
        )
        self.assertIn("Сегмент: Сегмент 1", md)
        self.assertIn("уверенный разрыв", md)
        self.assertIn("Топ-", md)
        self.assertIn("Внутрипрогонная устойчивость сэмплов:", md)
        # Клиентский слой НЕ содержит сырых E/CI/95%/PMF-цифр (те - в приложении).
        self.assertNotIn("95% CI", md)
        self.assertNotIn("E[шкала]", md)

    def test_appendix_table_section_has_raw_numbers(self):
        rng = np.random.default_rng(0)
        shared = rng.normal(0, 0.15, size=10)
        resp_rows = _resp_rows("seg1", {"A": list(4.5 + shared), "B": list(3.0 + shared)})
        seg_rows = _segment_rows("seg1", {"A": 4.5, "B": 3.0})
        study, segments = self._study_and_segments()

        md = report.render_appendix_table_section(seg_rows, resp_rows, study, segments, 1500, 42)
        self.assertIn("Сегмент: Сегмент 1", md)
        self.assertIn("E[шкала]", md)
        self.assertIn("уверенный разрыв", md)
        # Сырые числа (в отличие от клиентского слоя render_ranking_section) -
        # E-значение и P(A>B) реально присутствуют как данные строки таблицы.
        self.assertIn("4.50", md)
        self.assertIn("1.00", md)

    def test_appendix_table_section_appends_controls_detail_when_applicable(self):
        rng = np.random.default_rng(0)
        shared = rng.normal(0, 0.15, size=10)
        resp_rows = _resp_rows("seg1", {"A": list(4.5 + shared), "B": list(3.0 + shared)})
        seg_rows = _segment_rows("seg1", {"A": 4.5, "B": 3.0})
        study, segments = self._study_and_segments()

        failed_verdict = {
            "applicable": True,
            "controls_failed": True,
            "decoy_of": "A",
            "per_segment": [
                {
                    "segment": "seg1",
                    "placebo_rank": 1,
                    "placebo_n_total": 4,
                    "placebo_ok": False,
                    "decoy_label": "уверенный разрыв",
                    "decoy_ok": False,
                }
            ],
        }
        md = report.render_appendix_table_section(
            seg_rows, resp_rows, study, segments, 1500, 42, failed_verdict
        )
        self.assertIn("прогон не прошёл самоконтроль", md)

    def test_appendix_table_section_omits_controls_detail_when_not_applicable(self):
        rng = np.random.default_rng(0)
        shared = rng.normal(0, 0.15, size=10)
        resp_rows = _resp_rows("seg1", {"A": list(4.5 + shared), "B": list(3.0 + shared)})
        seg_rows = _segment_rows("seg1", {"A": 4.5, "B": 3.0})
        study, segments = self._study_and_segments()

        md = report.render_appendix_table_section(
            seg_rows, resp_rows, study, segments, 1500, 42, {"applicable": False}
        )
        self.assertNotIn("Детализация самоконтроля", md)

    def test_render_controls_verdict_detail_passed(self):
        verdict = {
            "applicable": True,
            "controls_failed": False,
            "decoy_of": "A",
            "per_segment": [
                {
                    "segment": "seg1",
                    "placebo_rank": 4,
                    "placebo_n_total": 4,
                    "placebo_ok": True,
                    "decoy_label": "в пределах шума",
                    "decoy_ok": True,
                }
            ],
        }
        note = report.render_controls_verdict_detail(verdict)
        self.assertIn("пройден", note)
        self.assertNotIn("не прошёл самоконтроль", note)

    def test_render_controls_verdict_detail_failed(self):
        verdict = {
            "applicable": True,
            "controls_failed": True,
            "decoy_of": "A",
            "per_segment": [
                {
                    "segment": "seg1",
                    "placebo_rank": 1,
                    "placebo_n_total": 4,
                    "placebo_ok": False,
                    "decoy_label": "уверенный разрыв",
                    "decoy_ok": False,
                }
            ],
        }
        note = report.render_controls_verdict_detail(verdict)
        self.assertIn("прогон не прошёл самоконтроль", note)
        self.assertIn("ПРОВАЛ", note)


class TestRenderReportRealTemplate(unittest.TestCase):
    """
    Сквозной тест render_report/write_report на РЕАЛЬНОМ references/report_template.md
    + disclaimers.md (не мок) — самый ценный тест этого файла: юниты выше проверяют
    report.py в изоляции, а именно этот класс поймал на практике реальный интеграционный
    баг при переходе на v1.3-шаблон (обрезание секции "## Приложение" старой логикой
    сплайса дисклеймеров — см. render_report, "не обрезка хвоста файла, а замена
    заголовка"). Самопропускается, если файлов шаблона ещё нет (не моя зона сборки),
    но на момент написания они уже есть.
    """

    TEMPLATE_PATH = _SKILL_ROOT / "references" / "report_template.md"
    DISCLAIMERS_PATH = _SKILL_ROOT / "references" / "disclaimers.md"

    def setUp(self):
        if not self.TEMPLATE_PATH.exists() or not self.DISCLAIMERS_PATH.exists():
            self.skipTest("references/report_template.md или disclaimers.md ещё не созданы (не моя зона сборки)")

    def _build_inputs(self):
        study = {
            "name": "test_study",
            "type": "claims_ranking",
            "stimuli": [{"id": "A", "text": "Стимул А"}, {"id": "B", "text": "Стимул Б"}],
            "segments": ["seg1"],
        }
        segments = {"seg1": {"name": "Тестовый сегмент"}}
        rng = np.random.default_rng(0)
        shared = rng.normal(0, 0.15, size=8)
        resp_rows = _resp_rows("seg1", {"A": list(4.5 + shared), "B": list(3.0 + shared)})
        seg_rows = _segment_rows("seg1", {"A": 4.5, "B": 3.0}, n_respondents=8)
        sample_rows = _sample_rows_from_resp(resp_rows, n_samples=2, jitter=0.05, seed=1)
        header_mapping = {
            "STUDY_NAME": study["name"],
            "STUDY_TYPE": study["type"],
            "RUN_DATE": "2026-07-18T00:00:00+00:00",
            "PROVIDER_MODE": "agent",
            "MODEL_ID": "не зафиксирована (agent-режим)",
            "EMBEDDING_MODEL_ID": "ai-forever/ru-en-RoSBERTa",
            "ANCHORS_VERSION": "2",
            "N_PROFILES_TOTAL": "8",
            "PROFILES_PER_SEGMENT": "8",
            "SAMPLES_PER_RESPONDENT": "2",
            "N_RESPONSES": "32",
            "N_SEGMENTS": "1",
            "N_STIMULI": "2",
            "SEGMENT_NAMES_LIST": "Тестовый сегмент",
            "SCALE_NAME_RU": "Готовность купить",
            "SCALE_ID": "purchase_intent",
            "MANIFEST_FILENAME": "manifest.json",
            "MANIFEST_PATH": "manifest.json",
            "MODE": "exploratory",
            "MODE_BADGE": "🟡 РАЗВЕДОЧНЫЙ",
            "CONTROLS_STATUS_LINE": "плацебо и ловушка на своих местах — самоконтроль пройден",
            "CONTROLS_FAILED_BANNER": "",
            "ASCII_BAR": "▁▂▇▄▁",
        }
        return study, segments, seg_rows, resp_rows, sample_rows, header_mapping

    def test_render_report_end_to_end_no_crash_and_all_sections_present(self):
        study, segments, seg_rows, resp_rows, sample_rows, header_mapping = self._build_inputs()
        text = report.render_report(
            template_path=self.TEMPLATE_PATH,
            disclaimers_path=self.DISCLAIMERS_PATH,
            rows=seg_rows,
            resp_rows=resp_rows,
            sample_rows=sample_rows,
            study=study,
            segments=segments,
            scale_name_ru="Готовность купить",
            scale_id="purchase_intent",
            header_mapping=header_mapping,
            bootstrap_iters=500,
            bootstrap_seed=42,
            controls_verdict={"applicable": False},
        )
        # Все секции обязаны присутствовать - "## Приложение" в частности НЕ
        # должно теряться (регрессия конкретного бага, найденного смоук-тестом).
        for heading in (
            "## Главное",
            "## Паспорт методологии",
            "## 1. Рейтинг стимулов по сегментам",
            "## 2. Качественный разбор",
            "## Что с этим делать",
            "## Границы этого отчёта",
            "## Приложение",
        ):
            self.assertIn(heading, text, f"отсутствует секция {heading!r}")
        self.assertIn("<!-- KEY_TAKEAWAYS -->", text)
        self.assertIn("<!-- QUALITATIVE -->", text)
        self.assertIn("<!-- NEXT_STEPS -->", text)
        self.assertNotIn("{{", text, "остались незамещённые {{...}}-плейсхолдеры")
        self.assertIn("уверенный разрыв", text)
        self.assertIn("🟡 РАЗВЕДОЧНЫЙ", text)

    def test_render_report_controls_failed_banner_reaches_final_output(self):
        study, segments, seg_rows, resp_rows, sample_rows, header_mapping = self._build_inputs()
        header_mapping["CONTROLS_STATUS_LINE"] = (
            "прогон НЕ прошёл самоконтроль (см. приложение) — выводы не использовать"
        )
        header_mapping["CONTROLS_FAILED_BANNER"] = (
            "> **прогон не прошёл самоконтроль, выводы не использовать.** Детали в приложении."
        )
        controls_verdict = {
            "applicable": True,
            "controls_failed": True,
            "decoy_of": "A",
            "per_segment": [
                {
                    "segment": "seg1",
                    "placebo_rank": 1,
                    "placebo_n_total": 4,
                    "placebo_ok": False,
                    "decoy_label": "уверенный разрыв",
                    "decoy_ok": False,
                }
            ],
        }
        text = report.render_report(
            template_path=self.TEMPLATE_PATH,
            disclaimers_path=self.DISCLAIMERS_PATH,
            rows=seg_rows,
            resp_rows=resp_rows,
            sample_rows=sample_rows,
            study=study,
            segments=segments,
            scale_name_ru="Готовность купить",
            scale_id="purchase_intent",
            header_mapping=header_mapping,
            bootstrap_iters=500,
            bootstrap_seed=42,
            controls_verdict=controls_verdict,
        )
        self.assertIn("прогон не прошёл самоконтроль", text)
        self.assertIn("## Приложение", text)
        self.assertNotIn("{{", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""
test_ssr.py — юнит-тесты SSR-математики (scripts/ssr_core.py).

Требование spec_synthetic-panel_v1.md §11.2: все тесты зелёные БЕЗ сети и БЕЗ
скачивания моделей. Поэтому здесь только чистый numpy — ни один тест не создаёт
SentenceTransformerBackend и не импортирует sentence_transformers. Эмбеддинги
эмулируются вручную (простые синтетические векторы) или мок-классом MockEmbeddingBackend.

Запуск:
    python scripts/test_ssr.py
    (или: python -m unittest scripts.test_ssr -v из корня скилла)

Покрытие (см. spec §4 и §11.2):
    - монотонность PMF/E при смещении ответа от якоря 1 к якорю 5;
    - нормировка (PMF всегда суммируется в 1);
    - инвариантность к порядку наборов якорей;
    - поведение при малом/нулевом epsilon (включая вырожденный случай);
    - стабильность бутстрепа при фиксированном seed;
    - бонус: температура (T=1.0 — тождество), average_pmfs, aggregate_pmfs_by_key,
      load_anchor_sets (парсер references/anchors_ru.yaml на встроенных фикстурах).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import yaml

# Позволяет запускать файл напрямую (python scripts/test_ssr.py) независимо от cwd.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import ssr_core  # noqa: E402


class MockEmbeddingBackend(ssr_core.EmbeddingBackend):
    """Мок-бэкенд для тестов уровня SSREngine: возвращает заранее заданные векторы по тексту."""

    def __init__(self, vectors_by_text: dict[str, np.ndarray]):
        self.vectors_by_text = vectors_by_text

    def encode(self, texts):
        return np.stack([self.vectors_by_text[t] for t in texts], axis=0)


# ----------------------------------------------------------------------------
# Нормировка
# ----------------------------------------------------------------------------


class TestNormalization(unittest.TestCase):
    def test_pmf_rows_sum_to_one_random_vectors(self):
        rng = np.random.default_rng(0)
        response_embs = rng.normal(size=(20, 8))
        anchor_embs = rng.normal(size=(5, 8))
        for epsilon in (0.001, 0.05, 0.5, 1.0):
            pmf = ssr_core.pmf_single_anchor_set(response_embs, anchor_embs, epsilon)
            self.assertEqual(pmf.shape, (20, 5))
            np.testing.assert_allclose(pmf.sum(axis=1), np.ones(20), rtol=0, atol=1e-9)
            self.assertTrue(np.all(pmf >= 0.0))

    def test_pmf_sums_to_one_after_averaging_sets(self):
        rng = np.random.default_rng(1)
        response_embs = rng.normal(size=(10, 6))
        anchor_sets = [rng.normal(size=(5, 6)) for _ in range(4)]
        per_set = [ssr_core.pmf_single_anchor_set(response_embs, a, 0.001) for a in anchor_sets]
        averaged = ssr_core.average_pmfs(per_set)
        np.testing.assert_allclose(averaged.sum(axis=1), np.ones(10), atol=1e-9)


# ----------------------------------------------------------------------------
# Монотонность
# ----------------------------------------------------------------------------


class TestMonotonicity(unittest.TestCase):
    def test_expected_value_increases_as_response_moves_toward_anchor5(self):
        """
        5 якорей = 5 ортонормированных базисных векторов e_1..e_5 (шкала 1..5).
        Ответ response(t) = нормализованная смесь (1-t)*e_1 + t*e_5, t от 0 до 1.
        При t=0 ответ идентичен якорю 1 (E должно быть близко к 1), при t=1 —
        идентичен якорю 5 (E должно быть близко к 5). E(t) должно быть
        неубывающим по t — это и есть свойство монотонности SSR.
        """
        anchor_embs = np.eye(5)  # e_1..e_5
        ts = np.linspace(0.0, 1.0, 25)
        e_values = []
        for t in ts:
            vec = (1 - t) * anchor_embs[0] + t * anchor_embs[4]
            response_embs = vec.reshape(1, -1)
            pmf = ssr_core.pmf_single_anchor_set(response_embs, anchor_embs, epsilon=0.001)
            e = ssr_core.expected_value(pmf)[0]
            e_values.append(e)
        e_values = np.array(e_values)
        # неубывание с небольшим допуском на числовой шум
        diffs = np.diff(e_values)
        self.assertTrue(np.all(diffs >= -1e-9), f"E(t) не монотонно: {e_values}")
        # крайние точки — около полюсов шкалы
        self.assertLess(e_values[0], 2.0)
        self.assertGreater(e_values[-1], 4.0)

    def test_pmf_shifts_toward_anchor5_at_t1(self):
        anchor_embs = np.eye(5)
        response_embs = anchor_embs[4].reshape(1, -1)  # ответ идентичен якорю 5
        pmf = ssr_core.pmf_single_anchor_set(response_embs, anchor_embs, epsilon=0.001)[0]
        self.assertEqual(int(np.argmax(pmf)), 4)  # индекс 4 = точка шкалы "5"


# ----------------------------------------------------------------------------
# Инвариантность к порядку наборов якорей
# ----------------------------------------------------------------------------


class TestOrderInvariance(unittest.TestCase):
    def test_average_pmfs_invariant_to_set_order(self):
        rng = np.random.default_rng(2)
        response_embs = rng.normal(size=(7, 5))
        anchor_sets = [rng.normal(size=(5, 5)) for _ in range(4)]
        per_set_pmfs = [ssr_core.pmf_single_anchor_set(response_embs, a, 0.001) for a in anchor_sets]

        original_order = ssr_core.average_pmfs(per_set_pmfs)
        shuffled_order = ssr_core.average_pmfs(list(reversed(per_set_pmfs)))
        np.testing.assert_allclose(original_order, shuffled_order, atol=1e-12)

    def test_ssr_engine_score_invariant_to_anchor_set_list_order(self):
        rng = np.random.default_rng(3)
        dim = 6
        vectors = {f"resp{i}": rng.normal(size=dim) for i in range(5)}
        backend = MockEmbeddingBackend(vectors)
        # 4 набора, каждый — свой словарь фраз 1..5 (тексты используем как ключи в backend)
        anchor_sets = []
        for s in range(4):
            phrases = {i: f"set{s}_anchor{i}" for i in range(1, 6)}
            for i in range(1, 6):
                vectors[phrases[i]] = rng.normal(size=dim)
            anchor_sets.append(phrases)

        engine_a = ssr_core.SSREngine(backend, anchor_sets, epsilon=0.001, min_anchor_sets=4)
        engine_b = ssr_core.SSREngine(backend, list(reversed(anchor_sets)), epsilon=0.001, min_anchor_sets=4)
        texts = list(vectors.keys())[:5]
        pmf_a = engine_a.score_texts(texts)
        pmf_b = engine_b.score_texts(texts)
        np.testing.assert_allclose(pmf_a, pmf_b, atol=1e-12)


# ----------------------------------------------------------------------------
# Поведение при малом/нулевом epsilon
# ----------------------------------------------------------------------------


class TestEpsilonDegradation(unittest.TestCase):
    def test_epsilon_zero_gives_exact_zero_at_min_similarity(self):
        anchor_embs = np.eye(5)
        response_embs = anchor_embs[4].reshape(1, -1)  # идентичен якорю 5 => якорь 1 - антипод по косинусу(0)
        pmf = ssr_core.pmf_from_similarities(
            ssr_core.cosine_similarity_matrix(response_embs, anchor_embs), epsilon=0.0
        )[0]
        self.assertAlmostEqual(pmf[np.argmin(pmf)], 0.0, places=9)
        self.assertAlmostEqual(pmf.sum(), 1.0, places=9)

    def test_epsilon_degenerate_all_equal_similarities_falls_back_to_uniform(self):
        """Все сходства строки идентичны И epsilon=0 -> числитель/знаменатель нулевые.
        Ожидаем корректный откат к равномерному распределению, без NaN/деления на ноль."""
        sim = np.array([[0.3, 0.3, 0.3, 0.3, 0.3]])
        pmf = ssr_core.pmf_from_similarities(sim, epsilon=0.0)
        self.assertTrue(np.all(np.isfinite(pmf)))
        np.testing.assert_allclose(pmf, np.full((1, 5), 0.2), atol=1e-12)

    def test_small_epsilon_still_normalizes_and_stays_finite(self):
        rng = np.random.default_rng(4)
        sim = rng.normal(size=(10, 5))
        for epsilon in (1e-6, 1e-3, 0.0):
            pmf = ssr_core.pmf_from_similarities(sim, epsilon)
            self.assertTrue(np.all(np.isfinite(pmf)))
            np.testing.assert_allclose(pmf.sum(axis=1), np.ones(10), atol=1e-9)


# ----------------------------------------------------------------------------
# Температура (бонус: явный тест на T=1.0 = тождество, зафиксировано в §4)
# ----------------------------------------------------------------------------


class TestTemperature(unittest.TestCase):
    def test_temperature_one_is_identity(self):
        rng = np.random.default_rng(5)
        pmf = ssr_core.pmf_from_similarities(rng.normal(size=(5, 5)), epsilon=0.01)
        result = ssr_core.apply_temperature(pmf, 1.0)
        np.testing.assert_array_equal(result, pmf)

    def test_temperature_below_one_sharpens_distribution(self):
        pmf = np.array([[0.1, 0.15, 0.2, 0.25, 0.3]])
        sharpened = ssr_core.apply_temperature(pmf, 0.3)
        # заострение увеличивает максимум и уменьшает минимум относительно исходного
        self.assertGreater(sharpened.max(), pmf.max())
        self.assertLess(sharpened.min(), pmf.min())
        self.assertAlmostEqual(float(sharpened.sum()), 1.0, places=9)

    def test_temperature_rejects_non_positive(self):
        pmf = np.array([[0.2, 0.2, 0.2, 0.2, 0.2]])
        with self.assertRaises(ValueError):
            ssr_core.apply_temperature(pmf, 0.0)


# ----------------------------------------------------------------------------
# Бутстреп: стабильность при фиксированном seed
# ----------------------------------------------------------------------------


class TestBootstrapStability(unittest.TestCase):
    def test_same_seed_gives_identical_result(self):
        values = np.array([1.2, 2.5, 3.1, 2.8, 4.0, 3.6, 2.1, 3.9])
        r1 = ssr_core.bootstrap_ci(values, n_iters=500, seed=42, ci=0.95)
        r2 = ssr_core.bootstrap_ci(values, n_iters=500, seed=42, ci=0.95)
        self.assertEqual(r1.point_estimate, r2.point_estimate)
        self.assertEqual(r1.ci_low, r2.ci_low)
        self.assertEqual(r1.ci_high, r2.ci_high)

    def test_different_seed_can_differ_but_stays_within_scale(self):
        values = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 3.0, 2.0])
        r = ssr_core.bootstrap_ci(values, n_iters=1000, seed=7, ci=0.95)
        self.assertLessEqual(r.ci_low, r.point_estimate)
        self.assertLessEqual(r.point_estimate, r.ci_high)
        self.assertGreaterEqual(r.ci_low, 1.0)
        self.assertLessEqual(r.ci_high, 5.0)

    def test_bootstrap_single_value_degenerates_to_point(self):
        r = ssr_core.bootstrap_ci(np.array([3.5]), n_iters=100, seed=1)
        self.assertEqual(r.point_estimate, 3.5)
        self.assertEqual(r.ci_low, 3.5)
        self.assertEqual(r.ci_high, 3.5)

    def test_bootstrap_empty_raises(self):
        with self.assertRaises(ValueError):
            ssr_core.bootstrap_ci(np.array([]))


# ----------------------------------------------------------------------------
# aggregate_pmfs_by_key
# ----------------------------------------------------------------------------


class TestAggregateByKey(unittest.TestCase):
    def test_groups_and_averages_correctly(self):
        pmfs = np.array([
            [1.0, 0, 0, 0, 0],
            [0.0, 1, 0, 0, 0],
            [0, 0, 1.0, 0, 0],
        ])
        keys = ["a", "a", "b"]
        grouped = ssr_core.aggregate_pmfs_by_key(pmfs, keys)
        self.assertEqual(set(grouped.keys()), {"a", "b"})
        np.testing.assert_allclose(grouped["a"], [0.5, 0.5, 0, 0, 0])
        np.testing.assert_allclose(grouped["b"], [0, 0, 1.0, 0, 0])


# ----------------------------------------------------------------------------
# load_anchor_sets — парсер references/anchors_ru.yaml на встроенных фикстурах
# ----------------------------------------------------------------------------


class TestLoadAnchorSets(unittest.TestCase):
    def _write_yaml(self, tmp_dir: Path, data: dict) -> Path:
        path = tmp_dir / "anchors_ru.yaml"
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return path

    def test_parses_wrapped_schema_with_scales_key(self):
        data = {
            "meta": {"min_sets_required": 4},
            "scales": {
                "purchase_intent": {
                    "question": "Купили бы вы это?",
                    "anchor_sets": [
                        {"label": "набор1", "phrases": {1: "нет", 2: "вряд ли", 3: "может быть", 4: "скорее да", 5: "да"}},
                        {"label": "набор2", "phrases": {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"}},
                        {"label": "набор3", "phrases": {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"}},
                        {"label": "набор4", "phrases": {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"}},
                    ],
                }
            },
        }
        with tempfile.TemporaryDirectory() as td:
            path = self._write_yaml(Path(td), data)
            question, anchor_sets = ssr_core.load_anchor_sets(path, "purchase_intent")
            self.assertEqual(question, "Купили бы вы это?")
            self.assertEqual(len(anchor_sets), 4)
            self.assertEqual(anchor_sets[0][1], "нет")
            self.assertEqual(anchor_sets[0][5], "да")

    def test_parses_bare_phrases_without_wrapper(self):
        data = {
            "scales": {
                "appeal": {
                    "question": "Нравится?",
                    "anchor_sets": [
                        {1: "не нравится", 2: "b", 3: "c", 4: "d", 5: "нравится", "label": "служебное поле"},
                        {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"},
                        {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"},
                        {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"},
                    ],
                }
            }
        }
        with tempfile.TemporaryDirectory() as td:
            path = self._write_yaml(Path(td), data)
            question, anchor_sets = ssr_core.load_anchor_sets(path, "appeal")
            self.assertEqual(anchor_sets[0][1], "не нравится")
            self.assertEqual(anchor_sets[0][5], "нравится")

    def test_missing_scale_raises_key_error_with_available_list(self):
        data = {"scales": {"appeal": {"question": "q", "anchor_sets": []}}}
        with tempfile.TemporaryDirectory() as td:
            path = self._write_yaml(Path(td), data)
            with self.assertRaises(KeyError):
                ssr_core.load_anchor_sets(path, "purchase_intent")

    def test_wrong_phrase_count_raises_value_error(self):
        data = {
            "scales": {
                "relevance": {
                    "question": "q",
                    "anchor_sets": [
                        {"phrases": {1: "a", 2: "b", 3: "c", 4: "d"}},  # только 4 фразы вместо 5
                        {"phrases": {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"}},
                        {"phrases": {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"}},
                        {"phrases": {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"}},
                    ],
                }
            }
        }
        with tempfile.TemporaryDirectory() as td:
            path = self._write_yaml(Path(td), data)
            with self.assertRaises(ValueError):
                ssr_core.load_anchor_sets(path, "relevance")

    def test_missing_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            ssr_core.load_anchor_sets(Path("/nonexistent/anchors_ru.yaml"), "purchase_intent")

    def test_parses_real_anchors_ru_yaml_if_present(self):
        """
        Если references/anchors_ru.yaml уже существует в дереве скилла (сборщик
        методологии мог создать его параллельно) — проверяем совместимость парсера
        с реальным файлом, а не только с фикстурами. Тест самопропускается, если
        файла ещё нет (не блокирует общий прогон test_ssr.py).
        """
        real_path = _SCRIPTS_DIR.parent / "references" / "anchors_ru.yaml"
        if not real_path.exists():
            self.skipTest("references/anchors_ru.yaml ещё не создан (не моя зона сборки)")
        with real_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        scales = data.get("scales", {})
        self.assertTrue(scales, "в anchors_ru.yaml нет ни одной шкалы")
        for scale_id in scales:
            question, anchor_sets = ssr_core.load_anchor_sets(real_path, scale_id)
            self.assertIsInstance(question, str)
            self.assertGreaterEqual(len(anchor_sets), 4, f"шкала {scale_id}: меньше 4 наборов якорей")
            for s in anchor_sets:
                self.assertEqual(sorted(s.keys()), [1, 2, 3, 4, 5])


if __name__ == "__main__":
    unittest.main(verbosity=2)

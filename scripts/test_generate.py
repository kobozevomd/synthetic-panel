#!/usr/bin/env python3
"""
test_generate.py — юнит-тесты карточки персоны (scripts/generate.py, §1.1 v1.3, фикс Д1).

Требование spec_synthetic-panel_v1.3.md §1.1 + общее правило проекта: тесты зелёные
БЕЗ сети и без embedding-модели (generate.py вообще не импортирует sentence_transformers,
см. модульный докстринг ssr_core.py) — здесь только чистая сборка строк из словарей.

Покрытие:
    - jitter_persona: детерминированность, мягкая деградация (пустой/минимальный
      сегмент), опциональное gender не потребляет rng, если отсутствует в YAML
      (обратная совместимость возраста/дохода/города для ВСЕХ существующих сегментов).
    - _axis_label: известный ключ -> перевод; неизвестный -> гуманизированный fallback
      без падения.
    - _compress_text: короткий текст без изменений; несколько предложений; жёсткая
      обрезка длинного предложения по границе слова (не по середине слова); пустой ввод.
    - build_persona_card: полный сегмент -> все секции по порядку; минимальный сегмент
      -> только строка "Профиль"; мотивация/барьер/gender — мягкая деградация в обе
      стороны; детерминированность (один и тот же профиль+сегмент -> идентичный текст).
    - build_system_prompt: включает карточку, запрещает CoT/AI-упоминание, не падает
      без gender/language_flavors.
    - build_tasks: согласованность персоны — ОДИН респондент получает ОДНУ и ту же
      карточку на ВСЕХ своих стимулах/сэмплах (условие "связный человек, не кости").
"""

from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import generate  # noqa: E402

# Несколько тестов ниже НАРОЧНО подают неизвестные токены/оси, чтобы проверить
# graceful-fallback ветки (_translate_jitter_token/_axis_label) — это ожидаемо
# печатает WARNING в лог; глушим уровень логирования модуля, чтобы не шуметь
# в verbose-выводе unittest (сами тесты по-прежнему проверяют РЕЗУЛЬТАТ fallback).
logging.getLogger("generate").setLevel(logging.ERROR)

# Сегмент, использующий ВСЕ поля, которые build_persona_card умеет читать —
# включая schema-задел на будущее (motivation/barrier) и persona_jitter.gender,
# которых сегодня нет ни в одном panel/segments/**/*.yaml (см. generate.py докстринг).
FULL_SEGMENT: dict = {
    "id": "full_seg",
    "name": "Полный Сегмент",
    "description": "Первое предложение описания. Второе предложение с деталями мотивации и барьера.",
    "motivation": "Хочет быстрый результат.",
    "barrier": "Не доверяет рекламе.",
    "axes": {
        "hair_loss_anxiety": "высокая",
        "unknown_future_axis_xyz": "средняя",
    },
    "behavior": {
        "formats": ["формат1", "формат2"],
        "occasions": ["повод1"],
        "channels": ["канал1", "канал2"],
        "price_sensitivity": "Цена вторична относительно уверенности в результате.",
    },
    "language": ["Фраза раз", "Фраза два", "Фраза три"],
    "brands_context": "Категория воспринимается нейтрально, без оценки долей рынка.",
    "persona_jitter": {
        "age": [30, 40],
        "income_level": ["average"],
        "city_tier": ["big_city"],
        "gender": ["ж", "м"],
    },
}

# Минимальный сегмент — только обязательные для jitter_persona/build_persona_card
# поля id/name, БЕЗ persona_jitter/description/axes/behavior/brands_context/language.
# Мягкая деградация обязана давать связную (хоть и короткую) карточку, не падать.
MINIMAL_SEGMENT: dict = {"id": "min_seg", "name": "Минимальный"}


class TestJitterPersonaDeterminism(unittest.TestCase):
    def test_same_inputs_give_identical_profile(self):
        p1 = generate.jitter_persona(FULL_SEGMENT, "full_seg", 3, seed=42)
        p2 = generate.jitter_persona(FULL_SEGMENT, "full_seg", 3, seed=42)
        self.assertEqual(p1, p2)

    def test_different_respondent_idx_can_differ(self):
        profiles = [
            generate.jitter_persona(FULL_SEGMENT, "full_seg", i, seed=42) for i in range(1, 11)
        ]
        ages = {p["age"] for p in profiles}
        # Диапазон возраста 30-40 (11 значений) на 10 респондентов — почти наверняка
        # не все возрасты совпадут; допускаем полное совпадение НЕ ожидать (это не
        # гарантия математически, но при 11 возможных значениях и разном seed-хэше
        # на респондента это надёжный сигнал отсутствия константной заглушки).
        self.assertGreater(len(ages), 1, "джиттер возраста не варьируется между респондентами")

    def test_gender_absent_does_not_consume_rng_state_for_existing_segments(self):
        """
        Обратная совместимость (§1.1): у сегмента БЕЗ persona_jitter.gender (как у
        ВСЕХ 24 текущих panel/segments/**/*.yaml) age/income/city обязаны остаться
        такими же, как если бы gender-ветки в jitter_persona не было вовсе —
        проверяем это, сравнивая с сегментом, у которого persona_jitter идентичен,
        но БЕЗ ключа gender.
        """
        seg_with_gender = dict(FULL_SEGMENT)
        seg_with_gender["persona_jitter"] = dict(FULL_SEGMENT["persona_jitter"])
        seg_no_gender = dict(seg_with_gender)
        seg_no_gender["persona_jitter"] = {
            k: v for k, v in seg_with_gender["persona_jitter"].items() if k != "gender"
        }
        p_with = generate.jitter_persona(seg_with_gender, "full_seg", 5, seed=1)
        p_without = generate.jitter_persona(seg_no_gender, "full_seg", 5, seed=1)
        self.assertEqual(p_with["age"], p_without["age"])
        self.assertEqual(p_with["income_level"], p_without["income_level"])
        self.assertEqual(p_with["city_tier"], p_without["city_tier"])
        self.assertIsNone(p_without["gender"])
        self.assertIn(p_with["gender"], ("ж", "м"))

    def test_minimal_segment_does_not_crash_and_has_defaults(self):
        profile = generate.jitter_persona(MINIMAL_SEGMENT, "min_seg", 1, seed=7)
        self.assertIsInstance(profile["age"], int)
        self.assertTrue(25 <= profile["age"] <= 55)
        self.assertEqual(profile["income_level"], "средний доход")
        self.assertEqual(profile["city_tier"], "крупный город")
        self.assertIsNone(profile["gender"])
        self.assertEqual(profile["language_flavors"], [])

    def test_language_flavors_at_most_two_and_no_duplicates(self):
        profile = generate.jitter_persona(FULL_SEGMENT, "full_seg", 1, seed=42)
        flavors = profile["language_flavors"]
        self.assertLessEqual(len(flavors), 2)
        self.assertEqual(len(flavors), len(set(flavors)))
        for f in flavors:
            self.assertIn(f, FULL_SEGMENT["language"])


class TestAxisLabel(unittest.TestCase):
    def test_known_axis_translated(self):
        self.assertEqual(generate._axis_label("hair_loss_anxiety"), "тревога из-за выпадения волос")

    def test_unknown_axis_humanized_fallback_no_crash(self):
        label = generate._axis_label("some_brand_new_axis")
        self.assertEqual(label, "some brand new axis")


class TestCompressText(unittest.TestCase):
    def test_empty_text_returns_empty(self):
        self.assertEqual(generate._compress_text(""), "")
        self.assertEqual(generate._compress_text("   "), "")

    def test_short_text_passthrough(self):
        text = "Короткое предложение."
        self.assertEqual(generate._compress_text(text), text)

    def test_keeps_up_to_max_sentences_when_short_enough(self):
        text = "Предложение раз. Предложение два. Предложение три должно быть отброшено."
        result = generate._compress_text(text, max_sentences=2, max_chars=300)
        self.assertIn("Предложение раз.", result)
        self.assertIn("Предложение два.", result)
        self.assertNotIn("отброшено", result)

    def test_stops_before_second_sentence_if_it_would_exceed_max_chars(self):
        first = "Первое предложение довольно длинное само по себе, но помещается."
        second = "А вот второе предложение, если его добавить, обязано превысить лимит символов итоговой строки."
        result = generate._compress_text(f"{first} {second}", max_sentences=2, max_chars=len(first) + 5)
        self.assertEqual(result, first)

    def test_long_single_sentence_truncates_at_word_boundary_with_ellipsis(self):
        text = "Слово " * 50 + "конец."
        result = generate._compress_text(text, max_sentences=1, max_chars=30)
        self.assertTrue(result.endswith("…"))
        self.assertNotIn("Сло…", result)  # не обрезано на середине слова "Слово"
        self.assertLessEqual(len(result), 31)

    def test_collapses_internal_whitespace_and_newlines(self):
        text = "Текст   с  \n лишними    пробелами."
        result = generate._compress_text(text)
        self.assertEqual(result, "Текст с лишними пробелами.")


class TestBuildPersonaCard(unittest.TestCase):
    def test_full_segment_includes_all_sections_in_order(self):
        profile = generate.jitter_persona(FULL_SEGMENT, "full_seg", 1, seed=42)
        card = generate.build_persona_card(profile, FULL_SEGMENT)
        lines = card.split("\n")
        prefixes = [
            "Профиль:",
            "Кто это:",
            "Главное:",
            "Особенности:",
            "Поведение:",
            "Опыт категории:",
            "Говорит в духе:",
        ]
        self.assertEqual(len(lines), len(prefixes))
        for line, prefix in zip(lines, prefixes):
            self.assertTrue(line.startswith(prefix), f"{line!r} не начинается с {prefix!r}")

        self.assertIn("мотивация — Хочет быстрый результат", card)
        self.assertIn("барьер — Не доверяет рекламе", card)
        self.assertIn("тревога из-за выпадения волос — высокая", card)
        self.assertIn("unknown future axis xyz — средняя", card)  # гуманизированный fallback
        self.assertIn("формат1", card)
        self.assertIn("повод1", card)
        self.assertIn("канал1", card)
        self.assertIn("Опыт категории:", card)

    def test_no_double_terminal_punctuation_from_compressed_clauses(self):
        profile = generate.jitter_persona(FULL_SEGMENT, "full_seg", 1, seed=42)
        card = generate.build_persona_card(profile, FULL_SEGMENT)
        self.assertNotIn("..", card)
        self.assertNotIn(".;", card)

    def test_minimal_segment_gives_only_profile_line(self):
        profile = generate.jitter_persona(MINIMAL_SEGMENT, "min_seg", 1, seed=42)
        card = generate.build_persona_card(profile, MINIMAL_SEGMENT)
        lines = card.split("\n")
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("Профиль:"))
        self.assertIn("«Минимальный»", lines[0])

    def test_motivation_only_without_barrier(self):
        seg = dict(MINIMAL_SEGMENT)
        seg["motivation"] = "Только мотивация задана."
        profile = generate.jitter_persona(seg, "min_seg", 1, seed=1)
        card = generate.build_persona_card(profile, seg)
        self.assertIn("Главное: мотивация — Только мотивация задана.", card)
        self.assertNotIn("барьер", card)

    def test_gender_omitted_when_not_jittered(self):
        profile = generate.jitter_persona(MINIMAL_SEGMENT, "min_seg", 1, seed=1)
        card = generate.build_persona_card(profile, MINIMAL_SEGMENT)
        first_line = card.split("\n")[0]
        # Без gender строка начинается сразу с возраста, а не с "женщина,"/"мужчина,"
        self.assertRegex(first_line, r"^Профиль: \d+ лет")

    def test_gender_present_when_jittered(self):
        profile = generate.jitter_persona(FULL_SEGMENT, "full_seg", 1, seed=42)
        card = generate.build_persona_card(profile, FULL_SEGMENT)
        first_line = card.split("\n")[0]
        self.assertTrue(
            first_line.startswith("Профиль: женщина,") or first_line.startswith("Профиль: мужчина,")
        )

    def test_deterministic_same_profile_and_segment_gives_identical_card(self):
        profile = generate.jitter_persona(FULL_SEGMENT, "full_seg", 2, seed=99)
        card1 = generate.build_persona_card(profile, FULL_SEGMENT)
        card2 = generate.build_persona_card(profile, FULL_SEGMENT)
        self.assertEqual(card1, card2)

    def test_no_anchor_scale_vocabulary_leaks_in(self):
        """
        Дымовой тест на утечку шкалы (§0 несъёмный принцип): карточка строится
        ТОЛЬКО из segment/profile - в её сборке физически не участвует ни
        anchors_ru.yaml, ни какой-либо объект якорей, так что фразы вроде
        "точно куплю"/"совсем не нравится" не могут туда попасть, кроме как через
        сам YAML сегмента (которого в фикстурах этого теста нет).
        """
        profile = generate.jitter_persona(FULL_SEGMENT, "full_seg", 1, seed=42)
        card = generate.build_persona_card(profile, FULL_SEGMENT)
        for leaked in ("точно куплю", "совсем не нравится", "точно про меня"):
            self.assertNotIn(leaked, card)


class TestBuildSystemPrompt(unittest.TestCase):
    def test_includes_card_and_forbids_cot_and_ai_disclosure(self):
        profile = generate.jitter_persona(FULL_SEGMENT, "full_seg", 1, seed=42)
        prompt = generate.build_system_prompt(profile, FULL_SEGMENT)
        self.assertIn("Профиль:", prompt)
        self.assertIn("Не рассуждай пошагово", prompt)
        self.assertIn("Не упоминай, что ты ИИ", prompt)

    def test_does_not_crash_without_gender_or_language(self):
        profile = generate.jitter_persona(MINIMAL_SEGMENT, "min_seg", 1, seed=1)
        prompt = generate.build_system_prompt(profile, MINIMAL_SEGMENT)
        self.assertIn("Профиль:", prompt)


class TestBuildTasksPersonaConsistency(unittest.TestCase):
    """'Один профиль = связный человек' — build_tasks обязан переиспользовать ОДНУ
    и ту же карточку персоны для ВСЕХ стимулов/сэмплов ОДНОГО респондента."""

    def setUp(self):
        self.study = {
            "segments": ["full_seg"],
            "respondents_per_segment": 4,
            "stimuli": [
                {"id": "A", "text": "Стимул А"},
                {"id": "B", "text": "Стимул Б"},
                {"id": "C", "text": "Стимул В"},
            ],
        }
        self.segments = {"full_seg": FULL_SEGMENT}

    def test_same_respondent_same_persona_across_stimuli_and_samples(self):
        tasks = generate.build_tasks(
            self.study, self.segments, question="Вопрос?", seed=42, samples_per_respondent=2
        )
        by_respondent: dict[int, set[str]] = {}
        for t in tasks:
            by_respondent.setdefault(t.respondent_idx, set()).add(t.persona)
        self.assertEqual(len(by_respondent), 4)
        for respondent_idx, personas in by_respondent.items():
            self.assertEqual(
                len(personas), 1, f"респондент {respondent_idx} получил разные карточки персоны"
            )

    def test_different_respondents_can_get_different_personas(self):
        tasks = generate.build_tasks(
            self.study, self.segments, question="Вопрос?", seed=42, samples_per_respondent=1
        )
        personas_by_idx = {t.respondent_idx: t.persona for t in tasks}
        self.assertGreater(len(set(personas_by_idx.values())), 1)

    def test_total_task_count(self):
        tasks = generate.build_tasks(
            self.study, self.segments, question="Вопрос?", seed=42, samples_per_respondent=2
        )
        # 1 сегмент x 4 респондента x 3 стимула x 2 сэмпла
        self.assertEqual(len(tasks), 1 * 4 * 3 * 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

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

Визуальные стимулы (spec_synthetic-panel_v1.4.md §1.1/1.3, Модуль 1) — добавлено v1.4:
    - build_task_prompt: ветки image+text / image-only(label) / текст-только (байт-в-байт
      как в v1.3, регрессия).
    - ResponseTask/build_tasks: image_path/label корректно попадают в задачу из
      stimulus["image"]/stimulus["label"].
    - encode_image_base64/build_anthropic_image_block/build_openai_image_block: чистые
      функции файл->base64/mime — изображения-фикстуры генерируются PIL НА ЛЕТУ (tmp),
      без сети и без реальных вызовов API.
    - AnthropicProvider/OpenAIProvider.generate(image_path=...): мок HTTP-клиента
      (fake self._client), проверяем СТРУКТУРУ запроса (image-блок), а не сеть.
    - GigaChatProvider.generate(image_path=...): честная ProviderError о неподдержке
      визуальных стимулов, ДО NotImplementedError текстового TODO.
    - describe_image_via_provider/fill_vision_check_descriptions: FakeProvider (без сети).
"""

from __future__ import annotations

import base64
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

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
        """
        РЕШЕНО [review v1.3, находка №5]: дефолты income_level/city_tier — те же
        МАШИННЫЕ токены, что и реальные значения persona_jitter ("average"/
        "big_city"), а не уже готовый русский текст — см. generate.jitter_persona.
        Финальный человекочитаемый текст по-прежнему "средний доход"/"крупный
        город", но получается ЧЕРЕЗ перевод (см. test_default_jitter_tokens_
        translate_without_warning ниже), а не хардкодом на этом слое.
        """
        profile = generate.jitter_persona(MINIMAL_SEGMENT, "min_seg", 1, seed=7)
        self.assertIsInstance(profile["age"], int)
        self.assertTrue(25 <= profile["age"] <= 55)
        self.assertEqual(profile["income_level"], "average")
        self.assertEqual(profile["city_tier"], "big_city")
        self.assertIsNone(profile["gender"])
        self.assertEqual(profile["language_flavors"], [])

    def test_default_jitter_tokens_translate_without_warning(self):
        """
        РЕШЕНО [review v1.3, находка №5 MINOR]: сегмент БЕЗ persona_jitter (как
        MINIMAL_SEGMENT) раньше на КАЖДОМ респонденте писал в лог ложный warning
        "токен ... отсутствует в словаре перевода", потому что дефолт был уже
        готовым русским текстом, а не машинным токеном. Теперь дефолтный токен
        обязан НАЙТИСЬ в словаре перевода (_INCOME_LEVEL_RU/_CITY_TIER_RU) без
        единого предупреждения, и итоговая карточка обязана содержать корректный
        человекочитаемый русский текст (та же строка, что была бы и до фикса).
        """
        profile = generate.jitter_persona(MINIMAL_SEGMENT, "min_seg", 1, seed=7)
        with self.assertNoLogs("generate", level="WARNING"):
            card = generate.build_persona_card(profile, MINIMAL_SEGMENT)
        self.assertIn("средний доход", card)
        self.assertIn("крупный город", card)
        self.assertNotIn("average", card)
        self.assertNotIn("big_city", card)

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


# ============================================================================
# Визуальные стимулы (spec_synthetic-panel_v1.4.md §1.1/1.3) — build_task_prompt,
# ResponseTask/build_tasks, image-блоки провайдеров, проба зрения (API-режим).
# ============================================================================


def _make_png(path: Path, size: tuple[int, int] = (120, 80), color: tuple[int, int, int] = (10, 20, 30)) -> Path:
    """Фикстура-изображение НА ЛЕТУ (PIL, tmp) — без бинарных файлов в репозитории."""
    Image.new("RGB", size, color).save(path, "PNG")
    return path


class TestBuildTaskPromptVisual(unittest.TestCase):
    def test_text_only_unchanged_from_v13(self):
        """Регрессия: без image_path/label вывод БАЙТ-В-БАЙТ как до v1.4."""
        prompt = generate.build_task_prompt("Стимул текст", "Вопрос?")
        self.assertEqual(
            prompt,
            "Вот что тебе показывают:\n«Стимул текст»\n\nВопрос?\n\n"
            "Ответь свободным текстом, своими словами, БЕЗ числовой оценки и БЕЗ баллов по шкале.",
        )

    def test_mixed_image_and_text_quotes_text_and_mentions_image(self):
        prompt = generate.build_task_prompt("Текст на макете", "Вопрос?", image_path="/tmp/x.png")
        self.assertIn("Текст на макете", prompt)
        self.assertIn("макет", prompt.lower())
        self.assertIn("Вопрос?", prompt)

    def test_image_only_uses_label_not_empty_text(self):
        prompt = generate.build_task_prompt("", "Вопрос?", image_path="/tmp/x.png", label="Вариант синий")
        self.assertIn("Вариант синий", prompt)
        self.assertNotIn("«»", prompt)

    def test_image_only_without_label_has_placeholder_not_crash(self):
        prompt = generate.build_task_prompt("", "Вопрос?", image_path="/tmp/x.png", label=None)
        self.assertIn("без подписи", prompt)

    def test_no_anchor_leak_in_visual_branches(self):
        for kwargs in (
            {"image_path": "/tmp/x.png"},
            {"image_path": "/tmp/x.png", "label": "L"},
            {},
        ):
            prompt = generate.build_task_prompt("текст", "Вопрос?", **kwargs)
            self.assertNotIn("точно куплю", prompt)


class TestBuildTasksVisualFields(unittest.TestCase):
    def setUp(self):
        self.segments = {"full_seg": FULL_SEGMENT}
        self.study = {
            "segments": ["full_seg"],
            "respondents_per_segment": 1,
            "stimuli": [
                {"id": "A", "text": "Текстовый стимул"},
                {"id": "B", "image": "/abs/path/b.png", "label": "Вариант Б"},
                {"id": "C", "text": "Смешанный", "image": "/abs/path/c.png"},
            ],
        }

    def test_image_path_and_label_populated_per_stimulus(self):
        tasks = generate.build_tasks(self.study, self.segments, question="Q?", seed=1, samples_per_respondent=1)
        by_stim = {t.stimulus_id: t for t in tasks}
        self.assertIsNone(by_stim["A"].image_path)
        self.assertIsNone(by_stim["A"].label)
        self.assertEqual(by_stim["B"].image_path, "/abs/path/b.png")
        self.assertEqual(by_stim["B"].label, "Вариант Б")
        self.assertEqual(by_stim["B"].stimulus_text, "")
        self.assertEqual(by_stim["C"].image_path, "/abs/path/c.png")
        self.assertEqual(by_stim["C"].stimulus_text, "Смешанный")

    def test_missing_text_key_does_not_crash(self):
        """Image-only стимул может не иметь ключа 'text' вовсе (не только пустую строку)."""
        study = dict(self.study)
        study["stimuli"] = [{"id": "B", "image": "/abs/path/b.png", "label": "Вариант Б"}]
        tasks = generate.build_tasks(study, self.segments, question="Q?", seed=1, samples_per_respondent=1)
        self.assertEqual(tasks[0].stimulus_text, "")


class TestImageEncodingHelpers(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp_path = Path(self._tmpdir.name)

    def test_encode_image_base64_roundtrip_png(self):
        img_path = _make_png(self.tmp_path / "stim.png")
        data, mime = generate.encode_image_base64(str(img_path))
        self.assertEqual(mime, "image/png")
        self.assertEqual(base64.standard_b64decode(data), img_path.read_bytes())

    def test_encode_image_base64_jpeg_mime(self):
        img_path = self.tmp_path / "stim.jpg"
        Image.new("RGB", (50, 50), (1, 2, 3)).save(img_path, "JPEG")
        _data, mime = generate.encode_image_base64(str(img_path))
        self.assertEqual(mime, "image/jpeg")

    def test_unsupported_extension_raises_provider_error(self):
        bogus = self.tmp_path / "stim.gif"
        bogus.write_bytes(b"not really a gif")
        with self.assertRaises(generate.ProviderError):
            generate.encode_image_base64(str(bogus))

    def test_build_anthropic_image_block_structure(self):
        img_path = _make_png(self.tmp_path / "a.png")
        block = generate.build_anthropic_image_block(str(img_path))
        self.assertEqual(block["type"], "image")
        self.assertEqual(block["source"]["type"], "base64")
        self.assertEqual(block["source"]["media_type"], "image/png")
        self.assertTrue(block["source"]["data"])

    def test_build_openai_image_block_structure(self):
        img_path = _make_png(self.tmp_path / "a.png")
        block = generate.build_openai_image_block(str(img_path))
        self.assertEqual(block["type"], "image_url")
        self.assertTrue(block["image_url"]["url"].startswith("data:image/png;base64,"))


class _FakeAnthropicTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeAnthropicMessage:
    def __init__(self, text: str):
        self.content = [_FakeAnthropicTextBlock(text)]
        self.id = "fake_anthropic_id"


class _FakeOpenAIMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeOpenAIChoice:
    def __init__(self, content: str):
        self.message = _FakeOpenAIMessage(content)


class _FakeOpenAICompletion:
    def __init__(self, content: str):
        self.choices = [_FakeOpenAIChoice(content)]
        self.id = "fake_openai_id"


class TestAnthropicProviderImageBlock(unittest.TestCase):
    """Мок HTTP-клиента (self._client) — проверяем СТРУКТУРУ запроса, БЕЗ сети."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.img_path = str(_make_png(Path(self._tmpdir.name) / "stim.png"))

    def test_generate_with_image_sends_image_block(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            provider = generate.AnthropicProvider(model="claude-test")
        fake_client = mock.MagicMock()
        fake_client.messages.create.return_value = _FakeAnthropicMessage("описание изображения")
        provider._client = fake_client

        result = provider.generate("system", "user prompt", 0.5, image_path=self.img_path)

        self.assertEqual(result.text, "описание изображения")
        _, kwargs = fake_client.messages.create.call_args
        content = kwargs["messages"][0]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "image")
        self.assertEqual(content[1], {"type": "text", "text": "user prompt"})

    def test_generate_without_image_sends_plain_string_content(self):
        """Регрессия: без image_path content — простая строка, как в v1.3."""
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            provider = generate.AnthropicProvider(model="claude-test")
        fake_client = mock.MagicMock()
        fake_client.messages.create.return_value = _FakeAnthropicMessage("ответ")
        provider._client = fake_client

        provider.generate("system", "user prompt", 0.5)

        _, kwargs = fake_client.messages.create.call_args
        self.assertEqual(kwargs["messages"][0]["content"], "user prompt")


class TestOpenAIProviderImageBlock(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.img_path = str(_make_png(Path(self._tmpdir.name) / "stim.png"))

    def test_generate_with_image_sends_image_url_block(self):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            provider = generate.OpenAIProvider(model="gpt-test")
        fake_client = mock.MagicMock()
        fake_client.chat.completions.create.return_value = _FakeOpenAICompletion("описание изображения")
        provider._client = fake_client

        result = provider.generate("system", "user prompt", 0.5, image_path=self.img_path)

        self.assertEqual(result.text, "описание изображения")
        _, kwargs = fake_client.chat.completions.create.call_args
        user_content = kwargs["messages"][1]["content"]
        self.assertIsInstance(user_content, list)
        self.assertEqual(user_content[0], {"type": "text", "text": "user prompt"})
        self.assertEqual(user_content[1]["type"], "image_url")

    def test_generate_without_image_sends_plain_string_content(self):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            provider = generate.OpenAIProvider(model="gpt-test")
        fake_client = mock.MagicMock()
        fake_client.chat.completions.create.return_value = _FakeOpenAICompletion("ответ")
        provider._client = fake_client

        provider.generate("system", "user prompt", 0.5)

        _, kwargs = fake_client.chat.completions.create.call_args
        self.assertEqual(kwargs["messages"][1]["content"], "user prompt")


class TestGigaChatProviderVisualRefusal(unittest.TestCase):
    def test_image_path_raises_honest_provider_error_before_not_implemented(self):
        with mock.patch.dict("os.environ", {"GIGACHAT_AUTH_KEY": "test-auth-key"}):
            provider = generate.GigaChatProvider()
        with self.assertRaises(generate.ProviderError) as ctx:
            provider.generate("system", "user", 0.5, image_path="/tmp/x.png")
        self.assertIn("визуальные стимулы", str(ctx.exception).lower())

    def test_text_only_still_raises_not_implemented(self):
        """Регрессия: без image_path поведение — прежний NotImplementedError (TODO текстового REST)."""
        with mock.patch.dict("os.environ", {"GIGACHAT_AUTH_KEY": "test-auth-key"}):
            provider = generate.GigaChatProvider()
        with self.assertRaises(NotImplementedError):
            provider.generate("system", "user", 0.5)


class _FakeVisionProvider(generate.BaseProvider):
    name = "fake_vision"

    def __init__(self, canned_text: str = "На макете видна кружка и надпись «Бренд»."):
        self.canned_text = canned_text
        self.calls: list[dict] = []

    def generate(self, system_prompt, user_prompt, temperature, image_path=None):
        self.calls.append(
            {"system_prompt": system_prompt, "user_prompt": user_prompt, "temperature": temperature, "image_path": image_path}
        )
        return generate.GenerationResult(text=self.canned_text, model="fake-vision-model", request_id="req-1")


class TestVisionCheckApiHelpers(unittest.TestCase):
    def test_describe_image_via_provider_uses_neutral_system_prompt(self):
        provider = _FakeVisionProvider()
        text = generate.describe_image_via_provider(provider, "/tmp/img.png")
        self.assertEqual(text, provider.canned_text)
        self.assertEqual(len(provider.calls), 1)
        call = provider.calls[0]
        self.assertEqual(call["system_prompt"], generate.VISION_CHECK_SYSTEM_PROMPT)
        self.assertNotIn("Ты отвечаешь как живой человек", call["system_prompt"])
        self.assertEqual(call["image_path"], "/tmp/img.png")

    def test_fill_vision_check_descriptions_fills_only_empty_ones(self):
        provider = _FakeVisionProvider()
        vc = {
            "images": [
                {"image_path": "/tmp/a.png", "description": ""},
                {"image_path": "/tmp/b.png", "description": "уже описано вручную"},
            ]
        }
        generate.fill_vision_check_descriptions(vc, provider)
        self.assertEqual(vc["images"][0]["description"], provider.canned_text)
        self.assertEqual(vc["images"][1]["description"], "уже описано вручную")
        self.assertEqual(len(provider.calls), 1)  # только ПУСТОЕ description вызвало провайдера

    def test_fill_vision_check_descriptions_sets_api_vision_source(self):
        """v1.4 fix (docs/review_v1.4.md находка №3, references/methodology.md §6.4):
        API-режим — единственная точка кода, где вызов провайдера структурно
        гарантирует, что описание получено реальным просмотром пикселей (не
        самоотчётом) — vision_check_source обязан фиксировать именно это,
        ТОЛЬКО для образов, реально заполненных этим вызовом."""
        provider = _FakeVisionProvider()
        vc = {
            "images": [
                {"image_path": "/tmp/a.png", "description": "", "vision_check_source": None},
                {
                    "image_path": "/tmp/b.png",
                    "description": "уже описано вручную",
                    "vision_check_source": "agent_self_reported",
                },
            ]
        }
        generate.fill_vision_check_descriptions(vc, provider)
        self.assertEqual(vc["images"][0]["vision_check_source"], "api_vision")
        # Уже заполненное (agent-режим) описание НЕ трогается этим вызовом вообще —
        # значит и его vision_check_source не должен быть переписан.
        self.assertEqual(vc["images"][1]["vision_check_source"], "agent_self_reported")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""
generate.py — мультипровайдерная генерация свободных ответов синтетических респондентов.

Реализует spec_synthetic-panel_v1.md §5 + spec_synthetic-panel_v1.3.md §1.1 (карточка
персоны, фикс дефекта Д1). Точка входа для внешних вызовов (run_study.py) —
функция generate_responses(). Остальное — вспомогательные строительные блоки.

Провайдеры (config.yaml: llm.provider):
    agent      — дефолт. НЕ вызывает никакую LLM: пишет responses_todo.jsonl + AGENT_TASK.md,
                 которые заполняет агент/человек. temperature_control = False (см. spec §5).
    anthropic  — прямые вызовы Anthropic Messages API (ANTHROPIC_API_KEY из env).
    openai     — прямые вызовы OpenAI Chat Completions API (OPENAI_API_KEY из env).
    gigachat   — REST-каркас (OAuth) с TODO для интегратора: интерфейс полный
                 (BaseProvider.generate), сам HTTP-обмен не реализован (нужен корпоративный
                 контур/сертификат, см. research/2026-07-08/06_tech_implementation.md, В.3).

Промпт (§5, СТРОГО): системная часть описывает персону КАРТОЧКОЙ (build_persona_card,
контракт формата — см. ниже) и требует свободный текст от первого лица без
chain-of-thought; задание — стимул + question ИЗ ШКАЛЫ anchors_ru.yaml (ТОЛЬКО
формулировка вопроса). Якорные фразы (anchor_sets/phrases) НИКОГДА не передаются
в build_system_prompt/build_task_prompt — этот модуль их даже не импортирует из ssr_core,
получая `question` уже готовой строкой от вызывающего кода (run_study.py), что делает
утечку шкалы структурно исключённой (нечем утечь — тексту анкоров сюда просто неоткуда взяться).

КАРТОЧКА ПЕРСОНЫ (§1.1 v1.3, контракт формата — фикс Д1: раньше в промпт попадали только
возраст/город/доход/первый формат/одна фраза языка, а description/мотивация/барьер/оси/
поводы/каналы сегмента полностью игнорировались). build_persona_card(profile, segment)
собирает СТРУКТУРИРОВАННУЮ карточку из ВСЕХ полей сегмента, какие реально присутствуют в
его YAML, с МЯГКОЙ ДЕГРАДАЦИЕЙ: поле отсутствует -> соответствующая строка карточки просто
не пишется, функция никогда не падает на неполном/черновом segment YAML. Строки карточки
(в этом порядке; любая, кроме первой, может отсутствовать):
    1. "Профиль: ..."     — джиттер: пол (если задан persona_jitter.gender — см. jitter_persona),
                            возраст, город, доход + имя сегмента. ВСЕГДА присутствует.
    2. "Кто это: ..."     — сжатое (см. _compress_text) segment.description.
    3. "Главное: ..."     — мотивация/барьер, ТОЛЬКО если сегмент явно задаёт строковые поля
                            segment['motivation']/segment['barrier'] (schema-задел на будущее:
                            ни один panel/segments/**/*.yaml на 2026-07-18 их не задаёт — этот
                            смысл обычно уже есть прозой в description выше; если задано только
                            одно из двух полей — пишется только оно).
    4. "Особенности: ..." — все пары segment.axes ("читаемое имя оси — значение"; имя оси
                            переводится словарём _AXIS_LABEL_RU, см. ниже).
    5. "Поведение: ..."   — segment.behavior: forматы/поводы/каналы (списки, через "; ") +
                            price_sensitivity (сжато). Часть, которой нет в YAML, просто не
                            добавляется; если нет ни одной части — строки нет вовсе.
    6. "Опыт категории: ..." — сжатый (нейтральный по тону) segment.brands_context.
    7. "Говорит в духе: ..."  — 1-2 фразы из segment.language (profile['language_flavors'],
                            выбраны детерминированно от seed в jitter_persona).
Признаки согласованы: карточка — ДЕТЕРМИНИРОВАННАЯ сборка из ОДНОГО и того же
джиттер-профиля (jitter_persona) и одного и того же segment-словаря — один респондент
получает ОДНУ карточку, переиспользуемую для ВСЕХ его стимулов/сэмплов (см. build_tasks) —
связный человек, а не независимо подброшенные кости по каждому полю.

Персона-джиттер: детерминирован от (config.report.seed, segment_id, respondent_idx) —
см. jitter_persona(). Один и тот же прогон (тот же seed) с тем же study.yaml всегда даёт
одни и те же профили персон — воспроизводимость (§0.5).

Схема responses_todo.jsonl (agent-режим, ДО заполнения) — одна строка JSON на задачу:
    rid            str   уникальный id ответа, "<segment>__<stimulus_id>__<respondent_idx>__<sample_idx>"
    segment        str   id сегмента (как в panel/segments/<id>.yaml)
    persona        str   ПОЛНАЯ карточка персоны (build_persona_card) — МНОГОСТРОЧНАЯ строка
                         (JSONL это допускает: одна строка ФАЙЛА = один JSON-объект; JSON
                         внутри объекта хранит переводы строк как \n, это не нарушает формат).
                         Хранится целиком для аудита (spec §1.1).
    stimulus_id    str
    stimulus_text  str   может быть пустой строкой для image-only стимула (см. ниже)
    image_path     str | null   НОВОЕ v1.4 (spec_synthetic-panel_v1.4.md §1.1/1.3): абсолютный
                         путь к файлу изображения стимула (уже разрешён и провалидирован
                         run_study.py::validate_and_resolve_stimuli), null для чисто текстовых
                         стимулов. Agent-режим: ведущая модель обязана прочитать (Read) файл по
                         этому пути ПЕРЕД тем как писать ответ персоны для строки — см.
                         AGENT_TASK_TEMPLATE, раздел "Визуальные стимулы".
    label          str | null   короткая подпись стимула (обязательна в study.yaml для
                         image-only стимулов — без неё нечем подписать таблицу в отчёте);
                         null, если не задана (текстовый/смешанный стимул без явной подписи).
    question       str   ТОЛЬКО формулировка вопроса шкалы, без якорных фраз
    respondent_idx int   1..respondents_per_segment
    sample_idx     int   1..samples_per_respondent

Схема responses.jsonl (финальная, ВСЕ режимы) — те же поля ПЛЮС:
    text           str   свободный текст ответа персоны
    provider       str   "agent" | "anthropic" | "openai" | "gigachat"
    model          str | null   dated model id (null для agent-режима)
    request_id     str | null   id запроса у API-провайдера (null для agent-режима)
    generated_at   str   ISO8601 UTC timestamp

В agent-режиме responses.jsonl создаёт сам агент/человек на основе responses_todo.jsonl
(добавляя text/provider/model/request_id/generated_at) — см. AGENT_TASK.md, который
пишет write_agent_mode().

ВИЗУАЛЬНЫЕ СТИМУЛЫ (spec_synthetic-panel_v1.4.md §1.3, Модуль 1). Промпт задания
(build_task_prompt) меняет ТОЛЬКО формулировку "вот что тебе показывают" в зависимости от
наличия image_path/label — персона по-прежнему не видит якорных фраз, CoT запрещён, роль не
меняется (build_system_prompt/build_persona_card — БЕЗ изменений, персона одна и та же
что для текстовых, что для визуальных стимулов). API-режим: сам файл изображения уходит
ОТДЕЛЬНЫМ image-блоком в вызове провайдера (BaseProvider.generate(..., image_path=...)) —
см. build_anthropic_image_block/build_openai_image_block ниже; agent-режим передаёт путь
к файлу текстом в responses_todo.jsonl, файл читает (Read) ведущая модель сама. Проба
зрения (§1.2, "00_vision_check.yaml") — отдельная, БОЛЕЕ РАННЯЯ стадия, целиком в
run_study.py (описание изображения БЕЗ роли персоны — другой system prompt, см.
VISION_CHECK_SYSTEM_PROMPT/describe_image_via_provider ниже, вызываемые ИЗ run_study.py
ДО generate_responses).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Персона-джиттер
# ============================================================================


def _stable_seed(seed: int, *parts: str) -> int:
    """Детерминированный числовой seed из (seed, *parts) — стабилен между запусками/платформами."""
    h = hashlib.sha256(f"{seed}:{':'.join(str(p) for p in parts)}".encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def make_rng(seed: int, *parts: str) -> random.Random:
    return random.Random(_stable_seed(seed, *parts))


def jitter_persona(segment: dict, segment_id: str, respondent_idx: int, seed: int) -> dict:
    """
    Детерминированный от seed джиттер конкретного респондента внутри сегмента.

    Читает segment['persona_jitter'] = {age: [min, max], income_level: [...],
    city_tier: [...], gender: [...] (опционально — spec_synthetic-panel_v1.2.md,
    Модуль 2 п.1; НИ ОДИН panel/segments/**/*.yaml на 2026-07-18 это поле не задаёт)}
    (схема сегмента — spec §9, владелец файлов — сборщик панели [B3]). Один и тот же
    (seed, segment_id, respondent_idx) всегда даёт один и тот же профиль — воспроизводимость
    прогона (§0.5). Разумные дефолты подставляются, если сегмент не задаёт часть полей
    (не должно падать на неполном/черновом segment YAML).

    Порядок обращений к rng ВАЖЕН для обратной совместимости: age -> income -> city
    -> (gender, ТОЛЬКО если поле задано) -> language_flavors. Поскольку gender
    сегодня отсутствует у ВСЕХ существующих сегментов, эта ветка не потребляет rng
    вообще ни для одного из них — age/income/city любого существующего сегмента
    остаются побитово такими же, как до появления gender/language_flavors.
    """
    jitter = segment.get("persona_jitter") or {}
    rng = make_rng(seed, segment_id, str(respondent_idx))

    age_range = jitter.get("age") or [25, 55]
    age_lo, age_hi = int(age_range[0]), int(age_range[1])
    if age_hi < age_lo:
        age_lo, age_hi = age_hi, age_lo
    age = rng.randint(age_lo, age_hi)

    # РЕШЕНО [review v1.3, находка №5 MINOR]: дефолты — те же МАШИННЫЕ токены
    # (_INCOME_LEVEL_RU/_CITY_TIER_RU ключи), что и реальные значения из
    # persona_jitter сегментов, а НЕ уже готовый русский текст. До этой правки
    # дефолт был литералом "средний доход"/"крупный город" — _translate_jitter_token
    # не находил его в словаре (ключи словаря — "average"/"big_city") и на КАЖДОМ
    # респонденте сегмента без persona_jitter писал в лог ложный warning "токен
    # ... отсутствует в словаре перевода", хотя итоговый текст карточки был и
    # раньше корректен (случайное совпадение "уже по-русски"). Теперь дефолт
    # проходит ЧЕРЕЗ тот же путь перевода, что и обычные сегменты — единственный
    # путь получения русского текста, без обходной короткой цепочки.
    income_options = jitter.get("income_level") or ["average"]
    income = rng.choice(income_options)

    city_options = jitter.get("city_tier") or ["big_city"]
    city = rng.choice(city_options)

    gender_options = jitter.get("gender")
    gender = rng.choice(gender_options) if gender_options else None

    language_bank = segment.get("language") or []
    # 1-2 фразы (карточка персоны §1.1), без повторов, если банк даёт 2+ разных
    # варианта. rng.sample идёт ПОСЛЕДНИМ обращением к rng в этой функции — не
    # влияет на воспроизводимость age/income/city/gender выше.
    n_flavors = min(2, len(language_bank))
    language_flavors = rng.sample(language_bank, n_flavors) if n_flavors else []

    return {
        "age": age,
        "income_level": income,
        "city_tier": city,
        "gender": gender,
        "language_flavors": language_flavors,
        "segment_name": segment.get("name", segment_id),
    }


# Переводы токенов persona_jitter (income_level/city_tier/gender) в естественный русский.
# Схема сегментов (panel/segments/*.yaml, владелец [B3]) использует английские
# snake_case-токены как МАШИННЫЕ идентификаторы диапазона для детерминированного
# выбора в jitter_persona() — это нормально для данных. Но эти же значения идут
# ДАЛЬШЕ в промпт персоны и в поле `persona` JSONL, а там уже должен быть
# человекочитаемый русский текст (spec_synthetic-panel_v1.md §0.4: "Всё
# русскоязычное: якоря, промпты, сегменты, отчёты"; без перевода "mid_city"/
# "average" утекали дословно в русский системный промпт — см. docs/review_v1.md,
# находка №2). Словари закрытые и сверены со ВСЕМИ panel/segments/**/*.yaml
# на момент написания; неизвестный токен не роняет прогон — заменяется как есть
# с предупреждением в лог, чтобы новый сегмент не проходил тихо мимо перевода.
_INCOME_LEVEL_RU = {
    "below_average": "доход ниже среднего",
    "average": "средний доход",
    "above_average": "доход выше среднего",
    "high": "высокий доход",
}
_CITY_TIER_RU = {
    "million_plus": "город-миллионник",
    "big_city": "крупный город",
    "mid_city": "город среднего размера",
    "small_town_rural": "малый город или село",
}
_GENDER_RU = {
    "ж": "женщина",
    "м": "мужчина",
    "жен": "женщина",
    "муж": "мужчина",
    "женский": "женщина",
    "мужской": "мужчина",
}


def _translate_jitter_token(token: str, mapping: dict[str, str], field_name: str) -> str:
    if token in mapping:
        return mapping[token]
    logger.warning(
        "generate.py: токен %s=%r отсутствует в словаре перевода на русский — "
        "используется как есть (проверьте panel/segments/*.yaml на новые значения "
        "persona_jitter и добавьте перевод в generate.py).",
        field_name,
        token,
    )
    return token


# Переводы КЛЮЧЕЙ segment['axes'] (факторные оси) в читаемую русскую фразу — часть
# карточки персоны (§1.1 v1.3, строка 4 "Особенности"). Ключи осей — snake_case
# английские идентификаторы (см. panel/segments/**/*.yaml), а вот ЗНАЧЕНИЯ осей уже
# по-русски словами ("высокая"/"средняя"/...) прямо в схеме сегмента. Без перевода
# ИМЕНИ оси в промпт утекал бы английский идентификатор — ровно тот же класс
# проблемы, что и с income_level/city_tier токенами выше (см. docs/review_v1.md,
# находка №2). Словарь сверен со ВСЕМИ panel/segments/**/*.yaml на 2026-07-18
# (22 уникальных ключа на момент написания); НЕИЗВЕСТНЫЙ ключ (новый сегмент завёл
# новую ось) не роняет прогон — _axis_label() гуманизирует его (замена "_" на
# пробел) и пишет warning в лог, чтобы перевод могли добавить при следующей правке.
_AXIS_LABEL_RU = {
    "coffee_expertise": "экспертность в кофе",
    "coffee_harm_reduction": "восприятие вреда от кофе",
    "dessert_treat": "восприятие кофе как десерта",
    "effect_energy": "важность бодрящего эффекта",
    "flavor_experimentation": "готовность экспериментировать со вкусами",
    "hair_loss_anxiety": "тревога из-за выпадения волос",
    "ingredient_literacy": "насколько разбирается в составе средств",
    "irritation_sensitivity": "чувствительность кожи к раздражению",
    "mood_atmosphere": "важность настроения и атмосферы",
    "naturalness": "важность натуральности",
    "patience_horizon": "готовность ждать результат",
    "postacne_focus": "фокус на постакне и ровном тоне кожи",
    "premium_quality": "тяга к премиальности",
    "price_sensitivity": "чувствительность к цене",
    "quick_result_expectation": "ожидание быстрого результата",
    "routine_readiness": "готовность соблюдать курс/рутину",
    "self_treatment_vs_doctor": "склонность лечиться самостоятельно, а не у врача",
    "shame_stigma": "стыд и стигма темы",
    "soc_pressure": "социальное давление",
    "topic_openness": "открытость обсуждать тему",
    "treatment_distrust": "недоверие к средствам лечения",
    "trust_in_active_ingredients": "доверие к действующим веществам",
}


def _axis_label(axis_key: str) -> str:
    if axis_key in _AXIS_LABEL_RU:
        return _AXIS_LABEL_RU[axis_key]
    logger.warning(
        "generate.py: ось %r отсутствует в словаре _AXIS_LABEL_RU — используется "
        "гуманизированный fallback (замена '_' на пробел). Если это не опечатка, "
        "добавьте перевод оси в generate.py.",
        axis_key,
    )
    return axis_key.replace("_", " ")


def _compress_text(text: str, max_sentences: int = 2, max_chars: int = 300) -> str:
    """
    Сжимает длинный абзац YAML-поля (description/brands_context/price_sensitivity
    и т.п.) для карточки персоны, которая идёт в ПРОМПТ — не путать с
    report.py::truncate_label (та функция обрезает текст СТИМУЛА для табличной
    ячейки отчёта, другой модуль и другое назначение).

    Берёт первые предложения (граница — ".", "!", "?" + пробел), ДОБАВЛЯЯ их по
    одному, пока не набрано max_sentences ИЛИ следующее предложение не превысило
    бы max_chars (в этом случае просто останавливается, не обрезая предложение
    на середине слова) — всегда сохраняется минимум первое предложение целиком.
    Если даже ОДНО первое предложение длиннее max_chars — обрезает по границе
    слова (не байт-в-байт по символам, чтобы не отдавать оборванный огрызок
    слова вроде "не стол…") и добавляет "…". Пустой/отсутствующий текст -> пустая
    строка (вызывающий код такую строку в карточку не добавляет, см.
    build_persona_card).
    """
    collapsed = " ".join(text.split())
    if not collapsed:
        return ""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", collapsed) if s] or [collapsed]

    kept = sentences[0]
    for extra in sentences[1:max_sentences]:
        candidate = f"{kept} {extra}"
        if len(candidate) > max_chars:
            break
        kept = candidate

    if len(kept) > max_chars:
        cut = kept[: max_chars - 1]
        last_space = cut.rfind(" ")
        if last_space > max_chars // 2:  # не обрезать до огрызка в пару символов
            cut = cut[:last_space]
        if cut.count('"') % 2 == 1:
            # оборванная НЕЗАКРЫТАЯ кавычка внутри вырезанного текста (например,
            # обрезали ровно на открывающей «"маленькой радости"») — обрубаем от
            # неё же, чтобы не оставлять висящую кавычку без пары в середине фразы.
            cut = cut[: cut.rfind('"')]
        kept = cut.rstrip(' ,;:—-"') + "…"
    return kept


def _as_clause(text: str) -> str:
    """
    Готовит компрессированный фрагмент (_compress_text) для встраивания как
    ВНУТРЕННЕЙ клаузы строки карточки, которая сама получит один финальный "."
    в конце (см. "Главное"/"Поведение" ниже) — снимает конечную точку/!/?, чтобы
    не получалось "...врача.; барьер" или "...компонентах.." (двойная пунктуация).
    Многоточие "…" (уже signal сжатия) НЕ снимается — двоеточие/точка-с-запятой
    в исходном тексте тоже не трогаются, только терминальные .!? одним символом.
    """
    return text[:-1] if text and text[-1] in ".!?" else text


def build_persona_card(profile: dict, segment: dict) -> str:
    """
    Структурированная карточка смоделированного профиля (§1.1 v1.3, фикс Д1) —
    контракт формата целиком описан в докстринге МОДУЛЯ выше ("КАРТОЧКА ПЕРСОНЫ").
    Мягкая деградация: поле сегмента отсутствует -> соответствующая строка карточки
    просто не пишется, функция никогда не бросает исключение из-за неполного YAML.

    Результат идёт И в build_system_prompt (ниже), И в поле `persona` JSONL (для
    аудита, целиком, см. build_tasks/write_agent_mode) — это ОДИН и тот же текст.
    """
    lines: list[str] = []

    city_ru = _translate_jitter_token(profile["city_tier"], _CITY_TIER_RU, "city_tier")
    income_ru = _translate_jitter_token(profile["income_level"], _INCOME_LEVEL_RU, "income_level")
    gender_token = profile.get("gender")
    gender_prefix = (
        f"{_translate_jitter_token(gender_token, _GENDER_RU, 'gender')}, " if gender_token else ""
    )
    segment_name = segment.get("name") or profile.get("segment_name") or ""
    lines.append(
        f"Профиль: {gender_prefix}{profile['age']} лет, {city_ru}, {income_ru}, "
        f"сегмент «{segment_name}»."
    )

    description = (segment.get("description") or "").strip()
    if description:
        lines.append(f"Кто это: {_compress_text(description, max_sentences=2, max_chars=320)}")

    # Мотивация/барьер — schema-задел на будущее (см. докстринг модуля, строка 3);
    # мягкая деградация означает, что СЕЙЧАС эта строка не пишется ни для одного
    # существующего сегмента (ни один panel/segments/**/*.yaml их не задаёт) —
    # это ожидаемо, а не баг: смысл обычно уже есть прозой в description выше.
    motivation = (segment.get("motivation") or "").strip()
    barrier = (segment.get("barrier") or "").strip()
    if motivation or barrier:
        parts = []
        if motivation:
            parts.append(f"мотивация — {_as_clause(_compress_text(motivation, max_sentences=1, max_chars=200))}")
        if barrier:
            parts.append(f"барьер — {_as_clause(_compress_text(barrier, max_sentences=1, max_chars=200))}")
        lines.append("Главное: " + "; ".join(parts) + ".")

    axes = segment.get("axes")
    if isinstance(axes, dict) and axes:
        axis_phrases = [f"{_axis_label(str(k))} — {v}" for k, v in axes.items()]
        lines.append("Особенности: " + "; ".join(axis_phrases) + ".")

    behavior = segment.get("behavior") or {}
    behavior_parts = []
    formats = behavior.get("formats")
    if isinstance(formats, list) and formats:
        behavior_parts.append("форматы — " + "; ".join(str(x) for x in formats))
    occasions = behavior.get("occasions")
    if isinstance(occasions, list) and occasions:
        behavior_parts.append("повод — " + "; ".join(str(x) for x in occasions))
    channels = behavior.get("channels")
    if isinstance(channels, list) and channels:
        behavior_parts.append("каналы — " + "; ".join(str(x) for x in channels))
    price_sensitivity = (behavior.get("price_sensitivity") or "").strip()
    if price_sensitivity:
        behavior_parts.append(
            "к цене — " + _as_clause(_compress_text(price_sensitivity, max_sentences=1, max_chars=200))
        )
    if behavior_parts:
        lines.append("Поведение: " + "; ".join(behavior_parts) + ".")

    brands_context = (segment.get("brands_context") or "").strip()
    if brands_context:
        lines.append(
            "Опыт категории: " + _compress_text(brands_context, max_sentences=2, max_chars=260)
        )

    language_flavors = profile.get("language_flavors") or []
    if language_flavors:
        quoted = "; ".join(f"«{p}»" for p in language_flavors)
        lines.append(f"Говорит в духе: {quoted}")

    return "\n".join(lines)


# ============================================================================
# Промпт-шаблон (§5, §1.1) — системная часть и задание
# ============================================================================


def build_system_prompt(profile: dict, segment: dict) -> str:
    """
    Системная часть промпта респондента (§5, §1.1). НЕ содержит и не может
    содержать якорные фразы шкалы — эта функция вообще не получает anchor_sets на
    вход, а карточка персоны (build_persona_card) строится только из полей
    сегмента и джиттер-профиля, которым тоже неоткуда взять анкоры.
    """
    card = build_persona_card(profile, segment)
    return (
        "Ты отвечаешь как живой человек. Вот твой профиль:\n"
        f"{card}\n\n"
        "Это согласованное целое, один конкретный человек, а не список случайных "
        "фактов — отвечай, оставаясь им. Отвечай от первого лица, разговорно, 2-5 "
        "предложений. "
        "Не упоминай, что ты ИИ, языковая модель или ассистент. "
        "Не рассуждай пошагово и не объясняй ход мыслей — сразу дай живую, естественную "
        "реакцию, как будто тебя спросили об этом в короткой беседе. "
        "Если в профиле есть строка «Говорит в духе» — иногда, где уместно, можно "
        "оттолкнуться от интонации этих фраз, но не копируй их дословно в каждом ответе."
    )


def build_task_prompt(
    stimulus_text: str,
    question: str,
    *,
    image_path: Optional[str] = None,
    label: Optional[str] = None,
) -> str:
    """
    Задание (§5, расширено spec_synthetic-panel_v1.4.md §1.3 — визуальные стимулы):
    стимул (+ изображение, если задано) + question. `question` — ТОЛЬКО формулировка
    вопроса шкалы из anchors_ru.yaml (поле `question`), передаётся вызывающим кодом
    (run_study.py) как простая строка. Якорные фразы (anchor_sets/phrases) сюда
    НИКОГДА не должны попадать — вызывающий код обязан передавать question, а не
    что-либо ещё из anchors_ru.yaml.

    image_path/label — НОВОЕ v1.4, оба по умолчанию None (ОБРАТНАЯ СОВМЕСТИМОСТЬ:
    старый вызов build_task_prompt(text, question) без этих аргументов даёт БАЙТ-В-БАЙТ
    тот же текст, что и до v1.4 — ветка "иначе" ниже не менялась). image_path сам файл
    в текст промпта НЕ встраивает (это делает BaseProvider.generate через отдельный
    image-блок, см. generate_responses) — здесь только формулировка "вот что показывают"
    меняется на явное упоминание приложенного макета/изображения:
      - image + непустой text (смешанный стимул) — текст цитируется, как и раньше, но
        с явной пометкой "макет/изображение приложено";
      - image без text (image-only, label ОБЯЗАТЕЛЕН в study.yaml — см.
        run_study.py::validate_and_resolve_stimuli) — цитируется label вместо text;
      - ни один из случаев выше — классический текстовый стимул, форматирование БЕЗ
        изменений с v1.3.
    """
    has_text = bool((stimulus_text or "").strip())
    if image_path and has_text:
        shown = f"Вот макет/изображение (приложено файлом), с текстом на нём или рядом: «{stimulus_text}»"
    elif image_path:
        caption = (label or "").strip() or "(без подписи)"
        shown = f"Вот макет/изображение (приложено файлом), подпись: «{caption}»"
    else:
        shown = f"Вот что тебе показывают:\n«{stimulus_text}»"
    return (
        f"{shown}\n\n"
        f"{question}\n\n"
        "Ответь свободным текстом, своими словами, БЕЗ числовой оценки и БЕЗ баллов по шкале."
    )


# ============================================================================
# Задачи генерации
# ============================================================================


@dataclass
class ResponseTask:
    rid: str
    segment: str
    persona: str
    stimulus_id: str
    stimulus_text: str
    question: str
    respondent_idx: int
    sample_idx: int
    system_prompt: str  # нужен только API-провайдерам; в responses_todo.jsonl не пишется
    # НОВОЕ v1.4 (§1.1/1.3 spec_synthetic-panel_v1.4.md) — оба по умолчанию None,
    # т.е. полностью обратно совместимы со study.yaml без визуальных стимулов.
    image_path: Optional[str] = None  # абсолютный путь к файлу (уже разрешён run_study.py)
    label: Optional[str] = None  # короткая подпись стимула (обязательна для image-only)


def build_tasks(
    study: dict,
    segments: dict[str, dict],
    question: str,
    seed: int,
    samples_per_respondent: int,
) -> list[ResponseTask]:
    """Строит полный список задач (сегмент × респондент × стимул × сэмпл), детерминированно от seed."""
    tasks: list[ResponseTask] = []
    respondents_per_segment = int(study["respondents_per_segment"])

    for segment_id in study["segments"]:
        segment = segments[segment_id]
        for respondent_idx in range(1, respondents_per_segment + 1):
            profile = jitter_persona(segment, segment_id, respondent_idx, seed)
            persona_card = build_persona_card(profile, segment)
            system_prompt = build_system_prompt(profile, segment)
            for stimulus in study["stimuli"]:
                # v1.4 §1.1: text может отсутствовать у image-only стимула (только
                # image+label обязательны) — .get с дефолтом "", не stimulus["text"].
                stimulus_text = stimulus.get("text", "") or ""
                image_path = stimulus.get("image")
                label = stimulus.get("label")
                for sample_idx in range(1, samples_per_respondent + 1):
                    rid = f"{segment_id}__{stimulus['id']}__{respondent_idx:03d}__{sample_idx}"
                    tasks.append(
                        ResponseTask(
                            rid=rid,
                            segment=segment_id,
                            persona=persona_card,
                            stimulus_id=stimulus["id"],
                            stimulus_text=stimulus_text,
                            question=question,
                            respondent_idx=respondent_idx,
                            sample_idx=sample_idx,
                            system_prompt=system_prompt,
                            image_path=image_path,
                            label=label,
                        )
                    )
    return tasks


# ============================================================================
# Провайдеры API
# ============================================================================


class ProviderError(RuntimeError):
    """Ошибка провайдера генерации: отсутствие ключа/пакета, исчерпанные ретраи и т.п."""


# Визуальные стимулы (spec_synthetic-panel_v1.4.md §1.1/1.3) — image-блоки для
# anthropic/openai. Чистые функции (файл -> base64/mime), НИКАКОГО сетевого вызова —
# тестируются напрямую, без API-ключей и без мока сети (см. test_generate.py).
_IMAGE_MIME_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _image_mime_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext not in _IMAGE_MIME_BY_EXT:
        raise ProviderError(
            f"Неподдерживаемый формат изображения: {ext!r} (файл {path}). "
            f"Разрешены: {sorted(_IMAGE_MIME_BY_EXT)} (см. run_study.py::IMAGE_EXTENSIONS — "
            "формат уже должен был быть отсеян валидацией study.yaml, эта проверка — "
            "защита от прямого вызова провайдера в обход run_study.py)."
        )
    return _IMAGE_MIME_BY_EXT[ext]


def encode_image_base64(image_path: str) -> tuple[str, str]:
    """
    Возвращает (base64_data, mime_type) для файла изображения стимула — общая
    функция для anthropic/openai image-блоков ниже. Чистое чтение файла с диска +
    кодирование, БЕЗ сетевого вызова — image_path уже абсолютный и провалидирован
    run_study.py::validate_and_resolve_stimuli к моменту вызова.
    """
    path = Path(image_path)
    mime = _image_mime_type(path)
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return data, mime


def build_anthropic_image_block(image_path: str) -> dict:
    """Anthropic Messages API — блок изображения (base64), см. §1.3."""
    data, mime = encode_image_base64(image_path)
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}


def build_openai_image_block(image_path: str) -> dict:
    """OpenAI Chat Completions API — блок изображения (data: URL, base64), см. §1.3."""
    data, mime = encode_image_base64(image_path)
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}


@dataclass
class GenerationResult:
    text: str
    model: str
    request_id: Optional[str] = None


class BaseProvider(ABC):
    name: str = "base"

    @abstractmethod
    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        image_path: Optional[str] = None,
    ) -> GenerationResult:
        """
        image_path — НОВОЕ v1.4 (§1.3), по умолчанию None (текстовый стимул,
        поведение БЕЗ изменений с v1.3). Если задан — реализация обязана
        приложить файл отдельным image-блоком (см. build_anthropic_image_block/
        build_openai_image_block), а НЕ встраивать путь текстом в user_prompt.
        """
        raise NotImplementedError


class AnthropicProvider(BaseProvider):
    """Прямые вызовы Anthropic Messages API. Ключ — ANTHROPIC_API_KEY (env)."""

    name = "anthropic"

    def __init__(self, model: str, max_tokens: int = 300, max_retries: int = 4):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ProviderError(
                "ANTHROPIC_API_KEY не задан в окружении — provider: anthropic недоступен."
            )
        try:
            import anthropic  # лениво: тяжёлая опциональная зависимость
        except ImportError as exc:
            raise ProviderError(
                "Пакет 'anthropic' не установлен. Установите: pip install anthropic"
            ) from exc
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.max_retries = max_retries

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        image_path: Optional[str] = None,
    ) -> GenerationResult:
        # §1.3: image_path задан -> content становится списком блоков (изображение
        # + текст задания), иначе content — простая строка, как в v1.3 (без изменений).
        content = (
            [build_anthropic_image_block(image_path), {"type": "text", "text": user_prompt}]
            if image_path
            else user_prompt
        )
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    temperature=temperature,
                    system=system_prompt,
                    messages=[{"role": "user", "content": content}],
                )
                text = "".join(
                    block.text for block in resp.content if getattr(block, "type", None) == "text"
                )
                request_id = getattr(resp, "_request_id", None) or getattr(resp, "id", None)
                logger.info("anthropic request_id=%s model=%s", request_id, self.model)
                return GenerationResult(text=text.strip(), model=self.model, request_id=request_id)
            except Exception as exc:  # ретраим любые транзиентные ошибки SDK
                last_err = exc
                wait = min(2**attempt, 20)
                logger.warning(
                    "anthropic: попытка %d/%d не удалась (%s), повтор через %ss",
                    attempt + 1, self.max_retries, exc, wait,
                )
                time.sleep(wait)
        raise ProviderError(f"anthropic: все {self.max_retries} попыток исчерпаны: {last_err}")


class OpenAIProvider(BaseProvider):
    """Прямые вызовы OpenAI Chat Completions API. Ключ — OPENAI_API_KEY (env)."""

    name = "openai"

    def __init__(self, model: str, max_tokens: int = 300, max_retries: int = 4):
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ProviderError("OPENAI_API_KEY не задан в окружении — provider: openai недоступен.")
        try:
            import openai  # лениво
        except ImportError as exc:
            raise ProviderError(
                "Пакет 'openai' не установлен. Установите: pip install openai"
            ) from exc
        self._client = openai.OpenAI(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.max_retries = max_retries

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        image_path: Optional[str] = None,
    ) -> GenerationResult:
        # §1.3: та же логика, что у AnthropicProvider выше — image_path задан ->
        # user-контент становится списком блоков (текст + image_url), иначе
        # простая строка (без изменений с v1.3).
        user_content = (
            [{"type": "text", "text": user_prompt}, build_openai_image_block(image_path)]
            if image_path
            else user_prompt
        )
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    temperature=temperature,
                    max_tokens=self.max_tokens,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                )
                text = resp.choices[0].message.content or ""
                request_id = getattr(resp, "id", None)
                logger.info("openai request_id=%s model=%s", request_id, self.model)
                return GenerationResult(text=text.strip(), model=self.model, request_id=request_id)
            except Exception as exc:
                last_err = exc
                wait = min(2**attempt, 20)
                logger.warning(
                    "openai: попытка %d/%d не удалась (%s), повтор через %ss",
                    attempt + 1, self.max_retries, exc, wait,
                )
                time.sleep(wait)
        raise ProviderError(f"openai: все {self.max_retries} попыток исчерпаны: {last_err}")


class GigaChatProvider(BaseProvider):
    """
    REST-каркас GigaChat API (Sber, OAuth). Интерфейс полный и совместим с BaseProvider —
    run_study.py/generate.py могут переключиться на этот провайдер без изменений в
    остальном коде, как только TODO ниже будут реализованы интегратором с доступом к
    контуру ГигаЧата (сертификат НУЦ Минцифры для TLS, коммерческий Authorization Key —
    см. research/2026-07-08/06_tech_implementation.md, часть В.3).

    Сейчас оба метода (_ensure_token, generate) намеренно бросают NotImplementedError —
    это КАРКАС, не заглушка "тихо возвращает мусор": вызывающий код должен явно увидеть,
    что провайдер не реализован, а не получить правдоподобный, но пустой результат.
    """

    name = "gigachat"

    AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

    def __init__(self, model: str = "GigaChat", max_tokens: int = 300, max_retries: int = 4, verify_ssl: bool = True):
        self.auth_key = os.environ.get("GIGACHAT_AUTH_KEY")
        if not self.auth_key:
            raise ProviderError("GIGACHAT_AUTH_KEY не задан в окружении — provider: gigachat недоступен.")
        self.model = model
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.verify_ssl = verify_ssl
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    def _ensure_token(self) -> str:
        """
        TODO(интегратор): OAuth-обмен Authorization Key -> access_token.
        POST {AUTH_URL}, заголовки: 'Authorization: Basic {auth_key}', 'RqUID': str(uuid4()),
        'Content-Type: application/x-www-form-urlencoded'; тело: 'scope=GIGACHAT_API_PERS'
        (или GIGACHAT_API_CORP — уточнить тариф у интегратора). access_token живёт ~30 минут
        (кэшировать в self._access_token/_token_expires_at). В части окружений нужен
        сертификат НУЦ Минцифры для verify_ssl=True (self.verify_ssl — уже параметризовано).
        """
        raise NotImplementedError(
            "GigaChatProvider._ensure_token: OAuth-обмен не реализован (TODO для интегратора). "
            "См. docstring класса и research/2026-07-08/06_tech_implementation.md (часть В.3)."
        )

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        image_path: Optional[str] = None,
    ) -> GenerationResult:
        """
        TODO(интегратор): после реализации _ensure_token() — POST {CHAT_URL} с телом
        {"model": self.model, "temperature": temperature, "messages": [
            {"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}
        ]}, заголовок 'Authorization: Bearer {token}'. Ретраи/бэкофф — по образцу
        AnthropicProvider.generate()/OpenAIProvider.generate() выше.

        image_path (spec_synthetic-panel_v1.4.md §1.3): визуальные стимулы ЭТИМ
        провайдером пока НЕ поддержаны — честная, отдельная от TODO выше ошибка
        (ProviderError, не NotImplementedError) ДО попытки любого REST-вызова, чтобы
        run_study.py мог отличить "визуальные стимулы не поддержаны" от "провайдер
        вообще не реализован" (тот же паттерн API, что и текстовые вызовы — просто
        текст стимула ЕЩЁ и не реализован, а изображение НЕ БУДЕТ поддержано этим
        провайдером даже после реализации TODO выше, пока GigaChat API не даст
        официальный vision-режим).
        """
        if image_path:
            raise ProviderError(
                "GigaChatProvider: визуальные стимулы пока не поддержаны этим провайдером "
                "(spec_synthetic-panel_v1.4.md §1.3) — используйте provider: agent/anthropic/"
                "openai для исследований с изображениями."
            )
        raise NotImplementedError(
            "GigaChatProvider.generate: REST-вызов чата не реализован (TODO для интегратора). "
            "Интерфейс (BaseProvider.generate) готов к подключению — "
            "см. research/2026-07-08/06_tech_implementation.md, часть В.3."
        )


def get_provider(name: str, config: dict) -> BaseProvider:
    llm_cfg = config.get("llm", {})
    model = llm_cfg.get("model", "claude-sonnet-5")
    if name == "anthropic":
        return AnthropicProvider(model=model)
    if name == "openai":
        return OpenAIProvider(model=model)
    if name == "gigachat":
        return GigaChatProvider(model=model)
    raise ProviderError(
        f"Неизвестный провайдер: {name!r}. Ожидается один из: agent, anthropic, openai, gigachat."
    )


# ============================================================================
# Проба зрения, API-режим (spec_synthetic-panel_v1.4.md §1.2)
# ============================================================================
#
# Вызывается ИЗ run_study.py (владелец стадии/gate — см. run_study.py::
# ensure_vision_check в докстринге раздела "Проба зрения"), ДО generate_responses.
# System prompt здесь НАРОЧНО другой, чем build_system_prompt: НИКАКОЙ роли персоны
# — это отдельный, нейтральный "визуальный ассистент", который описывает
# изображение объективно (§1.2: "модель БЕЗ роли персоны описывает каждое
# изображение"). Agent-режим эту функцию не вызывает вообще — там описание
# пишет сама ведущая модель, читая файл (Read) и заполняя 00_vision_check.yaml
# вручную (см. run_study.py::VISION_CHECK_STOP_PENDING).

VISION_CHECK_SYSTEM_PROMPT = (
    "Ты — визуальный ассистент, а не персона респондента и не участник опроса. "
    "Опиши приложенное изображение ОБЪЕКТИВНО и нейтрально: что на нём "
    "изображено (продукт/упаковка/макет), какой текст на нём читается, ключевые "
    "визуальные элементы, композиция, цвета. НЕ оценивай привлекательность, НЕ "
    "изображай персону/реакцию человека, НЕ рассуждай пошагово. 2-5 предложений, "
    "по-русски."
)

VISION_CHECK_USER_PROMPT = "Опиши это изображение."


def describe_image_via_provider(provider: BaseProvider, image_path: str) -> str:
    """
    §1.2, API-режим: один vision-вызов провайдера для описания ОДНОГО изображения
    БЕЗ роли персоны (VISION_CHECK_SYSTEM_PROMPT — не build_system_prompt). Низкая
    temperature (0.2, не config.llm.temperature персон) — здесь нужно фактическое,
    воспроизводимое описание, а не разговорная вариативность ответа персоны.
    GigaChatProvider.generate сам бросит понятный ProviderError про неподдержку
    image_path (см. класс выше) — эта функция его не перехватывает намеренно,
    вызывающий код (run_study.py) должен увидеть ошибку как есть.
    """
    result = provider.generate(
        VISION_CHECK_SYSTEM_PROMPT,
        VISION_CHECK_USER_PROMPT,
        temperature=0.2,
        image_path=image_path,
    )
    return result.text


def fill_vision_check_descriptions(vision_check: dict, provider: BaseProvider) -> None:
    """
    Заполняет ПУСТЫЕ `description` в vision_check["images"] реальным vision-вызовом
    (§1.2, API-режим) — МУТИРУЕТ vision_check IN PLACE (вызывающий код — run_study.py
    — сам сохраняет результат на диск). Уже непустые описания не трогает (например,
    если часть изображений уже была описана вручную до переключения на API-режим).
    `key_element_recognized` эта функция НЕ трогает — оставляет как есть (обычно
    None), решение "распознан ли key_element" принимает run_study.py::
    compute_vision_verdicts единой эвристикой для agent- и API-режима (см. её докстринг).

    `vision_check_source` (v1.4 fix, см. run_study.py::_vision_check_stub/
    "Известное ограничение agent-режима пробы зрения" в докстринге модуля
    run_study.py) — ставится в "api_vision" именно здесь: это единственная точка
    кода, где provider реально получает пиксели изображения (image_path в
    provider.generate) для описания, а не полагается на самоотчёт агента.
    """
    for image in vision_check.get("images", []):
        if (image.get("description") or "").strip():
            continue
        image["description"] = describe_image_via_provider(provider, image["image_path"])
        image["vision_check_source"] = "api_vision"


# ============================================================================
# Agent-режим: responses_todo.jsonl + AGENT_TASK.md
# ============================================================================

AGENT_TASK_TEMPLATE = """\
# Задание агенту: заполнить свободные ответы синтетических респондентов

Файл `{todo_filename}` в этой же папке содержит {n_tasks} строк-заданий в формате JSONL.
Каждая строка — один требуемый ответ одного синтетического респондента на один стимул.

## Что нужно сделать

1. Прочитать `{todo_filename}` построчно (это JSON-объекты, по одному на строку).
2. Для КАЖДОЙ строки сгенерировать значение поля `text` — свободный текст ответа персоны
   на предложенный стимул, отвечая на вопрос из поля `question`.
3. Сохранить результат как `{output_filename}` в этой же папке — те же поля, что и в
   `{todo_filename}`, плюс добавленные поля `text`, `provider`, `model`, `request_id`,
   `generated_at` (для agent-режима: `provider="agent"`, `model=null`, `request_id=null`,
   `generated_at` — ISO8601 момент заполнения). Одна строка JSON на ответ, в том же порядке.

## Как отвечать за персону (обязательные правила)

- Роль: ты — персона, описанная в поле `persona` (структурированная карточка: демография,
  описание сегмента, особенности/оси, поведение, характерные фразы — см. spec §1.1). Отвечай
  от ПЕРВОГО ЛИЦА, разговорно, 2-5 предложений, оставаясь ЭТИМ конкретным человеком целиком.
- НЕ упоминай, что ты ИИ или языковая модель.
- НЕ рассуждай пошагово, не объясняй логику вывода — сразу живая реакция, как в короткой беседе.
- НЕ называй числовую оценку и НЕ используй фразы вида «я бы поставил X из 5» — только
  свободный текст впечатления/реакции.
- Между разными респондентами (разные `rid` с разным `persona`) ответы должны реально
  отличаться по формулировкам, интонации, деталям — не повторяй один и тот же шаблон.
  Персоны с одинаковым сегментом, но разным возрастом/доходом/городом должны звучать по-разному.
- Между сэмплами ОДНОГО респондента на ОДИН и тот же стимул (одинаковые segment+respondent_idx+
  stimulus_id, разные sample_idx) допустима и ожидается небольшая вариативность формулировок
  (это имитирует temperature > 0 у реального API) — не копируй текст дословно между сэмплами.
- НЕ вставляй в ответ якорные фразы шкалы — их в задании и нет намеренно (утечка шкалы
  испортила бы измерение).
{visual_section}
## Формат строки на выходе

Пример (поля — как во входной строке, плюс text/provider/model/request_id/generated_at):

```json
{{"rid": "dessertnye__A__001__1", "segment": "dessertnye", "persona": "...", "stimulus_id": "A", "stimulus_text": "...", "image_path": null, "label": null, "question": "...", "respondent_idx": 1, "sample_idx": 1, "text": "Свободный текст ответа персоны здесь.", "provider": "agent", "model": null, "request_id": null, "generated_at": "2026-07-09T12:00:00+00:00"}}
```

## Когда закончишь

Запустите скоринг:

```
python scripts/run_study.py --study {study_path} --stage score --run-dir {run_dir}
```
"""


# Вставляется в AGENT_TASK_TEMPLATE (плейсхолдер {visual_section}) ТОЛЬКО когда
# среди задач есть хотя бы одна с image_path — см. write_agent_mode. Для чисто
# текстовых study даёт visual_section="" -> AGENT_TASK.md байт-в-байт как до v1.4
# (плейсхолдер сидит на месте прежней пустой строки-разделителя, см. комментарий
# в write_agent_mode).
VISUAL_STIMULI_NOTE = """
## Визуальные стимулы

У части строк этого задания непустое поле `image_path` — абсолютный путь к файлу
PNG/JPG на диске (изображение стимула, уже провалидировано run_study.py). Правила:

- ПЕРЕД тем как писать ответ персоны для такой строки, прочитайте файл по этому
  пути (инструмент чтения файла) — персона реагирует на РЕАЛЬНЫЙ визуальный ряд
  макета/дизайна/упаковки, а не только на текст.
- Не придумывайте детали, которых не видно на изображении.
- Если `stimulus_text` пусто, а `label` заполнено — это короткая подпись стимула
  для контекста (не текст для дословного цитирования персоне).
- Все правила выше (роль/CoT/отсутствие якорей) действуют без изменений и для
  визуальных строк.
- Проба зрения (объективное описание каждого изображения БЕЗ роли персоны) уже
  пройдена ДО этого файла — см. `00_vision_check.md` в этой же папке, если нужно
  свериться с тем, что уже отмечено на макетах.

Строки без `image_path` (или с `image_path: null`) — обычные текстовые задания,
никаких изменений.
"""


def write_agent_mode(tasks: list[ResponseTask], run_dir: Path, study_path: str) -> Path:
    """
    Пишет run_dir/responses_todo.jsonl и run_dir/AGENT_TASK.md. Не вызывает никакую LLM.
    Возвращает путь к responses_todo.jsonl. Схема файла — см. модульный docstring выше.
    """
    todo_path = run_dir / "responses_todo.jsonl"
    with todo_path.open("w", encoding="utf-8") as f:
        for task in tasks:
            row = {
                "rid": task.rid,
                "segment": task.segment,
                "persona": task.persona,
                "stimulus_id": task.stimulus_id,
                "stimulus_text": task.stimulus_text,
                "image_path": task.image_path,
                "label": task.label,
                "question": task.question,
                "respondent_idx": task.respondent_idx,
                "sample_idx": task.sample_idx,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    task_md_path = run_dir / "AGENT_TASK.md"
    visual_section = VISUAL_STIMULI_NOTE if any(t.image_path for t in tasks) else ""
    task_md_path.write_text(
        AGENT_TASK_TEMPLATE.format(
            todo_filename="responses_todo.jsonl",
            output_filename="responses.jsonl",
            n_tasks=len(tasks),
            study_path=study_path,
            run_dir=str(run_dir),
            visual_section=visual_section,
        ),
        encoding="utf-8",
    )
    return todo_path


# ============================================================================
# Точка входа
# ============================================================================


@dataclass
class GenerateOutcome:
    status: str  # "todo" | "completed"
    responses_path: Optional[Path]
    todo_path: Optional[Path]
    n_tasks: int
    provider: str
    temperature_control: bool


def generate_responses(
    study: dict,
    config: dict,
    segments: dict[str, dict],
    question: str,
    run_dir: Path,
    study_path: str,
) -> GenerateOutcome:
    """
    Точка входа стадии generate (вызывается run_study.py).

    provider = agent (дефолт): пишет responses_todo.jsonl + AGENT_TASK.md, ничего не
    генерирует сама, temperature_control=False.
    provider = anthropic|openai: реально вызывает API, пишет responses.jsonl целиком,
    temperature_control=True.
    provider = gigachat: как выше, но провайдер бросит NotImplementedError на первом
    вызове .generate() (см. GigaChatProvider) — это ожидаемо для v1, run_study.py
    ловит исключение и печатает понятное сообщение (не наша забота здесь).
    """
    llm_cfg = config.get("llm", {})
    provider_name = llm_cfg.get("provider", "agent")
    seed = config.get("report", {}).get("seed", 42)
    samples_per_respondent = int(
        study.get("samples_per_respondent") or llm_cfg.get("samples_per_respondent", 2)
    )

    tasks = build_tasks(study, segments, question, seed, samples_per_respondent)

    if provider_name == "agent":
        todo_path = write_agent_mode(tasks, run_dir, study_path)
        return GenerateOutcome(
            status="todo", responses_path=None, todo_path=todo_path,
            n_tasks=len(tasks), provider="agent", temperature_control=False,
        )

    provider = get_provider(provider_name, config)
    temperature = float(llm_cfg.get("temperature", 0.85))
    responses_path = run_dir / "responses.jsonl"
    with responses_path.open("w", encoding="utf-8") as f:
        for task in tasks:
            # §1.3 v1.4: image_path/label — оба None для текстовых стимулов, тогда
            # build_task_prompt/provider.generate ведут себя байт-в-байт как в v1.3.
            user_prompt = build_task_prompt(
                task.stimulus_text, task.question, image_path=task.image_path, label=task.label
            )
            result = provider.generate(task.system_prompt, user_prompt, temperature, image_path=task.image_path)
            row = {
                "rid": task.rid,
                "segment": task.segment,
                "persona": task.persona,
                "stimulus_id": task.stimulus_id,
                "stimulus_text": task.stimulus_text,
                "image_path": task.image_path,
                "label": task.label,
                "question": task.question,
                "respondent_idx": task.respondent_idx,
                "sample_idx": task.sample_idx,
                "text": result.text,
                "provider": provider_name,
                "model": result.model,
                "request_id": result.request_id,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()  # инкрементальная запись — прогон переживает обрыв на середине (§ общих правил)

    return GenerateOutcome(
        status="completed", responses_path=responses_path, todo_path=None,
        n_tasks=len(tasks), provider=provider_name, temperature_control=True,
    )

"""
generate.py — мультипровайдерная генерация свободных ответов синтетических респондентов.

Реализует spec_synthetic-panel_v1.md §5. Точка входа для внешних вызовов (run_study.py) —
функция generate_responses(). Остальное — вспомогательные строительные блоки.

Провайдеры (config.yaml: llm.provider):
    agent      — дефолт. НЕ вызывает никакую LLM: пишет responses_todo.jsonl + AGENT_TASK.md,
                 которые заполняет агент/человек. temperature_control = False (см. spec §5).
    anthropic  — прямые вызовы Anthropic Messages API (ANTHROPIC_API_KEY из env).
    openai     — прямые вызовы OpenAI Chat Completions API (OPENAI_API_KEY из env).
    gigachat   — REST-каркас (OAuth) с TODO для интегратора: интерфейс полный
                 (BaseProvider.generate), сам HTTP-обмен не реализован (нужен корпоративный
                 контур/сертификат, см. research/2026-07-08/06_tech_implementation.md, В.3).

Промпт (§5, СТРОГО): системная часть описывает персону и требует свободный текст от
первого лица без chain-of-thought; задание — стимул + question ИЗ ШКАЛЫ anchors_ru.yaml
(ТОЛЬКО формулировка вопроса). Якорные фразы (anchor_sets/phrases) НИКОГДА не передаются
в build_system_prompt/build_task_prompt — этот модуль их даже не импортирует из ssr_core,
получая `question` уже готовой строкой от вызывающего кода (run_study.py), что делает
утечку шкалы структурно исключённой (нечем утечь — тексту анкоров сюда просто неоткуда взяться).

Персона-джиттер: детерминирован от (config.report.seed, segment_id, respondent_idx) —
см. jitter_persona(). Один и тот же прогон (тот же seed) с тем же study.yaml всегда даёт
одни и те же профили персон — воспроизводимость (§0.5).

Схема responses_todo.jsonl (agent-режим, ДО заполнения) — одна строка JSON на задачу:
    rid            str   уникальный id ответа, "<segment>__<stimulus_id>__<respondent_idx>__<sample_idx>"
    segment        str   id сегмента (как в panel/segments/<id>.yaml)
    persona        str   рендеренная одна строка описания персоны (RU), для контекста агенту
    stimulus_id    str
    stimulus_text  str
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
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
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

    Читает segment['persona_jitter'] = {age: [min, max], income_level: [...], city_tier: [...]}
    (схема сегмента — spec §9, владелец файлов — сборщик панели [B3]). Один и тот же
    (seed, segment_id, respondent_idx) всегда даёт один и тот же профиль — воспроизводимость
    прогона (§0.5). Разумные дефолты подставляются, если сегмент не задаёт часть полей
    (не должно падать на неполном/черновом segment YAML).
    """
    jitter = segment.get("persona_jitter") or {}
    rng = make_rng(seed, segment_id, str(respondent_idx))

    age_range = jitter.get("age") or [25, 55]
    age_lo, age_hi = int(age_range[0]), int(age_range[1])
    if age_hi < age_lo:
        age_lo, age_hi = age_hi, age_lo
    age = rng.randint(age_lo, age_hi)

    income_options = jitter.get("income_level") or ["средний доход"]
    income = rng.choice(income_options)

    city_options = jitter.get("city_tier") or ["крупный город"]
    city = rng.choice(city_options)

    language_bank = segment.get("language") or []
    language_flavor = rng.choice(language_bank) if language_bank else ""

    return {
        "age": age,
        "income_level": income,
        "city_tier": city,
        "language_flavor": language_flavor,
        "segment_name": segment.get("name", segment_id),
    }


# Переводы токенов persona_jitter (income_level/city_tier) в естественный русский.
# Схема сегментов (panel/segments/*.yaml, владелец [B3]) использует английские
# snake_case-токены как МАШИННЫЕ идентификаторы диапазона для детерминированного
# выбора в jitter_persona() — это нормально для данных. Но эти же значения идут
# ДАЛЬШЕ в промпт персоны и в поле `persona` JSONL, а там уже должен быть
# человекочитаемый русский текст (spec_synthetic-panel_v1.md §0.4: "Всё
# русскоязычное: якоря, промпты, сегменты, отчёты"; без перевода "mid_city"/
# "average" утекали дословно в русский системный промпт — см. docs/review_v1.md,
# находка №2). Словарь закрытый и сверен со ВСЕМИ 7 файлами panel/segments/*.yaml
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


def render_persona_line(profile: dict, segment: dict) -> str:
    """Компактная одна строка описания персоны — идёт и в промпт, и в поле `persona` JSONL."""
    formats = (segment.get("behavior") or {}).get("formats")
    format_hint = ""
    if isinstance(formats, list) and formats:
        format_hint = f", формат: {formats[0]}"
    city_ru = _translate_jitter_token(profile["city_tier"], _CITY_TIER_RU, "city_tier")
    income_ru = _translate_jitter_token(profile["income_level"], _INCOME_LEVEL_RU, "income_level")
    return (
        f"{profile['age']} лет, {city_ru}, {income_ru}, "
        f"сегмент «{profile['segment_name']}»{format_hint}"
    )


# ============================================================================
# Промпт-шаблон (§5) — системная часть и задание
# ============================================================================


def build_system_prompt(profile: dict, segment: dict) -> str:
    """
    Системная часть промпта респондента (§5). НЕ содержит и не может содержать якорные
    фразы шкалы — эта функция вообще не получает anchor_sets на вход.
    """
    persona_line = render_persona_line(profile, segment)
    language_hint = ""
    if profile.get("language_flavor"):
        language_hint = (
            f" Иногда, если уместно, используй характерные для себя обороты речи, "
            f"например что-то в духе: «{profile['language_flavor']}»."
        )
    return (
        f"Ты отвечаешь как живой человек: {persona_line}. "
        "Отвечай от первого лица, разговорно, 2-5 предложений. "
        "Не упоминай, что ты ИИ, языковая модель или ассистент. "
        "Не рассуждай пошагово и не объясняй ход мыслей — сразу дай живую, естественную "
        "реакцию, как будто тебя спросили об этом в короткой беседе."
        f"{language_hint}"
    )


def build_task_prompt(stimulus_text: str, question: str) -> str:
    """
    Задание (§5): стимул + question. `question` — ТОЛЬКО формулировка вопроса шкалы из
    anchors_ru.yaml (поле `question`), передаётся вызывающим кодом (run_study.py) как
    простая строка. Якорные фразы (anchor_sets/phrases) сюда НИКОГДА не должны попадать —
    вызывающий код обязан передавать question, а не что-либо ещё из anchors_ru.yaml.
    """
    return (
        f"Вот что тебе показывают:\n«{stimulus_text}»\n\n"
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
            persona_line = render_persona_line(profile, segment)
            system_prompt = build_system_prompt(profile, segment)
            for stimulus in study["stimuli"]:
                for sample_idx in range(1, samples_per_respondent + 1):
                    rid = f"{segment_id}__{stimulus['id']}__{respondent_idx:03d}__{sample_idx}"
                    tasks.append(
                        ResponseTask(
                            rid=rid,
                            segment=segment_id,
                            persona=persona_line,
                            stimulus_id=stimulus["id"],
                            stimulus_text=stimulus["text"],
                            question=question,
                            respondent_idx=respondent_idx,
                            sample_idx=sample_idx,
                            system_prompt=system_prompt,
                        )
                    )
    return tasks


# ============================================================================
# Провайдеры API
# ============================================================================


class ProviderError(RuntimeError):
    """Ошибка провайдера генерации: отсутствие ключа/пакета, исчерпанные ретраи и т.п."""


@dataclass
class GenerationResult:
    text: str
    model: str
    request_id: Optional[str] = None


class BaseProvider(ABC):
    name: str = "base"

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str, temperature: float) -> GenerationResult:
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

    def generate(self, system_prompt: str, user_prompt: str, temperature: float) -> GenerationResult:
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    temperature=temperature,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
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

    def generate(self, system_prompt: str, user_prompt: str, temperature: float) -> GenerationResult:
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    temperature=temperature,
                    max_tokens=self.max_tokens,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
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

    def generate(self, system_prompt: str, user_prompt: str, temperature: float) -> GenerationResult:
        """
        TODO(интегратор): после реализации _ensure_token() — POST {CHAT_URL} с телом
        {"model": self.model, "temperature": temperature, "messages": [
            {"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}
        ]}, заголовок 'Authorization: Bearer {token}'. Ретраи/бэкофф — по образцу
        AnthropicProvider.generate()/OpenAIProvider.generate() выше.
        """
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

- Роль: ты — персона, описанная в поле `persona` (возраст/город/доход/сегмент). Отвечай от
  ПЕРВОГО ЛИЦА, разговорно, 2-5 предложений.
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

## Формат строки на выходе

Пример (поля — как во входной строке, плюс text/provider/model/request_id/generated_at):

```json
{{"rid": "dessertnye__A__001__1", "segment": "dessertnye", "persona": "...", "stimulus_id": "A", "stimulus_text": "...", "question": "...", "respondent_idx": 1, "sample_idx": 1, "text": "Свободный текст ответа персоны здесь.", "provider": "agent", "model": null, "request_id": null, "generated_at": "2026-07-09T12:00:00+00:00"}}
```

## Когда закончишь

Запустите скоринг:

```
python scripts/run_study.py --study {study_path} --stage score --run-dir {run_dir}
```
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
                "question": task.question,
                "respondent_idx": task.respondent_idx,
                "sample_idx": task.sample_idx,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    task_md_path = run_dir / "AGENT_TASK.md"
    task_md_path.write_text(
        AGENT_TASK_TEMPLATE.format(
            todo_filename="responses_todo.jsonl",
            output_filename="responses.jsonl",
            n_tasks=len(tasks),
            study_path=study_path,
            run_dir=str(run_dir),
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
            user_prompt = build_task_prompt(task.stimulus_text, task.question)
            result = provider.generate(task.system_prompt, user_prompt, temperature)
            row = {
                "rid": task.rid,
                "segment": task.segment,
                "persona": task.persona,
                "stimulus_id": task.stimulus_id,
                "stimulus_text": task.stimulus_text,
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

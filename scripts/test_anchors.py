#!/usr/bin/env python3
"""
test_anchors.py — гейт монотонности якорных наборов SSR (spec_synthetic-panel_v1.3.md §1.2 п.2).

Чинит Д2 (docs/review найдены Fable+Codex, верифицировано оркестратором): русские якорные
наборы `references/anchors_ru.yaml` были монотонны только "на глаз" (см. docs/review_v1.md,
§3 — "прочитал и мысленно переставил силу каждой фразы") и НИКОГДА не проверялись реальным
вычислением через embedding-модель. Этот файл — тот самый расчёт, которого не хватало.

ВАЖНО, в отличие от test_ssr.py: этот файл требует sentence-transformers И реальную
embedding-модель (скачивание с HuggingFace при первом запуске — см. spec_synthetic-panel_v1.3.md
§1.2 п.3, "сеть до HuggingFace есть; модели кэшируются в venv"). test_ssr.py проверяет чистую
математику SSR на голых numpy-векторах и намеренно не трогает сеть — test_anchors.py проверяет
СЕМАНТИКУ конкретных русских фраз в конкретном embedding-пространстве, что структурно
невозможно без реальной модели. Первый прогон на новой модели может занять до нескольких минут
(скачивание + загрузка весов); повторные прогоны — из кэша, секунды-десятки секунд.

Два способа запуска:

  1) Регрессия (юнит-тест, "все тесты зелёные" из DoD spec_synthetic-panel_v1.3.md):
         python scripts/test_anchors.py
         python -m unittest scripts.test_anchors -v
     Тест использует эмбеддер, ЗАФИКСИРОВАННЫЙ в config.yaml (embedding.model/prefix/device) —
     то есть "выбранный стек" после этапа embedder_ab.py. Если config.yaml ещё указывает на
     эмбеддер, не прошедший гейт (например, до выбора победителя) — тест красный, это ожидаемо
     и есть сигнал "сборка не принята" (см. модульный докстринг спецификации, §1.2 п.2).

  2) CLI-диагностика на произвольном эмбеддере (нужна embedder_ab.py и ручной диагностике Д2):
         python scripts/test_anchors.py --model <hf-имя> --prefix "..." --device cpu -v
     Печатает таблицу E по уровням 1..5 для каждого набора каждой шкалы, вердикт монотонности,
     rank-recovery и общий вердикт гейта. Код возврата: 0 — гейт пройден, 1 — не пройден
     (пригодно для CI/скриптов).

Гейт (порог приёмки, spec_synthetic-panel_v1.3.md §1.2 п.2 — все условия через "И"):
  (a) Leave-one-set-out монотонность E по уровням, ОТДЕЛЬНО для каждого набора каждой шкалы:
      набор j временно исключается из "эталона"; его 5 фраз (уровни 1..5) скорятся как ОТВЕТЫ
      через штатный SSR-пайплайн (pmf_single_anchor_set + average_pmfs) на ОСТАВШИХСЯ наборах
      шкалы (не на себе — иначе тест тривиален: cosine(x,x)=1 всегда даёт "верный" максимум).
      Строгая монотонность — E(1)<E(2)<E(3)<E(4)<E(5) без исключений. Порог: >= 3 из 4 наборов
      монотонны НА КАЖДУЮ шкалу.
  (b) Среднее E(5) > среднее E(4) (усреднение по всем leave-one-out прогонам шкалы, п.(a)) —
      на ВСЕХ шкалах без исключения. Это отдельное, более узкое условие, чем (a): набор может
      быть "монотонным" в других парах уровней и всё равно проваливать именно переход 4->5
      (или наоборот, проходить (b) в среднем при отдельных немонотонных наборах) — Д2 бьёт
      именно по разлипанию 4/5, поэтому проверяется явно и отдельно от общей монотонности.
  (c) Rank-recovery: независимый held-out банк синтетических фраз-парафразов уровня k,
      загружаемый из references/paraphrase_bank_ru.yaml (см. load_paraphrase_bank ниже; ни
      одна фраза НЕ дублирует anchor_sets ни текстуально, ни на уровне значимых нормализованных
      словоформ — иначе тест был бы циркулярным, см. check_no_anchor_lexicon_overlap). Парафразы
      скорятся ПОЛНЫМ продакшн-пайплайном SSR (среднее по ВСЕМ наборам шкалы разом, как в
      ssr_core.SSREngine.score_texts — без leave-one-out, это тест на генерализацию за пределы
      обучающих фраз, а не на взаимную согласованность наборов). Среднее E по группе уровня k
      обязано строго возрастать k=1..5 на каждой шкале.

Гейт зелёный, только если (a) И (b) И (c) выполнены на ВСЕХ шкалах, найденных в файле якорей.

ИСТОРИЯ УСЛОВИЯ (c) — v1.3 -> v1.4 (spec_synthetic-panel_v1.4.md §2.1, чинит находку №2
docs/review_v1.3.md, MAJOR). До этой правки held-out банк был зашит прямо в этот файл (константа
PARAPHRASE_BANK): 3-4 фразы на уровень на шкалу, все написаны ОДНИМ автором — тем же, что писал
и сами anchor_sets. Условие (c) формально уже входило в ScaleGateResult.passed наравне с (a)/(b)
(код не делал разницы между "жёстким" и "мягким" гейтом), но ЭПИСТЕМИЧЕСКИ было слабым: PASS на
маленькой одноавторской выборке не давал уверенности, что шкала обобщается на независимо
сформулированный текст, а не просто на текст, случайно похожий по стилю на anchor_sets того же
автора (см. review_v1.3.md §1.2 — независимый банк из 3 фраз/уровень получил ДРУГОЙ вердикт по
паре шкала×эмбеддер, чем банк, зашитый здесь). references/paraphrase_bank_ru.yaml — устранение
этой находки: >= 10 фраз на уровень на шкалу, с полем `style` (разговорный/сдержанный/
эмоциональный/краткий/развёрнутый), проверенных программно (не "на глаз") на отсутствие лексики
anchors_ru.yaml v2. Числа "было/стало" по каждой шкале и каждому эмбеддеру (в т.ч. честно
зафиксированные слабости, если расширенный банк их вскрыл, — правило "не подгонять порог под
результат") — docs/rank_recovery_v14.md. Условие (c) как ТАКОВОЕ (строгая монотонность средних
E по группе уровня) не изменилось — расширился только объём и авторское разнообразие входных
данных, что и делает PASS/FAIL этого условия статистически весомым, а не просто формально верным.

ИСТОРИЯ УСЛОВИЯ (c) — v1.4 -> v1.4 fix (задание архитектора, 2026-07-19, чинит находку №1
docs/review_v1.4.md, MAJOR: "rank-recovery... статистически честный критерий"). До этой правки
условие (c) объявляло FAIL по ГОЛОМУ точечному сравнению средних E соседних уровней
(mean_e_by_level[k+1] <= mean_e_by_level[k]) — ТА ЖЕ категория ошибки "point-estimate сравнение
средних без учёта разброса", которую v1.3 уже чинил для отдельного вопроса (разделимость
стимулов внутри одного прогона, report.py) переходом на парный бутстреп
(ssr_core.joint_paired_bootstrap_means/pairwise_win_probability, см. модульный докстринг
report.py). docs/rank_recovery_v14.md §5.2 и независимо docs/review_v1.4.md §1.2 (два разных
банка, B2 и F1) сами, вручную и ВНЕ гейта, считали бутстреп-вероятность разворота для своих
проблемных переходов appeal — и оба раза получили P около 0.42-0.44 (близко к шуму 0.5, не к
уверенному развороту 0.0) — то есть точечный FAIL по букве старого условия (c) статистически
не отличим от ничьей. Эта правка переносит именно ЭТОТ уже посчитанный вручную бутстреп ВНУТРЬ
самого гейта (ScaleGateResult.passed), вместо того чтобы он оставался только текстом двух
документов поверх формально-красного точечного гейта.

Новая логика: для КАЖДОЙ из 4 соседних пар уровней (1,2)/(2,3)/(3,4)/(4,5) считается
P(инверсия) = доля бутстреп-итераций (ресэмплинг С ВОЗВРАЩЕНИЕМ, ПО ФРАЗАМ held-out банка
уровня, НЕ по респондентам — см. rank_recovery() и bootstrap_pair_inversion_probability ниже),
где резэмплированное среднее E уровня k оказывается СТРОГО ВЫШЕ резэмплированного среднего E
уровня k+1 (то есть "неправильный" порядок). FAIL — только при P(инверсия) >=
RANK_RECOVERY_INVERSION_CONFIDENCE_THRESHOLD (0.7 — порог ЗАФИКСИРОВАН АРХИТЕКТОРОМ до пересчёта
чисел этой итерации, меняться в ответ на то, что покажет пересчёт, НЕ должен — та же защита от
подгонки, что уже действует для самого банка парафразов, см. параграф выше "не подгонять порог
под результат"). Переход с точечно неверным порядком (mean(k+1) <= mean(k)), но P(инверсия) <
порога — печатается как WARNING "near-tie, в пределах шума" (см. format_report), НЕ считается
провалом гейта: ScaleGateResult.passed/rank_recovery.monotonic теперь читают именно
confident_reversal (P >= порога), а не голый знак разности средних. Числа по обоим банкам
(B2 — references/paraphrase_bank_ru.yaml, и независимому банку F1) — docs/rank_recovery_v14.md,
раздел "Критерий (c) v2: бутстреп". Сам банк парафразов и якоря этой правкой НЕ изменены —
меняется только СТАТИСТИЧЕСКАЯ ОБРАБОТКА уже существующих per-фразовых E-значений.
"""

from __future__ import annotations

import argparse
import re
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

import numpy as np
import yaml

# Позволяет запускать файл напрямую (python scripts/test_anchors.py) независимо от cwd.
_SCRIPTS_DIR = Path(__file__).resolve().parent
_SKILL_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import ssr_core  # noqa: E402

DEFAULT_ANCHORS_PATH = _SKILL_ROOT / "references" / "anchors_ru.yaml"
DEFAULT_CONFIG_PATH = _SKILL_ROOT / "config.yaml"
DEFAULT_PARAPHRASE_BANK_PATH = _SKILL_ROOT / "references" / "paraphrase_bank_ru.yaml"

LEVELS = (1, 2, 3, 4, 5)

# ============================================================================
# Критерий (c) v2: бутстреп-порог (см. "ИСТОРИЯ УСЛОВИЯ (c) — v1.4 -> v1.4 fix" в
# докстринге модуля выше за полным обоснованием). Оба числа ЗАФИКСИРОВАНЫ архитектором
# 2026-07-19, ДО пересчёта на реальном стеке — менять их в ответ на то, что покажет
# пересчёт, запрещено (защита от подгонки порога под желаемый результат).
# ============================================================================
RANK_RECOVERY_INVERSION_CONFIDENCE_THRESHOLD = 0.7
RANK_RECOVERY_BOOTSTRAP_ITERS = 5000
RANK_RECOVERY_BOOTSTRAP_SEED = 42


# ============================================================================
# Стоп-слова для проверки пересечений с лексикой якорей (см. п.(c) докстринга модуля,
# check_no_anchor_lexicon_overlap ниже)
# ============================================================================
#
# Функциональные слова русского языка: местоимения/определители, союзы/частицы,
# предлоги, обобщённые модальные/степенные наречия и связки-вспомогательные глаголы.
# НЕ включены слова с конкретным, различимым смыслом шкалы (например "актуально",
# "случай", "впечатление", "нравится", "куплю" — это и есть значимая лексика, которую
# банк парафразов обязан НЕ повторять). Список СОЗНАТЕЛЬНО щедрый на обобщённые
# модальные/степенные слова ("очень", "совсем", "вполне", "похоже", "отчасти", "точно"
# в смысле "наверное/для верности") — без этого послабления невозможно сформулировать
# естественную русскую фразу о степени/уверенности без "пересечения" с якорями, а сама
# проверка потеряла бы смысл (ловила бы неизбежное, а не отличимое). Проверка —
# СЛОВОФОРМЕННАЯ (нормализация: нижний регистр + ё->е + разбиение на буквенные токены),
# НЕ лемма-уровня — по заданию спецификации (spec_synthetic-panel_v1.4.md §2.1) этого
# достаточно: "покупка"/"куплю" НЕ считаются пересечением, хотя однокоренные, а вот
# буквальное "куплю" в банке парафразов запрещено, потому что это ТОЧНО ТА ЖЕ словоформа,
# что и в anchor_sets.
STOPWORDS_RU: frozenset[str] = frozenset({
    # местоимения / определители
    "я", "мы", "ты", "вы", "он", "она", "оно", "они", "меня", "мне", "мной", "мною",
    "тебя", "тебе", "тобой", "его", "ее", "её", "ему", "ей", "им", "их", "него", "неё",
    "нему", "ним", "нам", "нас", "вами", "вам", "вас", "себя", "себе", "собой", "кто",
    "что", "чей", "чья", "чьё", "чьи", "чьих", "который", "которая", "которое",
    "которые", "которого", "которой", "этот", "эта", "это", "эти", "этого", "этой",
    "этому", "этим", "этих", "этом", "эту", "того", "той", "тех", "тем", "таков",
    "такова", "такой", "такая", "такое", "такие", "такого", "такую", "весь", "вся",
    "всё", "все", "всего", "всей", "всех", "всем", "всею", "всеми", "сам", "сама",
    "само", "сами", "свой", "своя", "своё", "свои", "своего", "своей", "своих", "своим",
    "каждый", "каждая", "каждое", "каждые", "любой", "любая", "любое", "любые",
    "любого", "любому", "любым", "любую", "любом", "любыми", "любых", "никто", "ничто",
    "никакой", "никакая", "никакое", "кое", "какой", "какая", "какое", "какие", "иной",
    "иная", "иное", "другой", "другая", "другое", "одно", "один", "одна", "некоторый",
    "некоторая", "несколько", "многие", "мой", "моя", "моё", "мое", "мои", "моего",
    "моей", "моих", "моим", "моими", "мою", "твой", "твоя", "твоё", "твое", "твои",
    "всякий", "всякая", "всякое", "всякие", "всяких", "всяким", "всякими",
    # союзы / частицы
    "и", "а", "но", "или", "либо", "чтобы", "как", "будто", "словно", "если", "же",
    "ли", "бы", "б", "да", "нет", "не", "ни", "вот", "ну", "уже", "ещё", "еще",
    "только", "лишь", "просто", "там", "тут", "здесь", "туда", "сюда", "оттуда",
    "отсюда", "потом", "затем", "тогда", "когда", "где", "куда", "откуда", "почему",
    "зачем", "отчего", "итак", "притом", "причём", "причем", "хотя", "пускай", "пусть",
    "разве", "неужели", "якобы", "чтоб", "дабы", "то", "ведь", "мол", "де", "аж",
    "опять", "снова", "заново",
    # предлоги
    "в", "во", "на", "с", "со", "к", "ко", "у", "о", "об", "обо", "от", "ото", "до",
    "из", "изо", "за", "над", "надо", "под", "подо", "при", "для", "без", "безо",
    "через", "чрез", "между", "меж", "про", "по", "ради", "вместо", "кроме", "среди",
    "вокруг", "около", "внутри", "вдоль", "поперёк", "поперек", "согласно",
    "благодаря", "несмотря",
    # обобщённые модальные/степенные наречия и связки — генерические усилители/хеджи,
    # неизбежные в любой русской речи о степени/уверенности, не несут ОТЛИЧИТЕЛЬНОГО
    # смысла конкретной шкалы (в отличие от "актуально"/"случай"/"впечатление" и т.п.)
    "очень", "совсем", "совершенно", "вообще", "реально", "действительно", "прямо",
    "просто", "слегка", "немного", "чуть", "довольно", "вполне", "скорее", "отчасти",
    "наверное", "возможно", "может", "быть", "вроде", "кажется", "похоже", "примерно",
    "точно", "явно", "видимо", "пожалуй", "буквально", "практически", "полностью",
    "абсолютно", "максимально", "особо", "особенно", "весьма", "крайне",
    "чрезвычайно", "целиком", "есть", "был", "была", "было", "были", "будет", "будут",
    "стать", "станет", "нужно", "надо", "можно", "нельзя", "стоит",
})


def normalize_significant_tokens(text: str) -> frozenset[str]:
    """Нижний регистр + ё->е + буквенные токены (regex), минус STOPWORDS_RU. Ровно та
    нормализация, которую спецификация называет достаточной ("лемма-уровень не нужен,
    достаточно нормализованных словоформ существенных слов", spec_synthetic-panel_v1.4.md
    §2.1) — НЕ стемминг и НЕ лемматизация, только буквальные словоформы за вычетом
    служебных слов."""
    normalized = text.lower().replace("ё", "е")
    words = re.findall(r"[а-я]+", normalized)
    return frozenset(w for w in words if w not in STOPWORDS_RU)


def anchor_lexicon(anchors_path: Path | str = DEFAULT_ANCHORS_PATH) -> frozenset[str]:
    """Множество значимых (не служебных) нормализованных словоформ, встречающихся в
    ЛЮБОЙ фразе ЛЮБОГО anchor_sets ЛЮБОЙ шкалы anchors_ru.yaml (не только `question`/
    `comment` — те не идут в промпт скоринга и не участвуют в SSR-математике, поэтому
    не проверяются). Это и есть "лексика якорей v2" из §2.1 spec_synthetic-panel_v1.4.md,
    вычисленная программно из реального файла, а не заданная руками."""
    path = Path(anchors_path)
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    scales = data.get("scales", data) if isinstance(data, Mapping) else {}
    vocab: set[str] = set()
    for scale in scales.values():
        for aset in scale.get("anchor_sets", []) or []:
            for _, text in (aset.get("phrases", {}) or {}).items():
                vocab |= normalize_significant_tokens(str(text))
    return frozenset(vocab)


@dataclass
class LexiconOverlapViolation:
    scale_id: str
    level: int
    style: str
    text: str
    overlap_tokens: tuple[str, ...]


def check_no_anchor_lexicon_overlap(
    paraphrase_bank_raw: Mapping[str, Mapping[int, Sequence[Mapping[str, str]]]],
    anchors_path: Path | str = DEFAULT_ANCHORS_PATH,
) -> list[LexiconOverlapViolation]:
    """Условие независимости банка (см. докстринг модуля, п.(c)): ни одна фраза банка
    парафразов не должна содержать значимую (не служебную) словоформу, встречающуюся
    где-либо в anchors_ru.yaml. Возвращает список нарушений (пустой список = чисто).
    Принимает "сырую" структуру банка (load_paraphrase_bank_raw) — нужно поле `style`
    для диагностики, поэтому не text-only load_paraphrase_bank."""
    vocab = anchor_lexicon(anchors_path)
    violations: list[LexiconOverlapViolation] = []
    for scale_id, levels in paraphrase_bank_raw.items():
        for level, items in levels.items():
            for item in items:
                tokens = normalize_significant_tokens(item["text"])
                overlap = tokens & vocab
                if overlap:
                    violations.append(
                        LexiconOverlapViolation(
                            scale_id=scale_id,
                            level=int(level),
                            style=item.get("style", "?"),
                            text=item["text"],
                            overlap_tokens=tuple(sorted(overlap)),
                        )
                    )
    return violations


# ============================================================================
# Банк парафразов для rank-recovery (см. п.(c) в докстринге модуля) — v1.4:
# ЗАГРУЖАЕТСЯ из references/paraphrase_bank_ru.yaml, больше не зашит в этот файл
# (spec_synthetic-panel_v1.4.md §2.1 — см. историю условия (c) в докстринге модуля).
# ============================================================================


def load_paraphrase_bank_raw(
    path: Path | str = DEFAULT_PARAPHRASE_BANK_PATH,
) -> dict[str, dict[int, list[dict[str, str]]]]:
    """Полная структура references/paraphrase_bank_ru.yaml: {scale: {level: [{"text",
    "style"}, ...]}}. Используется там, где нужно поле `style` (диагностика,
    check_no_anchor_lexicon_overlap, структурные тесты) — для самого rank-recovery
    (нужен только текст) см. load_paraphrase_bank ниже."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    meta = data.get("meta", {}) or {}
    min_per_level = int(meta.get("min_phrases_per_level", 10))
    scales = data.get("scales", {}) or {}
    bank: dict[str, dict[int, list[dict[str, str]]]] = {}
    for scale_id, scale in scales.items():
        levels_raw = (scale or {}).get("levels", {}) or {}
        levels: dict[int, list[dict[str, str]]] = {}
        for lvl in LEVELS:
            items = list(levels_raw.get(lvl) or [])
            if len(items) < min_per_level:
                raise ValueError(
                    f"{p}: шкала '{scale_id}', уровень {lvl} — {len(items)} фраз(ы) "
                    f"< min_phrases_per_level={min_per_level} (meta.min_phrases_per_level)."
                )
            levels[lvl] = items
        bank[scale_id] = levels
    return bank


def load_paraphrase_bank(
    path: Path | str = DEFAULT_PARAPHRASE_BANK_PATH,
) -> dict[str, dict[int, list[str]]]:
    """Текстовая проекция load_paraphrase_bank_raw — {scale: {level: [text, ...]}},
    ровно тот формат, который принимает rank_recovery()/evaluate_scale() ниже (поле
    `style` для самой SSR-математики не нужно, только для диагностики/структурных
    проверок банка, см. load_paraphrase_bank_raw)."""
    raw = load_paraphrase_bank_raw(path)
    return {
        scale_id: {lvl: [item["text"] for item in items] for lvl, items in levels.items()}
        for scale_id, levels in raw.items()
    }


# ============================================================================
# Результаты (dataclasses)
# ============================================================================


@dataclass
class SetMonotonicityResult:
    set_index: int
    label: str
    e_values: list[float]  # E для уровней 1..5, индекс 0..4
    violations: list[tuple[int, int]]  # пары уровней (i, i+1), где E НЕ строго возросло

    @property
    def monotonic(self) -> bool:
        return len(self.violations) == 0


@dataclass
class RankRecoveryTransition:
    """
    Один переход (уровень k -> уровень k+1) условия (c) v2 (см. "ИСТОРИЯ УСЛОВИЯ (c) —
    v1.4 -> v1.4 fix" в докстринге модуля). `p_inversion` — доля бутстреп-итераций, где
    резэмплированное среднее E уровня k СТРОГО ВЫШЕ резэмплированного среднего уровня k+1
    (т.е. "неправильный" порядок) — см. bootstrap_pair_inversion_probability.
    """

    level_from: int
    level_to: int
    point_diff: float  # mean_e_by_level[level_to] - mean_e_by_level[level_from] (точечная оценка)
    p_inversion: float

    @property
    def confident_reversal(self) -> bool:
        """FAIL этого перехода — П(инверсия) >= порога, зафиксированного архитектором
        (RANK_RECOVERY_INVERSION_CONFIDENCE_THRESHOLD). Единственное условие провала
        конкретного перехода в v2 — НЕ знак point_diff сам по себе."""
        return self.p_inversion >= RANK_RECOVERY_INVERSION_CONFIDENCE_THRESHOLD

    @property
    def near_tie(self) -> bool:
        """Точечно порядок выглядит неверным (point_diff <= 0 — по старому, точечному
        критерию v1.4 это был бы FAIL), но бутстреп не даёт уверенности в развороте —
        печатается как предупреждение (format_report), не проваливает гейт."""
        return self.point_diff <= 0 and not self.confident_reversal


@dataclass
class RankRecoveryResult:
    mean_e_by_level: list[float]  # индекс 0..4 = уровень 1..5
    e_values_by_level: dict[int, list[float]]  # сырые per-фразовые E — вход бутстрепа
    transitions: list[RankRecoveryTransition]  # все 4 соседних перехода, всегда заполнены
    bootstrap_iters: int
    bootstrap_seed: int

    @property
    def confident_reversals(self) -> list[tuple[int, int]]:
        """Единственное, что проваливает гейт условия (c) v2 — переходы с УВЕРЕННЫМ
        (P >= порога) разворотом. Имя `violations` сохранено как алиас ниже для обратной
        совместимости мест, ожидающих старое имя (format_report/embedder_ab.py читают
        через .monotonic, не напрямую .violations, но алиас безопаснее менять молча)."""
        return [(t.level_from, t.level_to) for t in self.transitions if t.confident_reversal]

    @property
    def violations(self) -> list[tuple[int, int]]:
        return self.confident_reversals

    @property
    def near_ties(self) -> list[RankRecoveryTransition]:
        return [t for t in self.transitions if t.near_tie]

    @property
    def monotonic(self) -> bool:
        return len(self.confident_reversals) == 0


@dataclass
class ScaleGateResult:
    scale_id: str
    set_results: list[SetMonotonicityResult]
    rank_recovery: RankRecoveryResult
    min_monotonic_sets: int = 3

    @property
    def n_sets(self) -> int:
        return len(self.set_results)

    @property
    def n_monotonic(self) -> int:
        return sum(1 for r in self.set_results if r.monotonic)

    @property
    def mean_e4(self) -> float:
        return float(np.mean([r.e_values[3] for r in self.set_results]))

    @property
    def mean_e5(self) -> float:
        return float(np.mean([r.e_values[4] for r in self.set_results]))

    @property
    def e5_gt_e4(self) -> bool:
        return self.mean_e5 > self.mean_e4

    @property
    def passed(self) -> bool:
        return (
            self.n_monotonic >= self.min_monotonic_sets
            and self.e5_gt_e4
            and self.rank_recovery.monotonic
        )


@dataclass
class GateReport:
    scale_results: dict[str, ScaleGateResult] = field(default_factory=dict)
    model_name: str = ""
    prefix: str = ""

    @property
    def passed(self) -> bool:
        return bool(self.scale_results) and all(r.passed for r in self.scale_results.values())


# ============================================================================
# Загрузка config.yaml / anchors_ru.yaml
# ============================================================================


def load_embedding_config(config_path: Path | str = DEFAULT_CONFIG_PATH) -> dict:
    """Читает блок `embedding:` config.yaml. Отсутствующие поля — разумные дефолты."""
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    emb = cfg.get("embedding", {}) or {}
    return {
        "model": emb.get("model", "paraphrase-multilingual-MiniLM-L12-v2"),
        "prefix": emb.get("prefix", "") or "",
        "device": emb.get("device", "cpu"),
    }


def load_ssr_config(config_path: Path | str = DEFAULT_CONFIG_PATH) -> dict:
    """Читает блок `ssr:` config.yaml (epsilon/pmf_temperature/min_anchor_sets)."""
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    ssr_cfg = cfg.get("ssr", {}) or {}
    return {
        "epsilon": float(ssr_cfg.get("epsilon", 0.001)),
        "pmf_temperature": float(ssr_cfg.get("pmf_temperature", 1.0)),
        "min_anchor_sets": int(ssr_cfg.get("min_anchor_sets", 4)),
    }


def discover_scale_ids(anchors_path: Path | str = DEFAULT_ANCHORS_PATH) -> list[str]:
    """Список id шкал, реально присутствующих в anchors_ru.yaml (не хардкод — если появится
    4-я шкала, гейт подхватит её без правки этого файла)."""
    path = Path(anchors_path)
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    scales = data.get("scales", data) if isinstance(data, Mapping) else {}
    return sorted(k for k in scales if isinstance(k, str))


def load_set_labels(anchors_path: Path | str, scale_id: str) -> list[str]:
    """
    Best-effort метки наборов (поле `label`) — только для читаемости диагностического
    вывода, НЕ участвуют в вычислении гейта (за это отвечает ssr_core.load_anchor_sets,
    который метки намеренно игнорирует как служебное поле). Если меток нет/формат
    неожиданный — просто возвращает "набор N", гейт от этого не падает.
    """
    try:
        path = Path(anchors_path)
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        scales = data.get("scales", data) if isinstance(data, Mapping) else {}
        raw_sets = (scales.get(scale_id) or {}).get("anchor_sets") or []
        labels = []
        for i, raw in enumerate(raw_sets):
            label = raw.get("label") if isinstance(raw, Mapping) else None
            labels.append(str(label) if label else f"набор {i + 1}")
        return labels
    except Exception:
        return []


def build_backend(model: str, prefix: str = "", device: str = "cpu") -> ssr_core.SentenceTransformerBackend:
    return ssr_core.SentenceTransformerBackend(model_name=model, device=device, prefix=prefix)


# ============================================================================
# Ядро гейта
# ============================================================================


def leave_one_set_out_monotonicity(
    anchor_sets: Sequence[Mapping[int, str]],
    response_backend: ssr_core.EmbeddingBackend,
    anchor_backend: Optional[ssr_core.EmbeddingBackend] = None,
    epsilon: float = 0.001,
    pmf_temperature: float = 1.0,
    labels: Optional[Sequence[str]] = None,
) -> list[SetMonotonicityResult]:
    """
    Условие (a) гейта (см. докстринг модуля). `anchor_backend` — отдельный бэкенд для
    РОЛИ "эталонный якорь" (документ), если он отличается от роли "проверяемый ответ"
    (query) — нужно для асимметричных схем префиксов (например ru-en-RoSBERTa,
    search_query/search_document, см. embedder_ab.py). По умолчанию совпадает с
    response_backend — единый префикс на обе роли (текущая продакшн-схема ssr_core.py).

    Каждый набор кодируется РОВНО ОДИН РАЗ на роль (не n_sets^2 кодирований) — набор i
    в роли "ответ" кодируется response_backend, в роли "эталон" — anchor_backend; порядок
    leave-one-out не требует перекодирования, только разного комбинирования уже готовых
    эмбеддингов.
    """
    anchor_backend = anchor_backend or response_backend
    n = len(anchor_sets)
    labels = list(labels) if labels else [f"набор {i + 1}" for i in range(n)]
    texts_by_set = [[s[lvl] for lvl in LEVELS] for s in anchor_sets]
    query_embs_by_set = [response_backend.encode(t) for t in texts_by_set]
    doc_embs_by_set = [anchor_backend.encode(t) for t in texts_by_set]

    results = []
    for j in range(n):
        response_embs = query_embs_by_set[j]
        per_set_pmfs = [
            ssr_core.pmf_single_anchor_set(response_embs, doc_embs_by_set[i], epsilon, pmf_temperature)
            for i in range(n)
            if i != j
        ]
        avg_pmf = ssr_core.average_pmfs(per_set_pmfs)
        e_vals = ssr_core.expected_value(avg_pmf).flatten().tolist()
        diffs = [e_vals[k + 1] - e_vals[k] for k in range(4)]
        violations = [(k + 1, k + 2) for k, d in enumerate(diffs) if d <= 0]
        results.append(
            SetMonotonicityResult(set_index=j, label=labels[j], e_values=e_vals, violations=violations)
        )
    return results


def bootstrap_pair_inversion_probability(
    e_values_from: Sequence[float],
    e_values_to: Sequence[float],
    n_iters: int = RANK_RECOVERY_BOOTSTRAP_ITERS,
    seed: int = RANK_RECOVERY_BOOTSTRAP_SEED,
) -> float:
    """
    P(инверсия) для ОДНОГО перехода (уровень k -> уровень k+1) условия (c) v2 — см.
    "ИСТОРИЯ УСЛОВИЯ (c) — v1.4 -> v1.4 fix" в докстринге модуля. Ресэмплинг С
    ВОЗВРАЩЕНИЕМ, ПО ФРАЗАМ (не по респондентам — в отличие от
    ssr_core.joint_paired_bootstrap_means, который резэмплирует респондентов для
    ПАРНОГО сравнения нескольких стимулов ОДНОГО прогона). Здесь пары нет: фразы
    уровня k и уровня k+1 — два НЕЗАВИСИМЫХ множества (разных, возможно разного
    размера — банк F1 §1 review_v1.4.md использует 5 фраз/уровень, банк B2 — 10),
    без естественного соответствия "фраза i уровня k" <-> "фраза i уровня k+1",
    поэтому резэмплирование НЕЗАВИСИМОЕ для каждого уровня (не joint-paired).

    Один `rng`, использованный ПОСЛЕДОВАТЕЛЬНО (сначала индексы уровня k, потом
    уровня k+1) — детерминировано при фиксированном seed (тот же принцип
    воспроизводимости, что и everywhere в ssr_core.py: `np.random.default_rng(seed)`).

    Возвращает долю итераций, где резэмплированное среднее уровня k СТРОГО ВЫШЕ
    резэмплированного среднего уровня k+1 (то есть "неправильный", инвертированный
    порядок — по конструкции шкалы уровень k+1 обязан быть силнее/выше уровня k).
    """
    a = np.asarray(e_values_from, dtype=np.float64)
    b = np.asarray(e_values_to, dtype=np.float64)
    if a.size == 0 or b.size == 0:
        raise ValueError("bootstrap_pair_inversion_probability: пустой массив E-значений уровня")
    rng = np.random.default_rng(seed)
    idx_a = rng.integers(0, a.size, size=(n_iters, a.size))
    idx_b = rng.integers(0, b.size, size=(n_iters, b.size))
    means_a = a[idx_a].mean(axis=1)
    means_b = b[idx_b].mean(axis=1)
    return float(np.mean(means_a > means_b))


def rank_recovery(
    paraphrase_bank_scale: Mapping[int, Sequence[str]],
    anchor_sets: Sequence[Mapping[int, str]],
    response_backend: ssr_core.EmbeddingBackend,
    anchor_backend: Optional[ssr_core.EmbeddingBackend] = None,
    epsilon: float = 0.001,
    pmf_temperature: float = 1.0,
    bootstrap_iters: int = RANK_RECOVERY_BOOTSTRAP_ITERS,
    bootstrap_seed: int = RANK_RECOVERY_BOOTSTRAP_SEED,
) -> RankRecoveryResult:
    """Условие (c) гейта, v2 (бутстреп) — см. докстринг модуля. Скорит held-out
    парафразы ПОЛНЫМ ансамблем всех наборов шкалы разом (как в продакшн-пайплайне
    ssr_core.SSREngine), без leave-one-out — тест на генерализацию, а не на
    взаимную согласованность. В отличие от v1.4 (точечное сравнение средних),
    для каждого из 4 соседних переходов ДОПОЛНИТЕЛЬНО считается бутстреп
    P(инверсия) (см. bootstrap_pair_inversion_probability) — FAIL гейта даёт
    только УВЕРЕННЫЙ разворот (P >= RANK_RECOVERY_INVERSION_CONFIDENCE_THRESHOLD)."""
    anchor_backend = anchor_backend or response_backend
    doc_embs_by_set = [anchor_backend.encode([s[lvl] for lvl in LEVELS]) for s in anchor_sets]

    mean_e_by_level: list[float] = []
    e_values_by_level: dict[int, list[float]] = {}
    for lvl in LEVELS:
        texts = list(paraphrase_bank_scale[lvl])
        response_embs = response_backend.encode(texts)
        per_set_pmfs = [
            ssr_core.pmf_single_anchor_set(response_embs, doc_embs, epsilon, pmf_temperature)
            for doc_embs in doc_embs_by_set
        ]
        avg_pmf = ssr_core.average_pmfs(per_set_pmfs)
        e_vals = ssr_core.expected_value(avg_pmf).flatten()
        e_values_by_level[lvl] = e_vals.tolist()
        mean_e_by_level.append(float(e_vals.mean()))

    transitions: list[RankRecoveryTransition] = []
    for k in range(4):
        lvl_from, lvl_to = LEVELS[k], LEVELS[k + 1]
        p_inversion = bootstrap_pair_inversion_probability(
            e_values_by_level[lvl_from],
            e_values_by_level[lvl_to],
            n_iters=bootstrap_iters,
            seed=bootstrap_seed,
        )
        point_diff = mean_e_by_level[k + 1] - mean_e_by_level[k]
        transitions.append(
            RankRecoveryTransition(
                level_from=lvl_from, level_to=lvl_to, point_diff=point_diff, p_inversion=p_inversion
            )
        )

    return RankRecoveryResult(
        mean_e_by_level=mean_e_by_level,
        e_values_by_level=e_values_by_level,
        transitions=transitions,
        bootstrap_iters=bootstrap_iters,
        bootstrap_seed=bootstrap_seed,
    )


def evaluate_scale(
    scale_id: str,
    anchor_sets: Sequence[Mapping[int, str]],
    response_backend: ssr_core.EmbeddingBackend,
    anchor_backend: Optional[ssr_core.EmbeddingBackend] = None,
    epsilon: float = 0.001,
    pmf_temperature: float = 1.0,
    min_monotonic_sets: int = 3,
    labels: Optional[Sequence[str]] = None,
    paraphrase_bank: Optional[Mapping[str, Mapping[int, Sequence[str]]]] = None,
) -> ScaleGateResult:
    # v1.4: банк парафразов больше не хардкод в этом файле — по умолчанию грузится из
    # references/paraphrase_bank_ru.yaml (>= 10 фраз/уровень, см. load_paraphrase_bank).
    # evaluate_gate() резолвит и передаёт готовый bank на все шкалы разом (один парс YAML,
    # не по разу на шкалу) — прямой вызов evaluate_scale() без явного paraphrase_bank всё
    # равно подхватит тот же дефолт, просто перечитает файл.
    resolved_bank = paraphrase_bank if paraphrase_bank is not None else load_paraphrase_bank()
    bank = resolved_bank.get(scale_id)
    if not bank:
        raise KeyError(
            f"Нет банка парафразов для rank-recovery шкалы '{scale_id}' в "
            f"references/paraphrase_bank_ru.yaml — добавьте >= 10 фраз на каждый уровень 1..5."
        )
    set_results = leave_one_set_out_monotonicity(
        anchor_sets, response_backend, anchor_backend, epsilon, pmf_temperature, labels
    )
    rr = rank_recovery(bank, anchor_sets, response_backend, anchor_backend, epsilon, pmf_temperature)
    return ScaleGateResult(
        scale_id=scale_id, set_results=set_results, rank_recovery=rr, min_monotonic_sets=min_monotonic_sets
    )


def evaluate_gate(
    anchors_path: Path | str = DEFAULT_ANCHORS_PATH,
    response_backend: Optional[ssr_core.EmbeddingBackend] = None,
    anchor_backend: Optional[ssr_core.EmbeddingBackend] = None,
    epsilon: float = 0.001,
    pmf_temperature: float = 1.0,
    min_anchor_sets: int = 4,
    min_monotonic_sets: int = 3,
    model_name: str = "",
    prefix: str = "",
    paraphrase_bank: Optional[Mapping[str, Mapping[int, Sequence[str]]]] = None,
) -> GateReport:
    """Прогоняет весь гейт §1.2 п.2 на ВСЕХ шкалах, найденных в anchors_path."""
    if response_backend is None:
        raise ValueError("evaluate_gate: нужен response_backend (EmbeddingBackend)")
    # Резолвим банк парафразов ОДИН раз для всего прогона (не по разу на шкалу) — если
    # paraphrase_bank не передан явно, грузим references/paraphrase_bank_ru.yaml (дефолтный
    # путь DEFAULT_PARAPHRASE_BANK_PATH, v1.4: >= 10 фраз/уровень, полноценный гейт условия (c)).
    resolved_bank = paraphrase_bank if paraphrase_bank is not None else load_paraphrase_bank()
    report = GateReport(model_name=model_name, prefix=prefix)
    for scale_id in discover_scale_ids(anchors_path):
        _, anchor_sets = ssr_core.load_anchor_sets(anchors_path, scale_id)
        if len(anchor_sets) < min_anchor_sets:
            raise ValueError(
                f"Шкала '{scale_id}': {len(anchor_sets)} набор(ов) якорей < min_anchor_sets={min_anchor_sets}"
            )
        labels = load_set_labels(anchors_path, scale_id)
        report.scale_results[scale_id] = evaluate_scale(
            scale_id,
            anchor_sets,
            response_backend,
            anchor_backend,
            epsilon,
            pmf_temperature,
            min_monotonic_sets,
            labels,
            paraphrase_bank,
        )
    return report


# ============================================================================
# Форматированный вывод (переиспользуется embedder_ab.py)
# ============================================================================


def format_report(report: GateReport, verbose: bool = True) -> str:
    lines = []
    header = f"=== Гейт монотонности якорей: {report.model_name or '(без имени)'}"
    if report.prefix:
        header += f" | prefix={report.prefix!r}"
    header += " ==="
    lines.append(header)
    for scale_id, sr in report.scale_results.items():
        lines.append("")
        lines.append(f"--- Шкала: {scale_id} ---")
        if verbose:
            for r in sr.set_results:
                e_str = " -> ".join(f"{v:.3f}" for v in r.e_values)
                verdict = "OK" if r.monotonic else f"НЕМОНОТОНЕН {r.violations}"
                lines.append(f"  [{r.label:28s}] E(1..5) = {e_str}   {verdict}")
        lines.append(
            f"  Монотонных наборов: {sr.n_monotonic}/{sr.n_sets} "
            f"(порог >= {sr.min_monotonic_sets}) -> {'PASS' if sr.n_monotonic >= sr.min_monotonic_sets else 'FAIL'}"
        )
        lines.append(
            f"  Среднее E(4)={sr.mean_e4:.3f}, среднее E(5)={sr.mean_e5:.3f} "
            f"-> {'PASS (E5>E4)' if sr.e5_gt_e4 else 'FAIL (E5<=E4)'}"
        )
        rr_str = " -> ".join(f"{v:.3f}" for v in sr.rank_recovery.mean_e_by_level)
        lines.append(
            f"  Rank-recovery (held-out парафразы, критерий (c) v2: бутстреп по фразам, "
            f"{sr.rank_recovery.bootstrap_iters} итераций, seed={sr.rank_recovery.bootstrap_seed}) "
            f"E(1..5) = {rr_str} "
            f"-> {'PASS' if sr.rank_recovery.monotonic else f'FAIL (уверенный разворот) {sr.rank_recovery.confident_reversals}'}"
        )
        for t in sr.rank_recovery.transitions:
            if t.confident_reversal:
                lines.append(
                    f"    Переход ({t.level_from},{t.level_to}): точечно {t.point_diff:+.3f}, "
                    f"бутстреп P(инверсия)={t.p_inversion:.3f} >= "
                    f"{RANK_RECOVERY_INVERSION_CONFIDENCE_THRESHOLD:.2f} -> УВЕРЕННЫЙ РАЗВОРОТ (FAIL)"
                )
            elif t.near_tie:
                lines.append(
                    f"    Переход ({t.level_from},{t.level_to}): точечно {t.point_diff:+.3f} "
                    f"(немонотонно по букве старого точечного критерия), но бутстреп "
                    f"P(инверсия)={t.p_inversion:.3f} < {RANK_RECOVERY_INVERSION_CONFIDENCE_THRESHOLD:.2f} "
                    f"-> WARNING: near-tie, в пределах шума (не провал)"
                )
        lines.append(f"  ИТОГ шкалы '{scale_id}': {'PASS' if sr.passed else 'FAIL'}")
    lines.append("")
    lines.append(f"=== ОБЩИЙ ВЕРДИКТ ГЕЙТА: {'PASS' if report.passed else 'FAIL'} ===")
    return "\n".join(lines)


# ============================================================================
# CLI
# ============================================================================


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Гейт монотонности якорных наборов SSR (§1.2 п.2)")
    parser.add_argument("--anchors", default=str(DEFAULT_ANCHORS_PATH), help="Путь к anchors_ru.yaml")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Путь к config.yaml (дефолты)")
    parser.add_argument(
        "--paraphrase-bank",
        default=str(DEFAULT_PARAPHRASE_BANK_PATH),
        help="Путь к paraphrase_bank_ru.yaml (банк held-out парафразов для условия (c), v1.4)",
    )
    parser.add_argument("--model", default=None, help="HF-имя embedding-модели (иначе — из config.yaml)")
    parser.add_argument("--prefix", default=None, help="Префикс перед кодированием (иначе — из config.yaml)")
    parser.add_argument("--device", default=None, help="cpu/cuda/mps (иначе — из config.yaml)")
    parser.add_argument("--epsilon", type=float, default=None)
    parser.add_argument("--pmf-temperature", type=float, default=None)
    parser.add_argument("-v", "--verbose", action="store_true", help="Печатать таблицу E по каждому набору")
    args = parser.parse_args(argv)

    emb_cfg = load_embedding_config(args.config)
    ssr_cfg = load_ssr_config(args.config)
    model = args.model or emb_cfg["model"]
    prefix = args.prefix if args.prefix is not None else emb_cfg["prefix"]
    device = args.device or emb_cfg["device"]
    epsilon = args.epsilon if args.epsilon is not None else ssr_cfg["epsilon"]
    pmf_temperature = args.pmf_temperature if args.pmf_temperature is not None else ssr_cfg["pmf_temperature"]

    # Ловушка, в которую реально попал автор этого файла при A/B-сравнении эмбеддеров:
    # --model без --prefix молча наследует prefix ИЗ config.yaml, который может быть
    # настроен под СОВЕРШЕННО ДРУГУЮ модель (например, после того как embedder_ab.py
    # уже зафиксировал победителя) — числа при этом выглядят правдоподобно, но считаются
    # на мусорном входе (чужой префикс приклеен к чужой модели). Явно предупреждаем.
    if args.model is not None and args.prefix is None and model != emb_cfg["model"]:
        print(
            f"ВНИМАНИЕ: --model {model!r} задан явно, но --prefix не передан — "
            f"унаследован prefix={emb_cfg['prefix']!r} из {args.config} (там настроен для "
            f"{emb_cfg['model']!r}, ДРУГОЙ модели). Если это не тот префикс, который нужен "
            f"для {model!r} — передайте --prefix явно (пустой строкой, если модель без префикса).",
            file=sys.stderr,
        )

    print(f"Загружаю эмбеддер: {model} (prefix={prefix!r}, device={device})...", file=sys.stderr)
    backend = build_backend(model, prefix, device)
    paraphrase_bank = load_paraphrase_bank(args.paraphrase_bank)
    report = evaluate_gate(
        anchors_path=args.anchors,
        response_backend=backend,
        epsilon=epsilon,
        pmf_temperature=pmf_temperature,
        min_anchor_sets=ssr_cfg["min_anchor_sets"],
        model_name=model,
        prefix=prefix,
        paraphrase_bank=paraphrase_bank,
    )
    print(format_report(report, verbose=args.verbose))
    return 0 if report.passed else 1


# ============================================================================
# Unittest-обёртка (регрессия — "выбранный стек" из config.yaml)
# ============================================================================


class TestAnchorGateOnConfiguredStack(unittest.TestCase):
    """
    Гейт §1.2 п.2 на эмбеддере, зафиксированном в config.yaml — то есть "выбранном стеке"
    после embedder_ab.py. ТРЕБУЕТ СЕТЬ при первом запуске (скачивание модели), если она ещё
    не в кэше venv — это осознанное отличие от test_ssr.py (см. модульный докстринг).

    Если этот тест красный — config.yaml указывает на эмбеддер/anchors_ru.yaml, не прошедшие
    гейт §1.2 п.2: см. DoD spec_synthetic-panel_v1.3.md, "красный гейт = сборка не принята".
    """

    @classmethod
    def setUpClass(cls):
        cls.emb_cfg = load_embedding_config()
        cls.ssr_cfg = load_ssr_config()
        cls.backend = build_backend(cls.emb_cfg["model"], cls.emb_cfg["prefix"], cls.emb_cfg["device"])

    def test_gate_passes_on_configured_stack(self):
        report = evaluate_gate(
            anchors_path=DEFAULT_ANCHORS_PATH,
            response_backend=self.backend,
            epsilon=self.ssr_cfg["epsilon"],
            pmf_temperature=self.ssr_cfg["pmf_temperature"],
            min_anchor_sets=self.ssr_cfg["min_anchor_sets"],
            model_name=self.emb_cfg["model"],
            prefix=self.emb_cfg["prefix"],
        )
        print("\n" + format_report(report, verbose=True))
        failing = [sid for sid, sr in report.scale_results.items() if not sr.passed]
        self.assertTrue(
            report.passed,
            f"Гейт §1.2 п.2 провален на шкалах {failing} (эмбеддер {self.emb_cfg['model']!r}). "
            f"См. вывод format_report выше.",
        )


class TestParaphraseBankIntegrity(unittest.TestCase):
    """Быстрые структурные проверки references/paraphrase_bank_ru.yaml (БЕЗ эмбеддингов,
    без сети) — условие (c) гейта (rank_recovery, TestAnchorGateOnConfiguredStack) доверяет
    этим свойствам банка МОЛЧА (не перепроверяет их на каждый прогон), поэтому они должны
    быть гарантированы отдельно и всегда идти зелёными, даже если TestAnchorGateOnConfiguredStack
    пропущен (нет сети/модели не в кэше)."""

    @classmethod
    def setUpClass(cls):
        cls.raw_bank = load_paraphrase_bank_raw()
        with Path(DEFAULT_PARAPHRASE_BANK_PATH).open("r", encoding="utf-8") as f:
            cls.meta = (yaml.safe_load(f) or {}).get("meta", {}) or {}

    def test_every_scale_from_anchors_has_bank_with_all_five_levels(self):
        for scale_id in discover_scale_ids():
            self.assertIn(scale_id, self.raw_bank, f"нет банка парафразов для шкалы {scale_id}")
            bank = self.raw_bank[scale_id]
            for lvl in LEVELS:
                self.assertIn(lvl, bank, f"{scale_id}: нет уровня {lvl} в банке парафразов")

    def test_at_least_ten_phrases_per_level_per_scale(self):
        # Жёсткий пол >= 10 (spec_synthetic-panel_v1.4.md §2.1), НЕ просто "что скажет meta" —
        # так правка meta.min_phrases_per_level в сторону уменьшения не тихо ослабляет контракт.
        for scale_id, levels in self.raw_bank.items():
            for lvl, items in levels.items():
                self.assertGreaterEqual(
                    len(items), 10, f"{scale_id}[{lvl}]: {len(items)} фраз(ы) < 10 (spec §2.1 минимум)"
                )

    def test_style_field_present_and_within_declared_vocabulary(self):
        allowed_styles = set(self.meta.get("styles") or [])
        self.assertTrue(allowed_styles, "meta.styles пуст или отсутствует в paraphrase_bank_ru.yaml")
        for scale_id, levels in self.raw_bank.items():
            for lvl, items in levels.items():
                for item in items:
                    style = item.get("style")
                    self.assertIn(
                        style,
                        allowed_styles,
                        f"{scale_id}[{lvl}]: стиль {style!r} не из meta.styles {sorted(allowed_styles)} "
                        f"(фраза: {item.get('text')!r})",
                    )

    def test_no_exact_duplicate_texts_within_bank(self):
        for scale_id, levels in self.raw_bank.items():
            seen: dict[str, tuple[int, str]] = {}
            for lvl, items in levels.items():
                for item in items:
                    text = item["text"]
                    if text in seen:
                        self.fail(
                            f"{scale_id}: фраза {text!r} повторяется дословно на уровнях "
                            f"{seen[text][0]} ({seen[text][1]}) и {lvl} ({item['style']})"
                        )
                    seen[text] = (lvl, item["style"])

    def test_paraphrases_do_not_duplicate_anchor_phrases_verbatim(self):
        anchors_path = DEFAULT_ANCHORS_PATH
        for scale_id in discover_scale_ids(anchors_path):
            _, anchor_sets = ssr_core.load_anchor_sets(anchors_path, scale_id)
            anchor_texts = {s[lvl] for s in anchor_sets for lvl in LEVELS}
            bank = self.raw_bank.get(scale_id, {})
            for lvl, items in bank.items():
                for item in items:
                    self.assertNotIn(
                        item["text"],
                        anchor_texts,
                        f"{scale_id}[{lvl}]: парафраз дублирует anchor_sets дословно: {item['text']!r}",
                    )

    def test_paraphrase_bank_has_no_anchor_lexicon_overlap(self):
        """ГЛАВНАЯ проверка независимости банка (spec_synthetic-panel_v1.4.md §2.1: "БЕЗ
        лексики якорей v2 — проверить пересечения программно"). Если это красное — правка
        банка ввела фразу, использующую значимую словоформу из anchors_ru.yaml v2 (см.
        check_no_anchor_lexicon_overlap за списком стоп-слов и точным алгоритмом
        нормализации); сообщение об ошибке называет конкретную фразу и конкретный токен."""
        violations = check_no_anchor_lexicon_overlap(self.raw_bank, DEFAULT_ANCHORS_PATH)
        if violations:
            detail = "\n".join(
                f"  [{v.scale_id}][{v.level}][{v.style}] {v.text!r} -> {list(v.overlap_tokens)}"
                for v in violations
            )
            self.fail(
                f"{len(violations)} фраз(а) банка парафразов пересекается по значимой лексике "
                f"с anchors_ru.yaml v2:\n{detail}"
            )


if __name__ == "__main__":
    # Различаем "CLI-диагностика" и "unittest": если переданы CLI-флаги диагностики
    # (--model/--prefix/--device/--anchors отличный от дефолта и т.п.) — работаем как CLI.
    # По умолчанию (без аргументов) — обычный unittest-прогон, как у всех test_*.py в проекте.
    _cli_flags = {
        "--model", "--prefix", "--device", "--anchors", "--config", "--epsilon", "--pmf-temperature",
        "--paraphrase-bank",
    }
    if any(a in _cli_flags or a.startswith(tuple(f"{f}=" for f in _cli_flags)) for a in sys.argv[1:]):
        sys.exit(main())
    else:
        unittest.main(verbosity=2)

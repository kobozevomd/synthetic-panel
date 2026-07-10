#!/usr/bin/env python3
"""
cjm_lint.py — линтер честности cjm_report.md режима segment_map (spec_synthetic-panel_v1.1_segment_map.md §4).

    python scripts/cjm_lint.py --report runs/cjm_<имя>_<ts>/cjm_report.md
    python scripts/cjm_lint.py --report <report.md> --extra <02_cjm_seg1.md> <02_cjm_seg2.md>
    python scripts/cjm_lint.py --extra <02_cjm_seg1.md>          # ранняя проверка без готового отчёта

Зависимости: только стандартная библиотека (re, argparse, dataclasses) — линтер
не требует .venv (можно гонять любым python3, в т.ч. вне окружения скилла).

Exit 0 — нарушений не найдено (прогон можно считать завершённым, §1.2).
Exit 1 — печатается список нарушений с номерами строк, прогон НЕ завершён.

=== Правила (буквально §4 спецификации v1.1) ===

  1. Каждая строка, содержащая `\\d %` или `\\d%`, должна содержать тег источника
     из списка §1.2 (`[BA]`, `[Mediascope]`, `[DSM]`, `[Росстат]`, `[опрос]`,
     `[клиент]`) либо слово «оценка»/«гипотеза» (и словоформы) в той же строке.
  2. Слова «реальный отзыв», «реальная цитата» (и словоформы) допустимы ТОЛЬКО в
     строке с тегом источника `[BA]` или `[отзывы: <путь>]`; иначе — нарушение.
  3. Легенда карты доверия присутствует; каждый раздел отчёта имеет маркер 🟢/🟡/🔴.
  4. Запрещённые обещания: «% купят», «прогноз продаж», «Brand Lift»; формулировка
     точности метода — ТОЛЬКО «R=0,72 (~90% теоретического потолка)» (не «90%
     точности» и не любая другая обвязка слова «точност*» вокруг голого процента).

=== Операционные интерпретации мест, не заданных спецификацией механически
    (задокументировано здесь, чтобы F1-ревьюер / автор cjm_report_template.md
    видели ТОЧНОЕ правило, которое реально проверяется) ===

  - Правило 1/4 (слова «оценка»/«гипотеза»/«точност*»): матчатся ПО ОСНОВЕ слова
    (regex `\\w*`), чтобы ловить словоформы (оценка/оценки/оценочно,
    гипотеза/гипотезы, точность/точный/точно) — см. ESTIMATE_WORD_RE, ACCURACY_WORD_RE.
  - Правило 1 (находка №4, MAJOR, review_v1.1.md §3.4). PERCENT_RE ловит только
    цифровую форму («40%», «40 %») — количественное утверждение о доле можно
    сформулировать и БЕЗ символа «%»: числительным словом («сорок процентов»),
    цифрой без «%» («40 процентов») либо разговорной инверсией («процентов
    пять» = «около пяти процентов»). Всё это то же нарушение по духу §1.2
    спецификации, просто без «%». PERCENT_WORD_RE ловит все три формы, требуя
    числительное (цифрой ИЛИ словом) РЯДОМ со словом «процент» (один пробел,
    любой порядок). Голое слово «процент»/«процентов» БЕЗ числительного рядом
    (методологическая проза вида «правило процентов», «без процентов», «в
    процентах») НЕ матчится — это не количественное утверждение о доле, тегировать
    нечего. Отдельно исключена идиома «ни одного процента» и т.п. (утверждение
    ОТСУТСТВИЯ процента, не количественная доля — буквально встречается в
    чек-листах этого проекта, `runs/*/01_segmentation_run{1,2,3}.md`: «Ни одного
    процента, ни одного коэффициента значимости в тексте выше. ✅») через
    отрицательный lookbehind `(?<!ни )` — иначе честная фраза самопроверки ловилась
    бы как нарушение только потому, что «одного» формально является числительным.
  - Правило 2: строгое буквальное чтение спецификации — любое вхождение «реальн*
    отзыв*/цитат*» требует тега источника В ТОМ ЖЕ логическом блоке (см. ниже про
    блоки), без учёта отрицания («не реальная цитата» тоже требует тег). Отчёты
    должны вместо отрицания использовать нейтральную формулировку «синтетическая
    иллюстрация» (см. §6 спецификации — она прямо предписывает эту замену), а не
    «не реальная цитата». Ограничение метода: строка, которая ОПИСЫВАЕТ само это
    правило текстом (а не нарушает его) — например, легенда/методичка, дословно
    поясняющая "запрещена формулировка «реальный отзыв» без источника" — тоже
    будет поймана линтером, поскольку регэксп не отличает описание правила от
    его нарушения. Это осознанный компромисс простого механического чекера, не
    баг: авторам легенды стоит перефразировать пояснение так, чтобы не повторять
    сам триггер (например, «цитаты без указания источника недопустимы»).
  - «Логический блок» (для правил 1/2/4, НЕ для правила 3): физические строки
    прозы в этом проекте систематически завёрнуты на ~80 знаков (см. реальный
    references/disclaimers.md) — проверка "тег на той же ФИЗИЧЕСКОЙ строке"
    ловила бы ложные срабатывания на каждом переносе предложения. Поэтому
    правила 1/2/4 матчатся не на физической строке, а на "блоке" — максимальном
    прогоне строк одного абзаца/пункта списка, склеенном в одну логическую
    строку (см. split_into_blocks): заголовки ВСЕГДА атомарны (одна строка = один
    блок, следующая строка не может быть их "продолжением"); элемент списка
    (-/*/+/N.) или blockquote (>) начинает новый блок, но допускает "ленивое"
    продолжение (word-wrap) на следующих НЕ-заголовочных/НЕ-списочных строках;
    пустая строка закрывает текущий блок. Номер строки нарушения — это номер
    ПЕРВОЙ физической строки блока.
    РЕШЕНО [находка №1, CRITICAL, review_v1.1.md §3.2]: строка markdown-ТАБЛИЦЫ
    (`^\\s*\\|`, см. TABLE_ROW_RE) — тоже ВСЕГДА атомарный однострочный блок, той
    же природы, что заголовок: следующая строка НЕ считается её "продолжением",
    даже другая строка ТОЙ ЖЕ таблицы (у markdown-таблиц физически не может быть
    пустых строк между рядами, в отличие от прозы). До этой правки все подряд
    идущие строки таблицы склеивались в ОДИН блок (как обычная проза), из-за
    чего один корректно помеченный ряд «отмывал» непомеченные проценты/цитаты в
    соседних рядах ТОЙ ЖЕ таблицы — правила 1/2/4 проверяли "есть ли тег
    ГДЕ-ТО в блоке", а блок внезапно оказывался целой таблицей, а не одним рядом.
    Тег/маркер/слово-оценка теперь требуется на уровне РЯДА таблицы (все ячейки
    одного ряда — один блок, проверяется как строка целиком, без разбора по
    отдельным ячейкам — этого достаточно, чтобы один ряд не прикрывал другой;
    заголовочный ряд и ряд-разделитель `|---|---|` — тоже атомарные блоки,
    безобидные для всех 4 правил, так как не содержат ни процентов, ни цитат).
  - Правило 3, «раздел»: markdown-заголовок уровня `## ` (H2). Проверяется, что
    ГДЕ-ТО внутри блока от заголовка H2 до следующего заголовка уровня <=2
    (включительно все вложенные H3+) встречается 🟢/🟡/🔴 — маркер не обязан быть
    буквально в тексте самого заголовка (иначе не прошёл бы даже раздел
    «Легенда», который поясняет маркеры в теле, а не в заголовке). Контент ДО
    первого заголовка (обычно H1-титул) — не считается «разделом» и не проверяется.
  - Правило 3, «легенда присутствует»: (а) где-то в документе есть строка со
    словом «легенда» (без учёта регистра) И (б) все три маркера 🟢🟡🔴 встречаются
    в документе хотя бы по разу. Для --extra БЕЗ --report легенда проверяется по
    ОБЪЕДИНЁННОМУ тексту всех переданных файлов (условно — «отчёт ещё не собран,
    проверяем черновики вместе»); секционная проверка (H2-маркеры) — ПО КАЖДОМУ
    файлу отдельно (не имеет смысла требовать легенду от одного 02_cjm_*.md).
  - **Несъёмный блок дисклеймеров исключён из всех 4 правил.** references/disclaimers.md
    документирует блок между маркерами `<!-- DISCLAIMER_BLOCK_START -->` /
    `<!-- DISCLAIMER_BLOCK_END -->` (и калиброванный вариант `..._CALIBRATED_...`),
    который копируется в report.md ДОСЛОВНО и правилом SKILL.md никогда не
    редактируется. Этот текст закономерно и правомерно упоминает «Brand Lift»,
    «прогноз продаж», «медиамикс» — но ОБЪЯСНЯЯ, что они НЕ оценивались отчётом
    (это дисклеймер, а не обещание), и содержит незатегованный процент в
    канонической формулировке точности. Простой регэксп не отличает "предупреждаю
    про X" от "обещаю X", поэтому весь блок между этими маркерами (включая сами
    строки-маркеры) МАСКИРУЕТСЯ (заменяется на пустые строки, см.
    mask_reference_blocks) ДО применения всех 4 правил — как в исходном файле
    references/disclaimers.md, так и в любом report.md/cjm_report.md, куда он
    скопирован дословно. Номера строк остального документа не сдвигаются.
    РЕШЕНО [F2, пилот cjm_hairloss_demo]: references/disclaimers.md содержит
    именно такой отдельный блок для cjm-отчётов (раздел «Легенда карты доверия
    и блок для cjm-отчётов», маркеры `DISCLAIMER_BLOCK_CJM_START/END`) — его
    маркеры добавлены в DISCLAIMER_BLOCK_MARKERS ниже (третья пара кортежа).
    Как и раньше: сам легендовый текст (со словом «Легенда» и 🟢/🟡/🔴) в
    МАСКИРУЕМЫЙ блок класть не стоит — иначе правило 3а («легенда присутствует»)
    перестанет её видеть; в references/disclaimers.md легенда физически лежит
    ДО маркера `DISCLAIMER_BLOCK_CJM_START`, вне маскируемого блока — это
    сохраняет её видимой для правила 3а.

Юнит-тесты: scripts/test_cjm_lint.py (фикстуры на каждое правило + чистый
образец + самотест на настоящем references/cjm_report_template.md, если он уже
существует).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ============================================================================
# Правило 1: проценты без источника
# ============================================================================

PERCENT_RE = re.compile(r"\d\s?%")
SOURCE_TAGS = ("[BA]", "[Mediascope]", "[DSM]", "[Росстат]", "[опрос]", "[клиент]")
# \b обязателен: без границы слова "оцен" ложно матчится ВНУТРИ обычного слова
# "проЦЕНТ"/"проЦЕНТы" (про+оцен+т — случайное наложение морфем), а "процент(ы)"
# неизбежно встречается в отчёте о процентах на каждом шагу (см. test_cjm_lint.py,
# test_percent_without_space_is_also_matched — этот баг пойман юнит-тестом).
ESTIMATE_WORD_RE = re.compile(r"\bоцен\w*|\bгипотез\w*", re.IGNORECASE)
# Каноническая формулировка точности метода (см. CLAUDE.md проекта и
# references/disclaimers.md) сама содержит непомеченный процент ("~90%
# теоретического потолка") — это не "выдуманная модельная доля", а фиксированная
# ссылка на валидационное исследование метода, поэтому она освобождена от
# требования тега источника в правиле 1 (см. check_percent_sourcing ниже), а не
# только упомянута как "правильный" вариант в правиле 4.
ACCURACY_CANONICAL_MARKERS = ("теоретического потолка", "r=0,72", "r=0.72")

# Находка №4, MAJOR (review_v1.1.md §3.4) — числительное словом или голой
# цифрой (без "%") рядом со словом «процент» — см. докстринг модуля, пункт
# "Правило 1 (находка №4...)" выше, там же обоснование lookbehind (?<!ни ).
_NUMBER_WORDS_RU = (
    r"ноль|одна?|две|три|четыре|пять|шесть|семь|восемь|девять|"
    r"десять|одиннадцать|двенадцать|тринадцать|четырнадцать|пятнадцать|"
    r"шестнадцать|семнадцать|восемнадцать|девятнадцать|"
    r"двадцать|тридцать|сорок|пятьдесят|шестьдесят|семьдесят|восемьдесят|девяносто|"
    r"сто|двести|триста|четыреста|пятьсот|шестьсот|семьсот|восемьсот|девятьсот|"
    r"полтора|полторы|тысяча|тысячи|тысяч"
)
_NUMBER_TOKEN_RU = rf"(?:\d+|(?:{_NUMBER_WORDS_RU})\w*)"
PERCENT_WORD_RE = re.compile(
    rf"(?<!ни )\b{_NUMBER_TOKEN_RU}\s+процент\w*|\bпроцент\w*\s+(?<!ни ){_NUMBER_TOKEN_RU}\b",
    re.IGNORECASE,
)


@dataclass
class Violation:
    rule: int  # 1..4
    line: int  # 1-indexed; 0 = относится к документу в целом (не к конкретной строке)
    message: str
    excerpt: str = field(default="")


# ============================================================================
# Маскировка несъёмного блока дисклеймеров (см. модульный docstring)
# ============================================================================

DISCLAIMER_BLOCK_MARKERS = (
    ("<!-- DISCLAIMER_BLOCK_START -->", "<!-- DISCLAIMER_BLOCK_END -->"),
    ("<!-- DISCLAIMER_BLOCK_CALIBRATED_START -->", "<!-- DISCLAIMER_BLOCK_CALIBRATED_END -->"),
    # Добавлено интегратором [F2] при сборке пилота cjm_hairloss_demo: закрывает
    # ровно тот пробел, который сам этот докстринг предсказывал выше ("если в
    # references/disclaimers.md появится ОТДЕЛЬНЫЙ блок специально для
    # cjm-отчётов ... согласуйте с этим файлом добавление нового имени маркера").
    # references/disclaimers.md действительно содержит такой блок (раздел
    # «Легенда карты доверия и блок для cjm-отчётов»), но до этой правки его
    # маркеры не были в этом списке — cjm_report.md, честно копирующий блок
    # ДОСЛОВНО (включая упоминания «Brand Lift», «прогноз продаж» и фразы
    # «реальный отзыв»/«реальная цитата» — эти слова там объясняют правило, а
    # не нарушают его), ловил ложные срабатывания правил 2 и 4 ровно на этом
    # легитимном тексте. См. issues в финальном отчёте сборщика [F2].
    ("<!-- DISCLAIMER_BLOCK_CJM_START -->", "<!-- DISCLAIMER_BLOCK_CJM_END -->"),
)


def mask_reference_blocks(lines: list[str]) -> list[str]:
    """
    Заменяет строки между (и включая) маркерами DISCLAIMER_BLOCK_MARKERS на пустые
    строки — сохраняя длину списка и номера строк ВНЕ маскируемых блоков. См.
    docstring модуля, пункт «Несъёмный блок дисклеймеров исключён из всех 4
    правил». Незакрытый маркер START без END маскирует до конца документа
    (лучше перебдеть и не проверить дисклеймер строгими правилами, чем поймать
    ложное нарушение в заведомо вычитанном несъёмном тексте).
    """
    out = list(lines)
    masking = False
    for i, line in enumerate(out):
        stripped = line.strip()
        if not masking and any(stripped == start for start, _ in DISCLAIMER_BLOCK_MARKERS):
            masking = True
            out[i] = ""
            continue
        if masking and any(stripped == end for _, end in DISCLAIMER_BLOCK_MARKERS):
            masking = False
            out[i] = ""
            continue
        if masking:
            out[i] = ""
    return out


# ============================================================================
# Логические блоки (защита правил 1/2/4 от word-wrap — см. модульный docstring)
# ============================================================================

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
LIST_ITEM_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s+")
BLOCKQUOTE_RE = re.compile(r"^\s*>")
# Находка №1, CRITICAL (review_v1.1.md §3.2) — строка markdown-таблицы (в т.ч.
# заголовок таблицы и ряд-разделитель `|---|---|`). Атомарна, как заголовок —
# см. split_into_blocks ниже и докстринг модуля, раздел «Логический блок».
TABLE_ROW_RE = re.compile(r"^\s*\|")


def split_into_blocks(lines: list[str]) -> list[tuple[int, str]]:
    """
    Группирует физические строки в логические блоки для правил 1/2/4 (см. docstring
    модуля, раздел «Логический блок»). Возвращает список (start_line_1indexed,
    joined_text). Заголовки И строки markdown-таблиц — всегда атомарные
    однострочные блоки (следующая строка никогда не считается их продолжением,
    даже другая строка ТОЙ ЖЕ таблицы — находка №1 review_v1.1.md: у таблиц нет
    пустых строк между рядами, поэтому без этого правила все ряды одной таблицы
    склеивались бы в один блок, и тег в одном ряду прикрывал бы непомеченные
    проценты/цитаты в соседних); элементы списка/blockquote начинают новый блок,
    но допускают ленивое (word-wrap) продолжение прозы; пустая строка закрывает
    текущий блок.
    """
    blocks: list[tuple[int, list[str]]] = []
    current: list[str] = []
    current_start = 0

    def flush() -> None:
        nonlocal current
        if current:
            blocks.append((current_start, current))
            current = []

    for i, line in enumerate(lines):
        if not line.strip():
            flush()
            continue
        if HEADING_RE.match(line):
            flush()
            blocks.append((i + 1, [line]))
            continue
        if TABLE_ROW_RE.match(line):
            flush()
            blocks.append((i + 1, [line]))
            continue
        if LIST_ITEM_RE.match(line) or BLOCKQUOTE_RE.match(line) or not current:
            flush()
            current = [line]
            current_start = i + 1
        else:
            current.append(line)
    flush()

    return [(start, " ".join(l.strip() for l in block_lines)) for start, block_lines in blocks]


def check_percent_sourcing(blocks: list[tuple[int, str]]) -> list[Violation]:
    violations = []
    for start_line, text in blocks:
        if not PERCENT_RE.search(text) and not PERCENT_WORD_RE.search(text):
            continue
        has_tag = any(tag in text for tag in SOURCE_TAGS)
        has_estimate_word = bool(ESTIMATE_WORD_RE.search(text))
        has_accuracy_marker = any(marker in text.lower() for marker in ACCURACY_CANONICAL_MARKERS)
        if not has_tag and not has_estimate_word and not has_accuracy_marker:
            violations.append(
                Violation(
                    rule=1,
                    line=start_line,
                    message=(
                        "Процент (в т.ч. числительным словом, без «%» — находка №4 "
                        "review_v1.1.md) без тега источника ([BA]/[Mediascope]/[DSM]/"
                        "[Росстат]/[опрос]/[клиент]) и без слова «оценка»/«гипотеза»."
                    ),
                    excerpt=text,
                )
            )
    return violations


# ============================================================================
# Правило 2: "реальный отзыв"/"реальная цитата" без источника
# ============================================================================

REAL_QUOTE_RE = re.compile(r"реальн\w*\s+(отзыв\w*|цитат\w*)", re.IGNORECASE)
REAL_QUOTE_SOURCE_RE = re.compile(r"\[BA\]|\[отзывы:\s*[^\]]+\]")


def check_real_quote_sourcing(blocks: list[tuple[int, str]]) -> list[Violation]:
    violations = []
    for start_line, text in blocks:
        if REAL_QUOTE_RE.search(text) and not REAL_QUOTE_SOURCE_RE.search(text):
            violations.append(
                Violation(
                    rule=2,
                    line=start_line,
                    message="«Реальный отзыв/цитата» без тега источника [BA] или [отзывы: <путь>].",
                    excerpt=text,
                )
            )
    return violations


# ============================================================================
# Правило 3: легенда + маркеры разделов карты доверия
# ============================================================================

TRUST_MARKER_RE = re.compile("[\U0001F7E2\U0001F7E1\U0001F534]")  # 🟢 🟡 🔴


def find_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """(line_index_0based, level, heading_text) для строк-заголовков markdown."""
    out = []
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def check_section_markers(lines: list[str]) -> list[Violation]:
    """Каждый H2-раздел (до следующего заголовка уровня <=2) должен содержать 🟢/🟡/🔴."""
    headings = find_headings(lines)
    violations = []
    n = len(lines)
    for idx, (line_i, level, text) in enumerate(headings):
        if level != 2:
            continue
        end = n
        for line_j, level_j, _ in headings[idx + 1 :]:
            if level_j <= 2:
                end = line_j
                break
        block = "\n".join(lines[line_i:end])
        if not TRUST_MARKER_RE.search(block):
            violations.append(
                Violation(
                    rule=3,
                    line=line_i + 1,
                    message=f"Раздел «{text}» не содержит маркера карты доверия (🟢/🟡/🔴).",
                    excerpt=lines[line_i].strip(),
                )
            )
    return violations


def check_legend_present(lines: list[str]) -> list[Violation]:
    violations = []
    text = "\n".join(lines)
    legend_line = None
    for i, line in enumerate(lines):
        if "легенда" in line.lower():
            legend_line = i + 1
            break
    if legend_line is None:
        violations.append(
            Violation(
                rule=3,
                line=0,
                message="Легенда карты доверия не найдена (нет строки со словом «Легенда»).",
            )
        )
    missing_markers = [m for m in ("🟢", "🟡", "🔴") if m not in text]
    if missing_markers:
        violations.append(
            Violation(
                rule=3,
                line=legend_line or 0,
                message=f"В документе не хватает маркеров карты доверия: {', '.join(missing_markers)}.",
            )
        )
    return violations


# ============================================================================
# Правило 4: запрещённые обещания и формулировка точности метода
# ============================================================================

FORBIDDEN_PHRASES = ("% купят", "прогноз продаж", "brand lift")
ACCURACY_WORD_RE = re.compile(r"\bточн\w*", re.IGNORECASE)
# ACCURACY_CANONICAL_MARKERS определён выше, рядом с правилом 1 (используется в
# обоих правилах: как исключение из требования тега источника и как маркер
# "правильной" формулировки точности).


def check_forbidden_promises(blocks: list[tuple[int, str]]) -> list[Violation]:
    violations = []
    for start_line, text in blocks:
        low = text.lower()
        for phrase in FORBIDDEN_PHRASES:
            if phrase in low:
                violations.append(
                    Violation(
                        rule=4,
                        line=start_line,
                        message=f"Запрещённая формулировка: «{phrase}» (см. §4, п.4 и references/methodology.md).",
                        excerpt=text,
                    )
                )
        if ACCURACY_WORD_RE.search(text) and PERCENT_RE.search(text):
            if not any(marker in low for marker in ACCURACY_CANONICAL_MARKERS):
                violations.append(
                    Violation(
                        rule=4,
                        line=start_line,
                        message=(
                            "Формулировка точности метода должна быть целиком "
                            "«R=0,72 (~90% теоретического потолка)», не «N% точности»."
                        ),
                        excerpt=text,
                    )
                )
    return violations


# ============================================================================
# Оркестрация
# ============================================================================


def lint_text(text: str) -> list[Violation]:
    """Все 4 правила на ОДНОМ тексте (используется юнит-тестами и как простая точка входа)."""
    lines = mask_reference_blocks(text.splitlines())
    blocks = split_into_blocks(lines)
    violations: list[Violation] = []
    violations += check_percent_sourcing(blocks)
    violations += check_real_quote_sourcing(blocks)
    violations += check_legend_present(lines)
    violations += check_section_markers(lines)
    violations += check_forbidden_promises(blocks)
    return violations


def lint_file(path: Path) -> list[Violation]:
    return lint_text(path.read_text(encoding="utf-8"))


def lint_files(paths: list[Path]) -> list[tuple[str, Violation]]:
    """
    Многофайловая оркестрация для CLI (--report + опц. --extra, см. модульный docstring):
    правила 1/2/4 и посекционные маркеры (правило 3б) — ПО КАЖДОМУ файлу отдельно;
    присутствие легенды (правило 3а) — по ОБЪЕДИНЁННОМУ тексту всех файлов (если легенда
    есть хотя бы в одном из них — считается, что она "присутствует" для всего набора).
    """
    file_texts: list[tuple[Path, str]] = [(p, p.read_text(encoding="utf-8")) for p in paths]

    results: list[tuple[str, Violation]] = []
    for p, text in file_texts:
        lines = mask_reference_blocks(text.splitlines())
        blocks = split_into_blocks(lines)
        per_file = (
            check_percent_sourcing(blocks)
            + check_real_quote_sourcing(blocks)
            + check_section_markers(lines)
            + check_forbidden_promises(blocks)
        )
        for v in per_file:
            results.append((p.name, v))

    combined_lines: list[str] = []
    for _, text in file_texts:
        combined_lines.extend(mask_reference_blocks(text.splitlines()))
    label = paths[0].name if len(paths) == 1 else f"{paths[0].name} и др. ({len(paths)} файлов)"
    for v in check_legend_present(combined_lines):
        results.append((label, v))

    return results


def format_violation(filename: str, v: Violation) -> str:
    loc = f"{filename}:{v.line}" if v.line > 0 else filename
    excerpt_text = v.excerpt
    if len(excerpt_text) > 220:  # блок (правила 1/2/4) может быть длинным абзацем — обрезаем для читаемости
        excerpt_text = excerpt_text[:217] + "..."
    excerpt = f"\n        {excerpt_text!r}" if excerpt_text else ""
    return f"  [{loc}] Правило {v.rule}: {v.message}{excerpt}"


# ============================================================================
# CLI
# ============================================================================


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Линтер честности cjm_report.md режима segment_map (spec §4)."
    )
    p.add_argument("--report", default=None, help="Путь к cjm_report.md (типовая финальная проверка).")
    p.add_argument(
        "--extra",
        nargs="*",
        default=[],
        help="Доп. файлы (например, 02_cjm_<segment_id>.md) — можно использовать и БЕЗ --report "
        "для ранней проверки промежуточных стадий.",
    )
    return p


def main() -> None:
    args = build_arg_parser().parse_args()

    paths: list[Path] = []
    if args.report:
        paths.append(Path(args.report))
    paths.extend(Path(p) for p in args.extra)

    if not paths:
        print("ОШИБКА: укажите --report и/или --extra <файл ...>.", file=sys.stderr)
        sys.exit(1)

    missing = [p for p in paths if not p.exists()]
    if missing:
        for p in missing:
            print(f"ОШИБКА: файл не найден: {p}", file=sys.stderr)
        sys.exit(1)

    violations = lint_files(paths)

    if not violations:
        print(f"OK: {len(paths)} файл(ов) проверено, нарушений не найдено.")
        print("Линтер честности пройден — прогон можно считать завершённым (spec §1.2).")
        sys.exit(0)

    by_file: dict[str, list[Violation]] = {}
    for filename, v in violations:
        by_file.setdefault(filename, []).append(v)

    for filename, vs in by_file.items():
        vs_sorted = sorted(vs, key=lambda x: (x.line, x.rule))
        print(f"{filename}: {len(vs_sorted)} нарушени{'е' if len(vs_sorted) == 1 else 'я' if len(vs_sorted) < 5 else 'й'}")
        for v in vs_sorted:
            print(format_violation(filename, v))

    print(f"\nИТОГО: {len(violations)} нарушени(е/я/й) — прогон НЕ считается завершённым (spec §1.2).")
    sys.exit(1)


if __name__ == "__main__":
    main()

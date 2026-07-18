#!/usr/bin/env python3
"""
render_client.py — клиентский HTML-рендер отчёта (spec_synthetic-panel_v1.3.md §2.2, [B4]).

    python scripts/render_client.py --report runs/<study>_<ts>/report.md
    python scripts/render_client.py --report runs/cjm_<имя>_<ts>/cjm_report.md --out /tmp/x.html

Собирает из ПОЛНОГО report.md/cjm_report.md один самодостаточный HTML-файл
(инлайн CSS/SVG, системные шрифты, никаких внешних запросов) для клиента —
без цифр/данных, которых нет в исходном отчёте, и без сокращения дисклеймеров.

=== Жёсткие правила (§2.2) — реализованы как отказ с человекочитаемым сообщением ===

  (а) Рендер ОТКАЗЫВАЕТСЯ работать:
      - если отчёт не проходит `scripts/cjm_lint.py` (переиспользуем канонический
        линтер честности как есть — не дублируем и не ослабляем его правила здесь;
        см. build_html_or_refuse/run_lint_gate);
      - если manifest.json рядом с отчётом отмечен controls_failed (см.
        resolve_controls_failed — терпимо к нескольким вероятным путям схемы,
        т.к. на момент написания этого файла [B1] ещё не зафиксировал точное имя
        поля в run_study.py/generate.py, см. ниже "Известные точки связности").
  (б) Дисклеймеры («Границы этого отчёта» и любые смежные разделы, например
      «...карта сегментов / AI CJM» или объединённый вариант по §2.1.4) переносятся
      ЦЕЛИКОМ. Программная проверка — assert_disclaimers_not_shortened: каждый
      пункт-маркер (`- **Метка:** ...`, логический блок — переиспользуем
      cjm_lint.split_into_blocks, чтобы не изобретать повторно склейку word-wrap)
      обязан дословно (после нормализации пробелов/markdown-синтаксиса) найтись в
      отрендеренном тексте; иначе — отказ, а не тихая публикация урезанного текста.

Коды возврата CLI: 0 — успех; 1 — ошибка аргументов/файла; 2 — не прошёл cjm_lint;
3 — controls_failed; 4 — блок дисклеймеров не прошёл проверку целостности;
5 — в отчёте вообще не найден раздел «Границы этого отчёта» (переносить нечего).

=== Устойчивость к вариациям шаблона ===

Секции ищутся по ЗАГОЛОВКАМ (нормализованный текст, без номеров/эмодзи-маркеров),
не по номерам строк и не по фиксированному числу разделов — report_template.md и
cjm_report_template.md правит [B3] параллельно с этой работой (spec v1.3 §2.1:
секция «Главное» первой, «Что с этим делать» последней, «Паспорт методологии»,
склейка дисклеймеров в один раздел). Все пять специально распознаваемых разделов
(«Главное», «Что с этим делать», «Границы этого отчёта», «Легенда карты доверия»,
«Паспорт методологии») — ОПЦИОНАЛЬНЫ: если раздела нет (старый отчёт v1/v1.2,
как оба пилотных отчёта на момент написания), рендер просто не показывает
соответствующий блок, а не падает и не выдумывает контент. Любой раздел, который
не подошёл ни под одну из этих меток, всё равно рендерится (generic-веткой) в
исходном порядке — ничего не пропадает молча.

Таблицы-рейтинги (E[шкала]/CI/PMF/Отделимость) ищутся ГЛОБАЛЬНО по СИГНАТУРЕ
столбцов (наличие "Место" + один из "E[.../Отделим.../Раздел..."), а не по
конкретному разделу — это позволяет одинаково находить их и в разделе 1
report.md, и в разделе 6.2 «Результат SSR-теста» cjm_report.md. Названия столбцов
сопоставляются подстрокой/word-boundary-регэкспом (см. _find_col), а не точным
текстом — переживёт переименование "95% CI" → "ДИ" или лёгкую правку заголовка
столбца шаблона.

=== Известные точки связности (для F1-ревью и на случай рассинхрона) ===

  - manifest.json: `mode`/`controls_failed`/`self_reported`/`anchors_version`
    (spec §1.4-1.5, зона [B1] run_study.py/generate.py) на момент написания ЭТОГО
    файла ещё не встречаются ни в одном manifest.json дерева (см. resolve_mode/
    resolve_controls_failed/manifest_meta — читают ПОЛЕ, если оно есть, иначе
    безопасный дефолт exploratory/False и явная пометка "не размечено" в HTML).
    Если [B1] в итоге назовёт поля иначе — поправить только _dig(...)-пути в этих
    трёх функциях, остальной модуль не зависит от точной схемы.
  - cjm_lint.py (зона [B3]) на момент написания этого файла проверяет ПОЛНЫЙ
    report.md (claims_ranking/…) с 11 нарушениями (ложные срабатывания правила 3
    — карта доверия 🟢🟡🔴 никогда не была частью формата report.md, только
    cjm_report.md/comp_report.md; и правила 1/4 — report.py разворачивает
    DISCLAIMER_BLOCK_START/END без самих маркеров-строк, поэтому маскировка
    cjm_lint не срабатывает на готовом report.md, хотя срабатывает на
    cjm_report.md, где агент переносит маркеры дословно). Это НЕ баг этого файла:
    run_lint_gate() честно вызывает cjm_lint.lint_file() как есть и отказывает,
    если тот находит нарушения — так и должно быть по духу правила (а). Пока
    это не починено в report.py/cjm_lint.py, клиентский рендер обычного
    report.md (в т.ч. runs/biotinal_claims_20260717-1109/report.md) будет
    отказывать. Подробности — в итоговом summary сборки (issues).
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import cjm_lint  # переиспользуем канонический линтер честности и его split_into_blocks

try:
    from report import BLOCKS as _ASCII_BLOCKS  # тот же алфавит спарклайна, что и report.py::ascii_bar
except Exception:  # report.py — зона [B1], может временно не импортироваться при параллельной правке
    _ASCII_BLOCKS = "▁▂▃▄▅▆▇█"


# ============================================================================
# Коды возврата / исключение отказа
# ============================================================================

EXIT_OK = 0
EXIT_ARGS = 1
EXIT_LINT_FAILED = 2
EXIT_CONTROLS_FAILED = 3
EXIT_DISCLAIMER_INTEGRITY = 4
EXIT_NO_DISCLAIMERS = 5


class RenderRefused(Exception):
    """Рендер отказывается работать (жёсткие правила §2.2 (а)/(б)). exit_code — см. EXIT_*."""

    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


# ============================================================================
# manifest.json: режим/контроли/метаданные (spec §1.4-1.5)
# ============================================================================


def load_manifest(report_path: Path) -> Optional[dict]:
    manifest_path = report_path.parent / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _dig(d: dict, *paths: str):
    """Пробует несколько dotted-путей по вложенным словарям, возвращает первое найденное непустое значение."""
    for path in paths:
        cur = d
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur is not None and cur != "":
            return cur
    return None


@dataclass
class ModeInfo:
    mode: str  # "exploratory" | "validated"
    inferred: bool  # True — поле mode отсутствовало, подставлен безопасный дефолт


def resolve_mode(manifest: Optional[dict]) -> ModeInfo:
    if manifest is None:
        return ModeInfo(mode="exploratory", inferred=True)
    mode = _dig(manifest, "mode")
    if mode in ("exploratory", "validated"):
        return ModeInfo(mode=mode, inferred=False)
    return ModeInfo(mode="exploratory", inferred=True)


def resolve_controls_failed(manifest: Optional[dict]) -> bool:
    """
    Терпимо к нескольким вероятным путям схемы (см. докстринг модуля, "Известные
    точки связности") — на момент написания [B1] ещё не зафиксировал точное имя
    поля. Отсутствие поля ВЕЗДЕ означает False (контроли выключены/не
    отслеживались старым прогоном — не значит "провалены").
    """
    if manifest is None:
        return False
    candidates = [
        _dig(manifest, "controls_failed"),
        _dig(manifest, "controls.controls_failed"),
        _dig(manifest, "controls.failed"),
    ]
    stages = manifest.get("stages", {}) if isinstance(manifest, dict) else {}
    if isinstance(stages, dict):
        for stage_val in stages.values():
            if not isinstance(stage_val, dict):
                continue
            if "controls_failed" in stage_val:
                candidates.append(stage_val.get("controls_failed"))
            controls_sub = stage_val.get("controls")
            if isinstance(controls_sub, dict):
                candidates.append(controls_sub.get("controls_failed") or controls_sub.get("failed"))
    return any(bool(c) for c in candidates if c is not None)


def manifest_meta(manifest: Optional[dict]) -> list[tuple[str, str]]:
    """Человекочитаемые пары (метка, значение) для паспорта прогона — терпимо к схеме до/после spec §1.5."""
    if manifest is None:
        return []
    out: list[tuple[str, str]] = []
    model = _dig(manifest, "model", "stages.generate.model")
    self_reported = _dig(manifest, "self_reported", "stages.generate.self_reported")
    if model:
        suffix = " (самоидентификация модели, agent-режим)" if self_reported else ""
        out.append(("Модель", f"{model}{suffix}"))
    else:
        out.append(("Модель", "не зафиксирована (agent-режим, temperature_control=false)"))
    emb = _dig(manifest, "embedding_model", "stages.score.embedding_model", "config_snapshot.embedding.model")
    if emb:
        out.append(("Эмбеддинг-модель (SSR)", emb))
    anchors_version = _dig(manifest, "anchors_version")
    if anchors_version:
        out.append(("Версия якорей", str(anchors_version)))
    date = _dig(manifest, "created_at")
    if date:
        out.append(("Дата прогона", str(date)))
    return out


# ============================================================================
# Markdown: базовые примитивы
# ============================================================================


def strip_html_comments(text: str) -> str:
    """Убирает авторские HTML-комментарии (докстринги шаблонов, пометки «копировать дословно»,
    объяснения пропущенных разделов) — они не для клиента. Дисклеймеры извлекаются ниже по
    ТЕКСТУ ЗАГОЛОВКА, а не по этим маркерам, поэтому их можно смело стричь заранее."""
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$", re.MULTILINE)


@dataclass
class Heading:
    level: int
    text: str
    line_start: int
    content_start: int


def find_headings(text: str) -> list[Heading]:
    return [
        Heading(level=len(m.group(1)), text=m.group(2).strip(), line_start=m.start(), content_start=m.end())
        for m in HEADING_RE.finditer(text)
    ]


def _norm_heading(text: str) -> str:
    """Нормализация заголовка для сопоставления по ключевым словам: без нумерации, эмодзи-маркеров, ё/е."""
    t = text.strip()
    t = re.sub(r"^[\d.]+\s*", "", t)
    t = re.sub(r"[🟢🟡🔴]", "", t)
    t = t.lower().replace("ё", "е").strip()
    t = re.sub(r"\s+", " ", t)
    return t


def doc_title(text: str, headings: list[Heading], fallback: str) -> str:
    h1 = next((h for h in headings if h.level == 1), None)
    return h1.text.strip() if h1 else fallback


def preamble_text(text: str, headings: list[Heading]) -> str:
    h1 = next((h for h in headings if h.level == 1), None)
    if h1 is None:
        return ""
    later = [h for h in headings if h.level <= 2 and h.line_start > h1.line_start]
    end = later[0].line_start if later else len(text)
    return text[h1.content_start : end]


def top_level_sections(text: str, headings: list[Heading]) -> list[tuple[Heading, str]]:
    """(H2-заголовок, тело до следующего заголовка уровня <=2) — та же граница блока, что в cjm_lint.check_section_markers."""
    h_le2 = [h for h in headings if h.level <= 2]
    out = []
    for i, h in enumerate(h_le2):
        if h.level != 2:
            continue
        end = len(text)
        for nxt in h_le2[i + 1 :]:
            end = nxt.line_start
            break
        out.append((h, text[h.content_start : end]))
    return out


HEADER_BULLET_RE = re.compile(r"^-\s+\*\*(.+?):\*\*\s*(.*)$")


def parse_header_meta(preamble: str) -> list[tuple[str, str]]:
    """
    Шапка отчёта — список `- **Метка:** значение`, часто со значением, перенесённым
    на несколько физических строк (word-wrap, см. пилот cjm_hairloss_demo: строка
    "Устойчивость сегментации" переносится на 5 строк). Переиспользуем
    cjm_lint.split_into_blocks, чтобы не терять хвост значения после первого
    перевода строки — та же защита, что и в assert_disclaimers_not_shortened.
    """
    lines = preamble.splitlines()
    blocks = cjm_lint.split_into_blocks(lines)
    out = []
    for _, block_text in blocks:
        m = HEADER_BULLET_RE.match(block_text.strip())
        if m:
            out.append((m.group(1).strip(), m.group(2).strip()))
    return out


def parse_header_note(preamble: str) -> str:
    lines = [re.match(r"^>\s?(.*)$", line).group(1) for line in preamble.splitlines() if re.match(r"^>\s?(.*)$", line)]
    return " ".join(l.strip() for l in lines if l.strip())


# ---- инлайн-форматирование -------------------------------------------------

INLINE_CODE_RE = re.compile(r"`([^`]+)`")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)([^*]+?)(?<!\*)\*(?!\*)")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def inline_md_to_html(text: str) -> str:
    text = html.escape(text, quote=False)
    text = INLINE_CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", text)

    def _link(m: re.Match) -> str:
        label, href = m.group(1), m.group(2)
        # Самодостаточный HTML: относительная ссылка на файл прогона (manifest.json,
        # responses.jsonl) не резолвится, если файл откроют/перешлют отдельно — поэтому
        # показываем как текст с путём в <code>, а не как кликабельный <a href>.
        if label.strip() == href.strip():
            return f'<code class="ref-path">{href}</code>'
        return f'<span class="ref-link">{label} <code>{href}</code></span>'

    text = LINK_RE.sub(_link, text)
    text = BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
    text = ITALIC_RE.sub(lambda m: f"<em>{m.group(1)}</em>", text)
    return text


# ---- таблицы -----------------------------------------------------------------

TABLE_LINE_RE = re.compile(r"^[ \t]*\|.*\|[ \t]*$")
TABLE_SEP_RE = re.compile(r"^[ \t]*\|?[ \t:|-]+\|?[ \t]*$")


@dataclass
class ParsedTable:
    header: list[str]
    rows: list[list[str]]
    start: int  # индекс строки внутри переданного списка lines (для сплайса основного потока)
    end: int  # исключающий


def split_table_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [cell.strip() for cell in s.split("|")]


def find_tables_in_lines(lines: list[str]) -> list[ParsedTable]:
    tables: list[ParsedTable] = []
    i, n = 0, len(lines)
    while i < n:
        if TABLE_LINE_RE.match(lines[i]) and i + 1 < n and TABLE_SEP_RE.match(lines[i + 1]) and "-" in lines[i + 1]:
            start = i
            header = split_table_row(lines[i])
            j = i + 2
            rows = []
            while j < n and TABLE_LINE_RE.match(lines[j]):
                rows.append(split_table_row(lines[j]))
                j += 1
            tables.append(ParsedTable(header=header, rows=rows, start=start, end=j))
            i = j
            continue
        i += 1
    return tables


def _find_col(header: list[str], substrings: tuple[str, ...] = (), word_tokens: tuple[str, ...] = ()) -> Optional[int]:
    """
    Поиск столбца по ключевым словам — подстрокой (устойчиво к вариациям формулировки
    заголовка) либо по границе слова (для коротких токенов вроде "ci"/"ди", которые иначе
    ложно матчились бы ВНУТРИ других слов — например "ди" внутри "кандидат").
    """
    for i, cell in enumerate(header):
        lo = cell.strip().lower()
        if any(s in lo for s in substrings):
            return i
        if any(re.search(rf"\b{re.escape(w)}\b", lo) for w in word_tokens):
            return i
    return None


def is_ranking_table(header: list[str]) -> bool:
    has_rank = _find_col(header, substrings=("место",)) is not None
    has_metric = (
        _find_col(header, substrings=("e[",)) is not None
        # "отделим/разделим" — v1/v1.2 ("Отделимость от следующего"); "устойчив" —
        # v1.3 ("Устойчивость разрыва от следующего", report_template.md §1 п.7:
        # E/CI/PMF ушли в приложение, в основной таблице остался только ярлык).
        or _find_col(header, substrings=("отделим", "разделим", "устойчив")) is not None
    )
    return has_rank and has_metric


def is_pmf_table(header: list[str]) -> bool:
    return all(_find_col(header, substrings=(f"p({k})",)) is not None for k in range(1, 6))


def render_generic_table_html(table: ParsedTable) -> str:
    thead = "".join(f"<th>{inline_md_to_html(c)}</th>" for c in table.header)
    body_rows = []
    ncols = len(table.header)
    for row in table.rows:
        cells = "".join(f"<td>{inline_md_to_html(row[i] if i < len(row) else '')}</td>" for i in range(ncols))
        body_rows.append(f"<tr>{cells}</tr>")
    return (
        '<div class="table-wrap"><table class="generic-table"><thead><tr>'
        f"{thead}</tr></thead><tbody>{''.join(body_rows)}</tbody></table></div>"
    )


# ============================================================================
# PMF: точный поиск (по таблице P(1)..P(5)) + декодирование ASCII-спарклайна
# ============================================================================


def _leading_id(label: str) -> Optional[str]:
    """Ведущий id стимула/RTB-кандидата до двоеточия ("D: текст…" -> "D", "rtb4: «…»" -> "rtb4")."""
    m = re.match(r"^\s*([A-Za-zА-Яа-яЁё0-9]{1,12})\s*:", label)
    return m.group(1) if m else None


def _norm_key(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def build_pmf_lookup(all_tables: list[ParsedTable]) -> dict[tuple[str, str], list[float]]:
    """(сегмент_norm, id_стимула_lower) -> [P1..P5] — из ЛЮБОЙ таблицы формы 'Таблица PMF по стимулам и сегментам'."""
    lookup: dict[tuple[str, str], list[float]] = {}
    for t in all_tables:
        if not is_pmf_table(t.header):
            continue
        i_stim = _find_col(t.header, substrings=("стимул", "кандидат"))
        i_seg = _find_col(t.header, substrings=("сегмент",))
        i_p = [_find_col(t.header, substrings=(f"p({k})",)) for k in range(1, 6)]
        if i_stim is None or i_seg is None or any(p is None for p in i_p):
            continue
        for row in t.rows:
            if i_stim >= len(row) or i_seg >= len(row):
                continue
            try:
                pmf = [float(row[p].replace(",", ".")) for p in i_p]
            except (ValueError, IndexError):
                continue
            stim_id = _leading_id(row[i_stim])
            seg_name = _norm_key(row[i_seg])
            if stim_id and seg_name:
                lookup[(seg_name, stim_id.lower())] = pmf
    return lookup


def decode_ascii_bar(bar: str) -> Optional[list[float]]:
    """Обратное преобразование report.py::ascii_bar — уровни BLOCKS 0..7 -> относительные высоты 0..1."""
    levels = []
    for ch in bar:
        idx = _ASCII_BLOCKS.find(ch)
        if idx == -1:
            continue  # пробелы/иные символы внутри ячейки — пропускаем, не считаем как несовпадение
        levels.append(idx / (len(_ASCII_BLOCKS) - 1))
    if len(levels) != 5:
        return None
    return levels


def build_pmf_lookup_from_document(text: str) -> dict[tuple[str, str], list[float]]:
    """
    Как build_pmf_lookup(), плюс понимает PMF внутри таблиц-РЕЙТИНГОВ, у которых
    сегмент задаётся ближайшим ПРЕДШЕСТВУЮЩИМ заголовком "Сегмент: X" (не колонкой
    таблицы) — форма из report_template.md v1.3 §2.1 п.5/п.7: секция 1 лишилась
    своей колонки PMF (туда, где раньше стояли числа, теперь только ярлык
    разделимости), а полные E[шкала]/95% CI/PMF переехали в "## Приложение",
    "### Полная статистика по сегментам" — ОДНА таблица-рейтинг НА СЕГМЕНТ (как
    в секции 1), а не общая плоская таблица "стимул×сегмент", как раньше в v1/v1.2.
    Без этого моста карточки основной секции 1 остались бы совсем без бара
    (честно, но обеднённо) для любого report.md, собранного новым report.py.

    Точные P(1)..P(5) (из плоской таблицы, см. build_pmf_lookup) в приоритете над
    декодированным ASCII, если для одной и той же пары (сегмент, id) нашлись оба.
    """
    lines = text.splitlines()
    tables = find_tables_in_lines(lines)
    lookup = build_pmf_lookup(tables)

    table_at_start = {t.start: t for t in tables}
    current_ctx: Optional[str] = None
    i, n = 0, len(lines)
    while i < n:
        if i in table_at_start:
            t = table_at_start[i]
            if current_ctx is not None and not is_pmf_table(t.header):
                i_ascii = _find_col(t.header, substrings=("pmf",))
                i_stim = _find_col(t.header, substrings=("стимул", "кандидат"))
                if i_ascii is not None and i_stim is not None:
                    seg_key = _norm_key(current_ctx)
                    for row in t.rows:
                        if i_stim >= len(row) or i_ascii >= len(row):
                            continue
                        stim_id = _leading_id(row[i_stim])
                        if not stim_id:
                            continue
                        key = (seg_key, stim_id.lower())
                        if key in lookup:
                            continue  # точные P(1)..P(5) уже есть для этой пары — не затираем ASCII-декодом
                        decoded = decode_ascii_bar(row[i_ascii])
                        if decoded:
                            lookup[key] = decoded
            i = t.end
            continue
        heading_m = HEADING_LINE_RE.match(lines[i])
        if heading_m:
            seg_m = SEGMENT_HEADING_RE.match(heading_m.group(2).strip())
            if seg_m:
                current_ctx = seg_m.group(1).strip()
        i += 1
    return lookup


# ============================================================================
# Ранжирующие таблицы -> структурированные строки + светофор разделимости
# ============================================================================

SEPARABILITY_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("confident", re.compile(r"уверенн\w*\s+разрыв|разделимо\s*\(ci\s*не\s*пересека", re.IGNORECASE)),
    ("borderline", re.compile(r"на\s+грани", re.IGNORECASE)),
    ("noise", re.compile(r"в\s+пределах\s+шума|не\s+разделимо", re.IGNORECASE)),
)


_NONE_TIER_LEADING_DASH_RE = re.compile(r"^[—\-–]\s*(\(|$)")


def classify_separability(raw_text: str) -> tuple[str, str]:
    """
    (tier, отображаемый_текст). tier ∈ {confident, borderline, noise, unknown, none}.
    Текст НЕ придумывается — берётся дословно из отчёта; tier только раскрашивает.
    Понимает и старый двухуровневый словарь (разделимо/не разделимо, spec v1/v1.2),
    и новый трёхуровневый (уверенный разрыв/на грани/в пределах шума, spec v1.3 §1.3.3).

    Последняя строка ранжирования (нет "следующего" стимула для сравнения) в v1.3
    report.py — не голое "—", а "— (Топ-N воспроизведён в X из Y проверок…)" (см.
    ranking_section тест-фикстуру report.py): дефис + пояснение в скобках, не
    просто дефис. _NONE_TIER_LEADING_DASH_RE ловит обе формы (голый дефис и
    дефис+скобка), сохраняя пояснение в отображаемом тексте, а не заменяя на "—".
    """
    text = raw_text.strip()
    if not text:
        return "none", "—"
    if _NONE_TIER_LEADING_DASH_RE.match(text):
        return "none", text
    for tier, pattern in SEPARABILITY_PATTERNS:
        if pattern.search(text):
            return tier, text
    return "unknown", text


@dataclass
class RankRow:
    rank: str
    label: str
    e_value: Optional[str]
    pmf: Optional[list[float]]
    pmf_source: str  # "exact" | "ascii" | "none"
    separability_tier: str
    separability_text: str


def extract_rank_rows(table: ParsedTable, pmf_lookup: dict, segment_key: Optional[str]) -> list[RankRow]:
    h = table.header
    i_rank = _find_col(h, substrings=("место",))
    i_label = _find_col(h, substrings=("стимул", "кандидат"))
    i_e = _find_col(h, substrings=("e[",))
    i_pmf = _find_col(h, substrings=("pmf",))
    i_sep = _find_col(h, substrings=("отделим", "разделим", "устойчив"))

    out = []
    for row in table.rows:
        get = lambda idx: row[idx] if idx is not None and idx < len(row) else None  # noqa: E731
        label = get(i_label) or ""
        rank = get(i_rank) or ""
        e_value = get(i_e)
        sep_text = get(i_sep) or ""
        tier, sep_display = classify_separability(sep_text)

        pmf_vals: Optional[list[float]] = None
        pmf_source = "none"
        stim_id = _leading_id(label)
        if segment_key and stim_id:
            exact = pmf_lookup.get((segment_key, stim_id.lower()))
            if exact:
                mx = max(exact) or 1.0
                pmf_vals = [v / mx for v in exact]
                pmf_source = "exact"
        if pmf_vals is None:
            raw_bar = get(i_pmf)
            if raw_bar:
                decoded = decode_ascii_bar(raw_bar)
                if decoded:
                    pmf_vals = decoded
                    pmf_source = "ascii"

        out.append(
            RankRow(
                rank=rank,
                label=label,
                e_value=e_value,
                pmf=pmf_vals,
                pmf_source=pmf_source,
                separability_tier=tier,
                separability_text=sep_display,
            )
        )
    return out


# ============================================================================
# SVG: горизонтальные бары PMF (замена ASCII-спарклайна, spec §2.2)
# ============================================================================


def render_pmf_bars_svg(values: list[float], width: int = 168, row_h: int = 13, gap: int = 2) -> str:
    n = len(values)
    height = n * row_h + (n - 1) * gap
    label_w = 14
    bar_area = width - label_w - 2
    parts = []
    for idx, v in enumerate(values):
        y = idx * (row_h + gap)
        w = max(1.5, min(1.0, max(0.0, v)) * bar_area)
        scale_point = idx + 1
        ty = y + row_h * 0.72
        parts.append(
            f'<text x="0" y="{ty:.1f}" class="bar-label">{scale_point}</text>'
            f'<rect x="{label_w}" y="{y}" width="{bar_area}" height="{row_h - 3}" class="bar-track"/>'
            f'<rect x="{label_w}" y="{y}" width="{w:.1f}" height="{row_h - 3}" class="bar-fill"/>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="Распределение ответов по шкале от 1 до 5" class="pmf-svg">'
        f"{''.join(parts)}</svg>"
    )


TIER_DOT_CLASS = {
    "confident": "dot-confident",
    "borderline": "dot-borderline",
    "noise": "dot-noise",
    "unknown": "dot-unknown",
    "none": "dot-none",
}


def render_ranking_card(title: str, scale_line: Optional[str], rows: list[RankRow]) -> str:
    rows_html = []
    for r in rows:
        if r.pmf:
            bar_html = render_pmf_bars_svg(r.pmf)
        else:
            bar_html = '<span class="bar-missing">нет данных PMF в отчёте</span>'
        dot_class = TIER_DOT_CLASS.get(r.separability_tier, "dot-unknown")
        e_html = (
            f'<span class="e-hint" title="Среднее по шкале 1-5 — подробности (CI/PMF) см. приложение">E≈{html.escape(r.e_value)}</span>'
            if r.e_value
            else ""
        )
        rows_html.append(
            f"""<div class="rank-row">
  <div class="rank-num">{html.escape(r.rank)}</div>
  <div class="rank-label">{inline_md_to_html(r.label)}{e_html}</div>
  <div class="rank-bar">{bar_html}</div>
  <div class="rank-sep"><span class="dot {dot_class}" aria-hidden="true"></span>{html.escape(r.separability_text)}</div>
</div>"""
        )
    scale_html = f'<div class="card-scale">Шкала: {inline_md_to_html(scale_line)}</div>' if scale_line else ""
    return f"""<div class="segment-card">
  <div class="card-title">{inline_md_to_html(title)}</div>
  {scale_html}
  <div class="rank-rows">{''.join(rows_html)}</div>
</div>"""


# ============================================================================
# Общий рендер тела раздела (прогон/списки/цитаты/таблицы/подзаголовки)
# ============================================================================

LIST_ITEM_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
BLOCKQUOTE_LINE_RE = re.compile(r"^>\s?(.*)$")
HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.*)$")
HR_RE = re.compile(r"^\s*(-{3,}|\*{3,})\s*$")
SEGMENT_HEADING_RE = re.compile(r"^(?:Сегмент|RTB-набор)\s*:\s*(.+)$", re.IGNORECASE)
SCALE_LINE_RE = re.compile(r"^Шкала\s*:\s*(.+)$", re.IGNORECASE)


def render_body(
    text: str, *, pmf_lookup: dict, appendix_tables: list[str], cardify_rankings: bool = True
) -> tuple[str, int]:
    """
    Возвращает (html, число_найденных_карточек_рейтинга). Таблицы формы P(1)..P(5)
    убираются из основного потока и складываются в `appendix_tables` (общий список
    для всего документа, spec §1.3.3 — "E[шкала], CI и PMF уходят в раздел-приложение");
    на их месте остаётся короткая пометка. Таблицы-рейтинги заменяются карточками со
    светофором и SVG-баром. Остальные таблицы рендерятся как есть (не архивные цифры,
    а содержательные качественные таблицы — сегменты/данные/таргетинг и т.п.).

    `cardify_rankings=False` — используется для тела раздела "## Приложение" (v1.3):
    его собственная таблица "Полная статистика по сегментам" (E[шкала]/95% CI/
    PMF/P(A>B)/Ярлык) технически совпадает по сигнатуре с таблицей-рейтингом
    (есть "Место" + "E[") — но ПРЕВРАЩАТЬ её в упрощённую карточку внутри самого
    приложения было бы неправильно: карточка нарочно ПРЯЧЕТ как раз те сырые
    числа (CI, точный P(A>B)), ради которых читатель разворачивает приложение
    (шаблон прямо говорит: "для тех, кому нужны сырые цифры для собственного
    анализа"). При cardify_rankings=False ЛЮБАЯ таблица (включая ranking- и
    PMF-сигнатуру) рендерится как обычная HTML-таблица без карточек/бар-графиков
    и без повторного перемещения в appendix_tables (мы уже внутри приложения).
    """
    lines = text.splitlines()
    tables = find_tables_in_lines(lines)
    table_at_start = {t.start: t for t in tables}
    table_end_for_start = {t.start: t.end for t in tables}

    out: list[str] = []
    ranking_card_count = 0
    current_ctx: Optional[str] = None
    current_scale: Optional[str] = None

    buf_para: list[str] = []
    buf_list: list[tuple[str, str]] = []
    buf_quote: list[str] = []

    def flush_para() -> None:
        if buf_para:
            joined = " ".join(l.strip() for l in buf_para if l.strip())
            if joined:
                out.append(f"<p>{inline_md_to_html(joined)}</p>")
            buf_para.clear()

    def flush_list() -> None:
        if buf_list:
            ordered = bool(re.match(r"\d+[.)]", buf_list[0][0]))
            tag = "ol" if ordered else "ul"
            items = "".join(f"<li>{inline_md_to_html(c)}</li>" for _, c in buf_list)
            out.append(f"<{tag}>{items}</{tag}>")
            buf_list.clear()

    def flush_quote() -> None:
        if buf_quote:
            joined = " ".join(l.strip() for l in buf_quote if l.strip())
            if joined:
                out.append(f'<blockquote class="quote-card">{inline_md_to_html(joined)}</blockquote>')
            buf_quote.clear()

    def flush_all() -> None:
        flush_para()
        flush_list()
        flush_quote()

    i, n = 0, len(lines)
    while i < n:
        if i in table_at_start:
            flush_all()
            table = table_at_start[i]
            if cardify_rankings and is_ranking_table(table.header):
                seg_key = _norm_key(current_ctx) if current_ctx else None
                rows = extract_rank_rows(table, pmf_lookup, seg_key)
                out.append(render_ranking_card(current_ctx or "Рейтинг стимулов", current_scale, rows))
                ranking_card_count += 1
            elif cardify_rankings and is_pmf_table(table.header):
                appendix_tables.append(render_generic_table_html(table))
                out.append(
                    '<p class="appendix-note">Полная таблица распределений (P1…P5) вынесена в приложение '
                    '«Технические таблицы» в конце документа.</p>'
                )
            else:
                out.append(render_generic_table_html(table))
            i = table_end_for_start[table.start]
            continue

        line = lines[i]

        heading_m = HEADING_LINE_RE.match(line)
        if heading_m:
            flush_all()
            level = len(heading_m.group(1))
            htext = heading_m.group(2).strip()
            tag_level = min(level + 1, 6)
            out.append(f'<h{tag_level} class="sub-heading">{inline_md_to_html(htext)}</h{tag_level}>')
            seg_m = SEGMENT_HEADING_RE.match(htext)
            if seg_m:
                current_ctx = seg_m.group(1).strip()
                current_scale = None
            i += 1
            continue

        scale_m = SCALE_LINE_RE.match(line.strip())
        if scale_m:
            flush_all()
            current_scale = scale_m.group(1).strip()
            i += 1
            continue

        if not line.strip():
            flush_all()
            i += 1
            continue

        if HR_RE.match(line):
            flush_all()
            out.append("<hr/>")
            i += 1
            continue

        bq_m = BLOCKQUOTE_LINE_RE.match(line)
        if bq_m:
            flush_para()
            flush_list()
            buf_quote.append(bq_m.group(1))
            i += 1
            continue

        list_m = LIST_ITEM_RE.match(line)
        if list_m:
            flush_para()
            flush_quote()
            buf_list.append((list_m.group(2), list_m.group(3)))
            i += 1
            continue

        if buf_quote:
            buf_quote.append(line.strip())
        elif buf_list:
            marker, content = buf_list[-1]
            buf_list[-1] = (marker, content + " " + line.strip())
        else:
            buf_para.append(line)
        i += 1

    flush_all()
    return "\n".join(out), ranking_card_count


# ============================================================================
# Классификация H2-разделов
# ============================================================================


def classify_heading(text: str) -> str:
    t = _norm_heading(text)
    if "главное" in t:
        return "highlights"
    if "что с этим делать" in t or "что делать" in t:
        return "recommendations"
    if "границы этого отчета" in t:
        return "disclaimer"
    if "легенда карты доверия" in t:
        return "trust_legend"
    if "паспорт методологии" in t:
        return "method_passport"
    if "приложение" in t:
        # v1.3 report_template.md §2.1 п.3/п.5: технический паспорт + полная
        # статистика (E/CI/PMF) переехали в отдельный раздел "## Приложение" —
        # сворачиваем его в тот же collapsed-блок, что и авто-найденные PMF-таблицы
        # старых отчётов (см. build_html: kind == "appendix_section").
        return "appendix_section"
    return "generic"


# ============================================================================
# Проверка целостности дисклеймеров (жёсткое правило б)
# ============================================================================


_BLOCK_TAG_RE = re.compile(
    r"</?(p|li|ul|ol|h[1-6]|div|section|aside|header|footer|blockquote|tr|td|th|table|thead|tbody|details|summary)"
    r"(?:\s[^>]*)?>",
    re.IGNORECASE,
)
_ANY_TAG_RE = re.compile(r"<[^>]+>")


def _plain_text(html_str: str) -> str:
    """
    HTML -> текст для сверки целостности дисклеймера. Блочные теги (li/p/h*/...) —
    граница текста (заменяются пробелом); инлайновые (code/strong/em/span, SVG-бары)
    прозрачны и убираются БЕЗ пробела — иначе "(`vertical: pharma_rx`)" после снятия
    <code> превращалось бы в "( vertical: pharma_rx )" и переставало дословно совпадать
    с исходным текстом при проверке в assert_disclaimers_not_shortened.
    """
    text = _BLOCK_TAG_RE.sub(" ", html_str)
    text = _ANY_TAG_RE.sub("", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _disclaimer_bullet_texts(markdown_body: str) -> list[str]:
    """
    Пункты-маркеры дисклеймера (`- **Метка:** ...`) как логические блоки — переиспользуем
    cjm_lint.split_into_blocks, чтобы word-wrap строки внутри одного пункта не считались
    отдельными пунктами (та же защита, что и в линтере честности).
    """
    lines = markdown_body.splitlines()
    blocks = cjm_lint.split_into_blocks(lines)
    bullets = []
    for _, block_text in blocks:
        stripped = block_text.strip()
        m = re.match(r"^[-*+]\s+(.*)$", stripped)
        if not m:
            continue
        # Отбрасываем сам маркер списка ("- ") — в отрендеренном HTML это <li>,
        # маркер не текст; сравнивать нужно только содержимое пункта.
        content = m.group(1)
        # Снимаем ** (жирный) и ` (код) — оба превращаются в HTML-теги вокруг тех же
        # слов на рендере. Подчёркивание НЕ трогаем: инлайн-рендер не считает "_"
        # разметкой курсива (только "*"), и идентификаторы вида "temperature_control"/
        # "pharma_rx" внутри дисклеймеров иначе ложно рвутся на "temperaturecontrol".
        norm = re.sub(r"[*`]", "", content)
        norm = re.sub(r"\s+", " ", norm).strip()
        if norm:
            bullets.append(norm)
    return bullets


def assert_disclaimers_not_shortened(disclaimer_sections: list[tuple[str, str]], rendered_html_snippets: list[str]) -> None:
    """
    Правило (б): "дисклеймеры переносятся целиком, сокращение запрещено программно
    (проверка длины блока)". Реализовано как проверка КАЖДОГО пункта-маркера исходного
    markdown — нормализованный текст пункта обязан дословно найтись в отрендеренном
    plain-тексте; иначе рендер отказывает (а не публикует тихо урезанный текст).
    """
    rendered_plain = re.sub(r"\s+", " ", _plain_text(" ".join(rendered_html_snippets)))
    total_bullets = 0
    missing: list[tuple[str, str]] = []
    for heading_text, body in disclaimer_sections:
        for bullet in _disclaimer_bullet_texts(body):
            total_bullets += 1
            probe = bullet[:120]
            if probe and probe not in rendered_plain:
                missing.append((heading_text, bullet[:80]))

    if total_bullets == 0:
        raise RenderRefused(
            "Раздел «Границы этого отчёта» найден, но не содержит ни одного пункта-маркера "
            "(- **Метка:** ...) — проверка целостности дисклеймера не может подтвердить, что "
            "текст перенесён полностью без сокращения. Рендер отказывается работать (spec §2.2, правило б).",
            EXIT_DISCLAIMER_INTEGRITY,
        )
    if missing:
        sample = "; ".join(f"«{h}»: «{b}…»" for h, b in missing[:3])
        raise RenderRefused(
            f"Блок «Границы этого отчёта» похож на сокращённый программно — {len(missing)} из "
            f"{total_bullets} пунктов не найдены дословно в отрендеренном тексте (например: {sample}). "
            "Дисклеймеры нельзя сокращать (SKILL.md §3, spec §2.2 правило б). Рендер отказывается работать.",
            EXIT_DISCLAIMER_INTEGRITY,
        )


# ============================================================================
# Фиксированные текстовые блоки клиентского слоя (не из отчёта — часть UI рендера)
# ============================================================================

HOW_TO_READ_LINES = (
    "Читайте цифры и цвета ниже как сравнение вариантов между собой в этом прогоне, "
    "а не как процент реальных покупателей — сила метода в относительном порядке, "
    "а не в абсолютных числах.",
    "Светофор показывает надёжность разницы (устойчива ли она при повторной проверке), "
    "а не её размер: серый цвет не значит «плохой результат», только «разница пока в "
    "пределах шума метода».",
    "Полные ограничения метода — раздел «Границы этого отчёта» в конце документа; не "
    "читайте цифры выше в отрыве от него.",
)


def render_how_to_read_box() -> str:
    items = "".join(f"<li>{html.escape(line)}</li>" for line in HOW_TO_READ_LINES)
    return f'<aside class="how-to-read"><p class="how-to-read-title">Как читать этот отчёт</p><ol>{items}</ol></aside>'


def render_mode_badge(mode_info: ModeInfo) -> str:
    if mode_info.mode == "validated":
        cls, label = "badge-validated", "Валидированный режим"
    else:
        cls, label = "badge-exploratory", "Разведочный режим"
    note = ""
    if mode_info.inferred:
        note = (
            ' <span class="badge-note" title="Поле mode отсутствует в manifest.json этого прогона — '
            'показан безопасный дефолт exploratory">· не размечен в manifest</span>'
        )
    return f'<span class="mode-badge {cls}">{label}</span>{note}'


def render_header_meta_box(header_meta: list[tuple[str, str]], manifest_extra: list[tuple[str, str]], intro_note: str) -> str:
    rows_src = header_meta or manifest_extra
    if not rows_src:
        return ""
    rows = "".join(
        f'<div class="meta-row"><span class="meta-label">{html.escape(k)}</span>'
        f'<span class="meta-value">{inline_md_to_html(v)}</span></div>'
        for k, v in rows_src
    )
    note_html = f'<p class="meta-note">{html.escape(intro_note)}</p>' if intro_note else ""
    return f'<section class="method-passport"><p class="passport-title">О прогоне</p>{rows}{note_html}</section>'


# ============================================================================
# Сборка HTML-страницы
# ============================================================================

PAGE_CSS = """
:root {
  --bg: #fbfaf7;
  --text: #20242c;
  --muted: #5b6472;
  --accent: #1f4e79;
  --border: #dcdfe4;
  --card-bg: #ffffff;
  --confident: #1e7d34;
  --borderline: #b8860b;
  --noise: #78828e;
  --disclaimer-bg: #fdf8ec;
  --disclaimer-border: #e7d8a8;
  --highlight-bg: #eef4fb;
  --highlight-border: #c9dcef;
}
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 16px;
  line-height: 1.6;
}
.page {
  max-width: 880px;
  margin: 0 auto;
  padding: 32px 24px 64px;
}
h1, h2, h3, h4, .card-title, .passport-title, .how-to-read-title {
  font-family: Georgia, "Times New Roman", serif;
  color: var(--accent);
  font-weight: 700;
}
h1 { font-size: 1.7em; margin: 0 0 6px; }
h2 { font-size: 1.32em; margin: 2.2em 0 0.6em; border-bottom: 1px solid var(--border); padding-bottom: 0.3em; }
h3.sub-heading { font-size: 1.1em; margin: 1.6em 0 0.5em; }
h4.sub-heading, h5.sub-heading, h6.sub-heading { font-size: 1em; margin: 1.2em 0 0.4em; color: var(--text); }
p { margin: 0.7em 0; }
ul, ol { margin: 0.6em 0; padding-left: 1.4em; }
li { margin: 0.25em 0; }
code { background: #eef0f3; border-radius: 3px; padding: 0.1em 0.35em; font-size: 0.92em; }
.ref-link code { background: #e6eaf0; }
hr { border: none; border-top: 1px solid var(--border); margin: 1.6em 0; }

.report-header { margin-bottom: 6px; }
.report-subtitle { color: var(--muted); font-size: 0.95em; margin: 0 0 10px; }
.mode-badge {
  display: inline-block;
  font-size: 0.82em;
  font-weight: 600;
  padding: 3px 11px;
  border-radius: 999px;
  letter-spacing: 0.01em;
}
.badge-validated { background: #e4f3e8; color: var(--confident); border: 1px solid #bfe2c9; }
.badge-exploratory { background: #eaf0f8; color: var(--accent); border: 1px solid #c9dcef; }
.badge-note { color: var(--muted); font-size: 0.85em; }

.how-to-read {
  background: var(--highlight-bg);
  border: 1px solid var(--highlight-border);
  border-radius: 10px;
  padding: 14px 20px;
  margin: 18px 0 26px;
}
.how-to-read-title { margin: 0 0 6px; font-size: 1em; }
.how-to-read ol { margin: 0; padding-left: 1.3em; }
.how-to-read li { font-size: 0.95em; color: var(--text); margin: 0.35em 0; }

.method-passport {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 20px;
  margin: 0 0 26px;
}
.passport-title { margin: 0 0 8px; font-size: 1em; }
.meta-row { display: flex; gap: 10px; font-size: 0.93em; padding: 2px 0; }
.meta-label { color: var(--muted); min-width: 190px; flex-shrink: 0; }
.meta-value { color: var(--text); }
.meta-note { color: var(--muted); font-size: 0.88em; margin-top: 8px; font-style: italic; }

.highlights-box {
  background: var(--highlight-bg);
  border: 1px solid var(--highlight-border);
  border-radius: 10px;
  padding: 6px 22px 14px;
  margin: 0 0 28px;
}
.highlights-box h2 { border-bottom: none; margin-top: 0.6em; }

.recommendations-box {
  background: #f2f7f0;
  border: 1px solid #d3e6cd;
  border-radius: 10px;
  padding: 6px 22px 14px;
  margin: 28px 0;
}
.recommendations-box h2 { border-bottom: none; margin-top: 0.6em; }

.segment-card {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 20px;
  margin: 16px 0;
}
.card-title { font-size: 1.08em; margin: 0 0 2px; }
.card-scale { color: var(--muted); font-size: 0.9em; margin-bottom: 10px; }
.rank-rows { display: flex; flex-direction: column; gap: 8px; }
.rank-row {
  display: grid;
  grid-template-columns: 26px 1fr auto 160px;
  gap: 12px;
  align-items: center;
  padding: 6px 0;
  border-top: 1px solid #eee;
}
.rank-row:first-child { border-top: none; }
.rank-num { font-weight: 700; color: var(--muted); text-align: center; }
.rank-label { font-size: 0.95em; }
.e-hint { display: block; color: var(--muted); font-size: 0.82em; margin-top: 2px; }
.rank-bar { white-space: nowrap; }
.bar-missing { color: var(--muted); font-size: 0.82em; font-style: italic; }
.rank-sep { display: flex; align-items: center; gap: 6px; font-size: 0.82em; color: var(--muted); white-space: normal; }
.dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; display: inline-block; }
.dot-confident { background: var(--confident); }
.dot-borderline { background: var(--borderline); }
.dot-noise { background: var(--noise); }
.dot-unknown { background: #b8b8b8; }
.dot-none { background: transparent; border: 1px solid #cfcfcf; }

.pmf-svg .bar-track { fill: #eef0f3; }
.pmf-svg .bar-fill { fill: var(--accent); }
.pmf-svg .bar-label { font-size: 8px; fill: var(--muted); }

blockquote.quote-card {
  background: #f7f6f2;
  border-left: 3px solid var(--accent);
  margin: 0.8em 0;
  padding: 0.5em 1em;
  font-style: italic;
  color: #33383f;
}

.table-wrap { overflow-x: auto; margin: 1em 0; }
table.generic-table { border-collapse: collapse; width: 100%; font-size: 0.92em; }
table.generic-table th, table.generic-table td {
  border: 1px solid var(--border);
  padding: 6px 10px;
  text-align: left;
  vertical-align: top;
}
table.generic-table th { background: #f2f3f5; font-weight: 700; }

.appendix-note { color: var(--muted); font-size: 0.85em; font-style: italic; }

details.appendix { margin: 28px 0; border: 1px solid var(--border); border-radius: 10px; padding: 4px 20px; background: var(--card-bg); }
details.appendix > summary { cursor: pointer; font-weight: 700; color: var(--accent); padding: 10px 0; font-family: Georgia, serif; }

.disclaimer-block {
  background: var(--disclaimer-bg);
  border: 1px solid var(--disclaimer-border);
  border-radius: 10px;
  padding: 6px 24px 18px;
  margin: 32px 0 0;
}
.disclaimer-block h2, .disclaimer-block h3.sub-heading { border-bottom: none; }

.page-footer { margin-top: 40px; color: var(--muted); font-size: 0.82em; text-align: center; }

@media (max-width: 640px) {
  .rank-row { grid-template-columns: 22px 1fr; grid-template-rows: auto auto; }
  .rank-bar, .rank-sep { grid-column: 1 / -1; }
  .meta-row { flex-direction: column; gap: 0; }
  .meta-label { min-width: 0; }
}

@media print {
  body { background: #fff; }
  .page { max-width: 100%; padding: 0 8px; }
  details.appendix { border: 1px solid #999; }
  a { color: inherit; text-decoration: none; }
}
"""


def build_html(report_path: Path, manifest: Optional[dict]) -> str:
    raw = report_path.read_text(encoding="utf-8")
    text = strip_html_comments(raw)
    headings = find_headings(text)

    title = doc_title(text, headings, fallback=report_path.stem)
    preamble = preamble_text(text, headings)
    header_meta = parse_header_meta(preamble)
    intro_note = parse_header_note(preamble)

    mode_info = resolve_mode(manifest)
    manifest_extra = manifest_meta(manifest)

    pmf_lookup = build_pmf_lookup_from_document(text)

    appendix_tables: list[str] = []
    highlights_html: Optional[str] = None
    recommendations_html: Optional[str] = None
    legend_html: Optional[str] = None
    passport_html: Optional[str] = None
    disclaimer_sections: list[tuple[str, str]] = []
    generic_sections: list[tuple[Heading, str]] = []
    total_ranking_cards = 0

    for heading, body in top_level_sections(text, headings):
        kind = classify_heading(heading.text)
        if kind == "disclaimer":
            disclaimer_sections.append((heading.text, body))
            continue
        if kind == "appendix_section":
            # v1.3: "## Приложение" (технический паспорт + полная статистика E/CI/PMF,
            # report_template.md §2.1 п.3/п.5) — тот же collapsed-блок, что и авто-
            # найденные PMF-таблицы старых отчётов (см. render_body's appendix_tables).
            # cardify_rankings=False: таблицы ВНУТРИ приложения не превращаются в
            # карточки — здесь читатель ищет именно сырые числа (CI/P(A>B)), карточка
            # их прячет (см. докстринг render_body).
            rendered, _ = render_body(
                body, pmf_lookup=pmf_lookup, appendix_tables=appendix_tables, cardify_rankings=False
            )
            appendix_tables.append(f"<h3>{inline_md_to_html(heading.text)}</h3>{rendered}")
            continue
        rendered, n_cards = render_body(body, pmf_lookup=pmf_lookup, appendix_tables=appendix_tables)
        total_ranking_cards += n_cards
        if kind == "highlights":
            highlights_html = rendered
        elif kind == "recommendations":
            recommendations_html = rendered
        elif kind == "trust_legend":
            legend_html = rendered
        elif kind == "method_passport":
            passport_html = rendered
        else:
            generic_sections.append((heading, rendered))

    if not disclaimer_sections:
        raise RenderRefused(
            "В отчёте не найден раздел «Границы этого отчёта» (ни в каком варианте заголовка) — "
            "рендер не может выполнить перенос дисклеймеров без сокращения (spec §2.2 правило б), "
            "потому что переносить нечего. Проверьте источник отчёта.",
            EXIT_NO_DISCLAIMERS,
        )

    disclaimer_html_snippets = []
    disclaimer_body_html_parts = []
    for heading_text, body in disclaimer_sections:
        rendered, _ = render_body(body, pmf_lookup=pmf_lookup, appendix_tables=appendix_tables)
        disclaimer_html_snippets.append(rendered)
        disclaimer_body_html_parts.append(f'<h3 class="sub-heading">{inline_md_to_html(heading_text)}</h3>{rendered}')

    assert_disclaimers_not_shortened(disclaimer_sections, disclaimer_html_snippets)

    # ---- сборка страницы ----------------------------------------------------
    parts: list[str] = []
    parts.append('<div class="page">')
    parts.append('<header class="report-header">')
    parts.append(f"<h1>{inline_md_to_html(title)}</h1>")
    parts.append(f'<p class="report-subtitle">{render_mode_badge(mode_info)}</p>')
    parts.append("</header>")

    parts.append(render_how_to_read_box())

    if passport_html:
        parts.append(f'<section class="method-passport"><p class="passport-title">Паспорт методологии</p>{passport_html}</section>')
        # render_header_meta_box (ветка else) уже включает intro_note сама — здесь
        # (реальная секция "Паспорт методологии" уже нашлась) её больше неоткуда
        # взять, кроме как показать отдельно: иначе вводная фраза из преамбулы
        # отчёта («Это отчёт синтетической ИИ-панели…») молча пропадает.
        if intro_note:
            parts.append(f'<p class="meta-note">{html.escape(intro_note)}</p>')
    else:
        parts.append(render_header_meta_box(header_meta, manifest_extra, intro_note))

    if highlights_html:
        parts.append(f'<section class="highlights-box"><h2>Главное</h2>{highlights_html}</section>')

    if legend_html:
        parts.append(f'<section class="trust-legend"><h2>Легенда карты доверия</h2>{legend_html}</section>')

    for heading, rendered in generic_sections:
        parts.append(f"<h2>{inline_md_to_html(heading.text)}</h2>{rendered}")

    if recommendations_html:
        parts.append(f'<section class="recommendations-box"><h2>Что с этим делать</h2>{recommendations_html}</section>')

    if appendix_tables:
        joined_appendix = "".join(f"<div>{t}</div>" for t in appendix_tables)
        parts.append(
            f'<details class="appendix"><summary>Приложение: технические таблицы (E[шкала], CI, PMF)</summary>{joined_appendix}</details>'
        )

    parts.append(f'<section class="disclaimer-block"><h2>Границы этого отчёта</h2>{"".join(disclaimer_body_html_parts)}</section>')

    parts.append(
        '<footer class="page-footer">Синтетическая ИИ-панель — вывод модельного приближения, не наблюдение '
        "за реальными людьми. Клиентский HTML сгенерирован автоматически из report.md/cjm_report.md "
        "(scripts/render_client.py) без изменения посчитанных цифр.</footer>"
    )
    parts.append("</div>")

    body_html = "\n".join(parts)
    safe_title = html.escape(title)
    n_cards_note = f"<!-- ranking_cards_rendered: {total_ranking_cards} -->"
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{safe_title}</title>
<style>{PAGE_CSS}</style>
</head>
<body>
{n_cards_note}
{body_html}
</body>
</html>
"""


# ============================================================================
# Жёсткие правила (а): линтер честности + controls_failed
# ============================================================================


def run_lint_gate(report_path: Path) -> None:
    violations = cjm_lint.lint_file(report_path)
    if violations:
        sample = "; ".join(f"строка {v.line}, правило {v.rule}: {v.message}" for v in violations[:3])
        more = f" и ещё {len(violations) - 3}" if len(violations) > 3 else ""
        raise RenderRefused(
            f"Отчёт не проходит cjm_lint ({len(violations)} нарушени"
            f"{'е' if len(violations) == 1 else 'я' if len(violations) < 5 else 'й'}) — рендер клиентского "
            f"HTML отказывается работать, пока честность отчёта не подтверждена "
            f"(`python scripts/cjm_lint.py --report {report_path}`). Примеры: {sample}{more}.",
            EXIT_LINT_FAILED,
        )


def build_html_or_refuse(report_path: Path) -> str:
    manifest = load_manifest(report_path)
    if resolve_controls_failed(manifest):
        raise RenderRefused(
            "Manifest этого прогона отмечен controls_failed — прогон не прошёл самоконтроль "
            "(плацебо и/или пара-ловушка, см. spec_synthetic-panel_v1.3.md §1.4). Рендер "
            "клиентского HTML для такого прогона отключён: выводы использовать нельзя, "
            "сначала перезапустите прогон.",
            EXIT_CONTROLS_FAILED,
        )
    run_lint_gate(report_path)
    return build_html(report_path, manifest)


# ============================================================================
# CLI
# ============================================================================


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Клиентский HTML-рендер отчёта синтетической панели (spec v1.3 §2.2).")
    p.add_argument("--report", required=True, type=Path, help="Путь к report.md или cjm_report.md.")
    p.add_argument("--out", type=Path, default=None, help="Путь для .html (по умолчанию — рядом с отчётом, тот же stem).")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    report_path: Path = args.report

    if not report_path.exists():
        print(f"ОШИБКА: файл не найден: {report_path}", file=sys.stderr)
        return EXIT_ARGS

    try:
        page_html = build_html_or_refuse(report_path)
    except RenderRefused as e:
        print(f"ОТКАЗ: {e}", file=sys.stderr)
        return e.exit_code

    out_path = args.out or report_path.with_suffix(".html")
    out_path.write_text(page_html, encoding="utf-8")
    print(f"OK: клиентский отчёт сохранён — {out_path}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

"""
report.py — количественная часть report.md (spec_synthetic-panel_v1.md §8).

Рендерит report.md из references/report_template.md ([B2]), подставляя цифры,
посчитанные score-стадией run_study.py (pmf_by_segment.csv + manifest.json).

Что этот модуль НЕ делает:
    - НЕ пишет секцию 4 «Качественный разбор» — маркер `<!-- QUALITATIVE -->`
      остаётся в тексте как есть; эту секцию дополняет отдельным шагом модель,
      ведущая скилл, по фактическому responses.jsonl (см. SKILL.md).
    - НЕ придумывает и не перефразирует текст блока «Границы этого отчёта» —
      копирует его ДОСЛОВНО из references/disclaimers.md (между маркерами
      DISCLAIMER_BLOCK_START/END), подставляя только {{...}}-плейсхолдеры.

Не требует embedding-модели/sentence-transformers — работает только с уже
посчитанными CSV/JSON, поэтому `--stage report` можно запускать отдельным
процессом уже после `--stage score` (см. run_study.py).

Точка входа для run_study.py — render_report()/write_report().
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Optional

BLOCKS = "▁▂▃▄▅▆▇█"


def ascii_bar(pmf: list[float]) -> str:
    """
    Компактный спарклайн PMF (5 точек шкалы), см. report_template.md, пример `▁▂▇▄▁`.

    Нормировка на максимум ЭТОЙ строки (не на глобальный максимум по всей таблице) —
    так форма распределения каждого стимула видна независимо от абсолютных чисел
    (абсолюты в этом методе всё равно не надёжны, см. disclaimers.md).
    """
    pmf = list(pmf)
    max_p = max(pmf) if pmf else 0.0
    if max_p <= 0:
        return BLOCKS[0] * len(pmf)
    chars = []
    for p in pmf:
        level = int(round((p / max_p) * (len(BLOCKS) - 1)))
        level = max(0, min(len(BLOCKS) - 1, level))
        chars.append(BLOCKS[level])
    return "".join(chars)


def truncate_label(text: str, width: int = 70) -> str:
    """Схлопывает пробелы/переносы строк и обрезает длинный текст стимула для табличной ячейки."""
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    return text[: width - 1].rstrip() + "…"


def read_pmf_by_segment(path: Path) -> list[dict]:
    """Читает pmf_by_segment.csv (см. run_study.py::run_score_stage) в список словарей."""
    rows = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                {
                    "segment": row["segment"],
                    "stimulus_id": row["stimulus_id"],
                    "n_respondents": int(row["n_respondents"]),
                    "pmf": [float(row[f"P{i}"]) for i in range(1, 6)],
                    "e_value": float(row["E"]),
                    "ci_low": float(row["ci_low"]),
                    "ci_high": float(row["ci_high"]),
                }
            )
    return rows


def _line_anchored_marker_re(marker: str) -> re.Pattern:
    """Матчит `marker`, только если он занимает ЦЕЛУЮ строку (не считая краевых пробелов)."""
    return re.compile(r"^[ \t]*" + re.escape(marker) + r"[ \t]*$", re.MULTILINE)


def _extract_block(text: str, start_marker: str, end_marker: str, start_pos: int = 0) -> Optional[str]:
    """
    Возвращает текст МЕЖДУ start_marker и end_marker — но засчитывает только те их
    вхождения, что занимают строку целиком. Это отличает настоящий структурный
    маркер от упоминания той же строки в прозе документации (например,
    `` `<!-- DISCLAIMER_BLOCK_START -->` `` внутри предложения, объясняющего сам
    механизм, — такое упоминание НЕ занимает строку целиком и потому не считается).
    """
    start_m = _line_anchored_marker_re(start_marker).search(text, start_pos)
    if start_m is None:
        return None
    end_m = _line_anchored_marker_re(end_marker).search(text, start_m.end())
    if end_m is None:
        return None
    return text[start_m.end() : end_m.start()]


def _splice_block(text: str, start_marker: str, end_marker: str, replacement: str) -> str:
    """Заменяет ЦЕЛИКОМ span [начало строки start_marker .. конец строки end_marker] на replacement."""
    start_m = _line_anchored_marker_re(start_marker).search(text)
    if start_m is None:
        raise ValueError(f"report.py: маркер {start_marker!r} не найден как самостоятельная строка в шаблоне.")
    end_m = _line_anchored_marker_re(end_marker).search(text, start_m.end())
    if end_m is None:
        raise ValueError(f"report.py: маркер {end_marker!r} не найден после {start_marker!r} в шаблоне.")
    return text[: start_m.start()] + replacement + text[end_m.end() :]


def _strip_leading_authoring_comment(text: str) -> str:
    """
    Обрезает ведущий докстринг-комментарий report_template.md (инструкция для
    авторов шаблона — не для клиента, который получит report.md).

    Не используем здесь общий `<!--.*?-->` (как для мелких комментариев ниже по
    файлу): этот докстринг содержит ВНУТРИ себя примеры вида
    `` `<!-- ANCHOR_START -->` `` — с буквальным "--><" прямо в тексте примера.
    Нежадный `.*?` из общей чистки остановился бы на ПЕРВОМ таком внутреннем
    "-->", обрезав докстринг посередине и оставив хвост как видимый мусор в
    report.md. Вместо этого ищем последний "-->" перед первым настоящим
    заголовком файла (`# ...`) — докстринг гарантированно заканчивается там.
    """
    heading_m = re.search(r"^#\s", text, re.MULTILINE)
    if heading_m is None:
        return text
    last_close = text.rfind("-->", 0, heading_m.start())
    if last_close == -1:
        return text
    return text[last_close + len("-->") :]


def substitute_placeholders(text: str, mapping: dict[str, str]) -> str:
    for key, value in mapping.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


def render_ranking_section(
    rows: list[dict],
    study: dict,
    segments: dict[str, dict],
    scale_name_ru: str,
    scale_id: str,
) -> str:
    """
    Секция 1 report_template.md: один блок на каждый сегмент исследования, стимулы
    отсортированы по E[шкала] по убыванию. Отделимость считается ПОПАРНО между
    соседними по рейтингу стимулами (CI не пересекаются -> разделимо) — см.
    report_template.md, легенда секции 1.
    """
    stimulus_text_by_id = {s["id"]: s["text"] for s in study["stimuli"]}
    by_segment: dict[str, list[dict]] = {}
    for row in rows:
        by_segment.setdefault(row["segment"], []).append(row)

    blocks = []
    for segment_id in study["segments"]:
        seg_rows_sorted = sorted(by_segment.get(segment_id, []), key=lambda r: r["e_value"], reverse=True)
        segment_name = segments.get(segment_id, {}).get("name", segment_id)

        lines = [
            f"### Сегмент: {segment_name}",
            "",
            f"Шкала: {scale_name_ru} ({scale_id})",
            "",
            "| Место | Стимул | E[шкала] | 95% CI | PMF (1→5) | Отделимость от следующего |",
            "|---:|---|---:|---:|---|---|",
        ]
        for i, row in enumerate(seg_rows_sorted):
            label = f"{row['stimulus_id']}: {truncate_label(stimulus_text_by_id.get(row['stimulus_id'], ''))}"
            e_val = f"{row['e_value']:.2f}"
            ci = f"[{row['ci_low']:.2f}, {row['ci_high']:.2f}]"
            bar = ascii_bar(row["pmf"])
            if i + 1 < len(seg_rows_sorted):
                nxt = seg_rows_sorted[i + 1]
                separable = row["ci_low"] > nxt["ci_high"]
                flag = "разделимо (CI не пересекаются)" if separable else "не разделимо (CI пересекаются)"
            else:
                flag = "—"
            lines.append(f"| {i + 1} | {label} | {e_val} | {ci} | {bar} | {flag} |")
        blocks.append("\n".join(lines))

    return "\n\n---\n\n".join(blocks)


def render_pmf_table_section(rows: list[dict], study: dict, segments: dict[str, dict]) -> str:
    """Секция 2 report_template.md: полная таблица PMF, стимул×сегмент (сгруппировано по сегменту, порядок как в секции 1)."""
    stimulus_text_by_id = {s["id"]: s["text"] for s in study["stimuli"]}
    by_segment: dict[str, list[dict]] = {}
    for row in rows:
        by_segment.setdefault(row["segment"], []).append(row)

    lines = [
        "| Стимул | Сегмент | P(1) | P(2) | P(3) | P(4) | P(5) | E[шкала] |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for segment_id in study["segments"]:
        seg_rows_sorted = sorted(by_segment.get(segment_id, []), key=lambda r: r["e_value"], reverse=True)
        segment_name = segments.get(segment_id, {}).get("name", segment_id)
        for row in seg_rows_sorted:
            label = f"{row['stimulus_id']}: {truncate_label(stimulus_text_by_id.get(row['stimulus_id'], ''), width=50)}"
            p1, p2, p3, p4, p5 = (f"{p:.3f}" for p in row["pmf"])
            lines.append(f"| {label} | {segment_name} | {p1} | {p2} | {p3} | {p4} | {p5} | {row['e_value']:.2f} |")

    return "\n".join(lines)


def load_disclaimer_block(disclaimers_path: Path, mapping: dict[str, str]) -> str:
    """
    Читает references/disclaimers.md и возвращает блок между DISCLAIMER_BLOCK_START/END
    ДОСЛОВНО (с подставленными {{...}}-плейсхолдерами) — сам текст не перефразируется
    (правило SKILL.md, раздел «Жёсткие правила» + сам файл disclaimers.md).
    """
    text = disclaimers_path.read_text(encoding="utf-8")
    block = _extract_block(text, "<!-- DISCLAIMER_BLOCK_START -->", "<!-- DISCLAIMER_BLOCK_END -->")
    if block is None:
        raise ValueError(f"Не найден блок DISCLAIMER_BLOCK_START/END в {disclaimers_path}")
    return substitute_placeholders(block.strip("\n"), mapping)


def render_report(
    *,
    template_path: Path,
    disclaimers_path: Path,
    rows: list[dict],
    study: dict,
    segments: dict[str, dict],
    scale_name_ru: str,
    scale_id: str,
    header_mapping: dict[str, str],
) -> str:
    """
    Собирает финальный текст report.md из references/report_template.md.

    Подход (см. комментарий в самом report_template.md — "Jinja2 не требуется"):
    1. Маркер `<!-- QUALITATIVE -->` защищается сентинелом от последующей чистки
       комментариев — он должен остаться в выходном файле буквально как есть.
    2/3. Блоки между ANCHOR_START/END (RANKING_TABLE, PMF_TABLE) заменяются ЦЕЛИКОМ
       программно сгенерированным markdown (одно ЯВНОЕ значение в шаблоне было лишь
       примером формы — реальных блоков N, по числу сегментов/стимулов).
    4. Секция 5 «Границы этого отчёта» в самом report_template.md — только заголовок
       + служебный HTML-комментарий (сам текст живёт в disclaimers.md и уже содержит
       ТАКОЙ ЖЕ заголовок) — поэтому всё начиная с этого заголовка обрезается и
       заменяется дословным блоком из disclaimers.md (шаг 8).
    5. Остальные авторские HTML-комментарии (докстринг шаблона, пояснения к секциям
       для будущих читателей report_template.md) вычищаются — они не для клиента.
    """
    template = template_path.read_text(encoding="utf-8")
    text = _strip_leading_authoring_comment(template)

    qual_sentinel = "\x00QUALITATIVE_MARKER\x00"
    text = text.replace("<!-- QUALITATIVE -->", qual_sentinel)

    ranking_md = render_ranking_section(rows, study, segments, scale_name_ru, scale_id)
    text = _splice_block(text, "<!-- RANKING_TABLE_START -->", "<!-- RANKING_TABLE_END -->", ranking_md)

    pmf_md = render_pmf_table_section(rows, study, segments)
    text = _splice_block(text, "<!-- PMF_TABLE_START -->", "<!-- PMF_TABLE_END -->", pmf_md)

    idx = text.rfind("## Границы этого отчёта")
    if idx != -1:
        text = text[:idx]

    # Оставшиеся мелкие авторские комментарии (без вложенных "-->" внутри, в
    # отличие от ведущего докстринга выше) — чистим обычным нежадным regex.
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = text.replace(qual_sentinel, "<!-- QUALITATIVE -->")
    text = substitute_placeholders(text, header_mapping)

    disclaimer_block = load_disclaimer_block(disclaimers_path, header_mapping)
    text = text.rstrip("\n") + "\n\n" + disclaimer_block + "\n"

    # причёсываем следы вычищенных комментариев: висящие пробелы перед переводом
    # строки (комментарий стоял ПОСЛЕ значения на той же строке, напр. "{{STUDY_TYPE}} <!-- ... -->")
    # и появившиеся из-за этого лишние пустые строки
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).lstrip("\n")

    leftover = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", text)))
    if leftover:
        raise ValueError(f"report.py: незамещённые плейсхолдеры в report.md: {leftover}")

    return text


def write_report(run_dir: Path, **kwargs) -> Path:
    report_text = render_report(**kwargs)
    report_path = run_dir / "report.md"
    report_path.write_text(report_text, encoding="utf-8")
    return report_path

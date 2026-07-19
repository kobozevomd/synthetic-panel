"""
report.py — количественная часть report.md (spec_synthetic-panel_v1.md §8,
расширено spec_synthetic-panel_v1.3.md §1.3 "честная статистика" и §1.4
"негативные контроли").

Рендерит report.md из references/report_template.md ([B2]), подставляя цифры,
посчитанные score-стадией run_study.py (pmf_by_respondent.csv/pmf_by_segment.csv/
pmf_by_sample.csv + manifest.json).

Что этот модуль НЕ делает:
    - НЕ пишет секцию 4 «Качественный разбор» — маркер `<!-- QUALITATIVE -->`
      остаётся в тексте как есть; эту секцию дополняет отдельным шагом модель,
      ведущая скилл, по фактическому responses.jsonl (см. SKILL.md).
    - НЕ придумывает и не перефразирует текст блока «Границы этого отчёта» —
      копирует его ДОСЛОВНО из references/disclaimers.md (между маркерами
      DISCLAIMER_BLOCK_START/END), подставляя только {{...}}-плейсхолдеры.
    - НЕ трогает references/report_template.md/cjm_report_template.md/
      competitive_report_template.md (зона [B3], v1.3 §2.1 "Паспорт методологии" /
      "Главное" / склейка границ — отдельная будущая работа над структурой ВСЕГО
      документа). Секции 1.3/1.4 ниже целиком встраиваются в УЖЕ существующие
      splice-точки RANKING_TABLE_START/END шаблона (эту секцию report.py и раньше
      генерировал программно целиком) — новых маркеров в самом файле шаблона это
      не требует. Единственное следствие: статичный абзац шаблона ПЕРЕД
      RANKING_TABLE_START (описывающий старый метод "CI не пересекаются") устарел
      после этой правки — поэтому render_ranking_section начинает свой вывод с
      явной заметки, что метод обновлён (см. _METHOD_UPDATE_NOTE ниже), а не
      молчит поверх устаревшего текста шаблона.

Не требует embedding-модели/sentence-transformers — работает только с уже
посчитанными CSV/JSON, поэтому `--stage report` можно запускать отдельным
процессом уже после `--stage score` (см. run_study.py).

Контракт для B3/B4 (докстринг-схема, spec_synthetic-panel_v1.3.md §1.3/1.4/1.5):
    - separability_label(p_win) -> {"уверенный разрыв", "на грани", "в пределах
      шума"} по порогам P(A>B) >= 0.9 / [0.7, 0.9) / < 0.7 (см. константы
      SEPARABILITY_HIGH/SEPARABILITY_MID ниже) — тот же вокабуляр обязаны
      использовать render_client.py [B4] и любые будущие шаблоны [B3].
    - compute_controls_verdict(...)["controls_failed"]: bool — если True, отчёт
      обязан нести красную плашку (см. render_ranking_section) и линтер (cjm_lint.py,
      [B3]) обязан ловить её отсутствие.
    - manifest.json (пишет run_study.py, [B1]): верхнеуровневые поля `mode`
      ("exploratory"|"validated"), `model`, `embedding_model`, `anchors_version`,
      `controls` (см. run_study.py докстринг), `controls_verdict` — контракт для
      режим-бейджа отчёта (v1.3 §1.5) и для render_client.py.

Точка входа для run_study.py — render_report()/write_report().

v1.4 (spec_synthetic-panel_v1.4.md §1.1-1.3, §2.2) добавляет:
    - stimulus_display_text(stimulus) -> str: подпись стимула для таблиц — text,
      иначе label (обязателен для image-only стимулов, см. run_study.py::
      validate_and_resolve_stimuli), иначе id.
    - render_report(..., vision_verdict=...)/render_appendix_table_section(...,
      vision_verdict=...)/render_vision_check_detail(vision_verdict): построчная
      детализация пробы зрения (§1.2) ПО СТИМУЛАМ в "## Приложение" — None для
      текстовых study (report.md не меняется). Аггрегированный статус
      пройдена/не пройдена — ОТДЕЛЬНЫЕ плейсхолдеры {{VISION_CHECK_SECTION}}/
      {{VISION_CHECK_STATUS_LINE}}/{{VISION_CHECK_FAILED_BANNER}}/
      {{STIMULUS_KIND_LINE}}/{{STIMULUS_KIND}} — эти пять report_template.md
      ([B3]) заполняет ЦЕЛИКОМ run_study.py::run_report_stage через
      header_mapping (тот же механизм, что уже несут MODE_BADGE/
      CONTROLS_STATUS_LINE/CONTROLS_FAILED_BANNER — см. run_study.py, функции
      compute_stimulus_kind_line/compute_vision_check_section/
      compute_vision_check_status_line/compute_vision_check_failed_banner);
      report.py сам их не вычисляет, только принимает vision_verdict для
      построчной детализации выше. Схема vision_verdict — run_study.py::
      compute_vision_verdicts.
    - compute_controls_verdict(...)["placebo_kind"] / render_controls_verdict_detail:
      kind выбранного плацебо этого прогона ("neutral"|"irrelevant"|
      "empty_promise", references/placebo_bank_ru.yaml поле kind, [B2]) — в
      детализации самоконтроля "## Приложение"; None для прогонов до v1.4.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

import ssr_core

BLOCKS = "▁▂▃▄▅▆▇█"

# Пороги трёхуровневых ярлыков разделимости (spec_synthetic-panel_v1.3.md §1.3.3) —
# КОНТРАКТ для report.py/render_client.py [B4]/шаблонов [B3]: не менять без правки
# спецификации, эти же числа обязаны использоваться везде, где встречается P(A>B).
SEPARABILITY_HIGH = 0.9
SEPARABILITY_MID = 0.7

# "Верхняя треть"/"нижняя треть" для проверки плацебо (§1.4) и "топ-N" для
# устойчивости (§1.3.2) — контракт-константы, не магические числа по месту.
STABILITY_TOP_N_CAP = 3
PLACEBO_BOTTOM_FRACTION = 1.0 / 3.0
# РЕШЕНО [review v1.3, находка №1 CRITICAL]: порог «плацебо уверенно обыграл
# реальный стимул» — согласован с нижним порогом separability_label («на грани»,
# 0.7). P(placebo>real) выше него уже не объясняется шумом → провал самоконтроля.
PLACEBO_BEATS_REAL_THRESHOLD = 0.7
# Ранговое правило «нижней трети» осмысленно только при достаточном числе строк
# рейтинга (реальные + контроли); при меньшем n оно вырождается и заменяется
# чисто вероятностным правилом выше.
PLACEBO_RANK_RULE_MIN_N = 6

# Смещение seed для "повторного скоринга с другим bootstrap-seed" (§1.3.2) —
# произвольное большое простое число, только чтобы гарантированно получить ДРУГУЮ
# последовательность np.random.default_rng, не совпадающую с основным seed.
BOOTSTRAP_RESEED_OFFSET = 1_000_003


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


def read_pmf_by_respondent(path: Path) -> list[dict]:
    """Читает pmf_by_respondent.csv (см. run_study.py::run_score_stage) — гранулярность
    ОДНА строка на (segment, stimulus_id, respondent_idx), т.е. "PMF профиля" ДО
    усреднения по респондентам сегмента. Нужен для парного бутстрепа §1.3.1 (пары
    "тот же респондент, стимул A против B" собираются именно на этом уровне)."""
    rows = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                {
                    "segment": row["segment"],
                    "stimulus_id": row["stimulus_id"],
                    "respondent_idx": int(row["respondent_idx"]),
                    "n_samples": int(row["n_samples"]),
                    "pmf": [float(row[f"P{i}"]) for i in range(1, 6)],
                    "e_value": float(row["E"]),
                }
            )
    return rows


def read_pmf_by_sample(path: Path) -> list[dict]:
    """
    Читает pmf_by_sample.csv — НОВЫЙ артефакт v1.3 (см. run_study.py::run_score_stage),
    гранулярность ОДНА строка на (segment, stimulus_id, respondent_idx, sample_idx),
    т.е. 1:1 с исходными responses.jsonl ДО усреднения по сэмплам одного респондента.
    Нужен для §1.3.2 (сплит-халф по сэмплам) и §1.3.4 (внутрипрогонная нестабильность
    сэмплов, "печатается, не маскируется"). Прогоны ДО v1.3 этот файл не писали —
    вызывающий код (run_study.py) обязан сам проверить path.exists() и передать
    пустой список/None в статистику вместо вызова этого читателя (см.
    compute_sample_instability/check_split_half_stability ниже — оба принимают
    пустой sample_rows и просто возвращают None "метрика недоступна", не падают).
    """
    rows = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                {
                    "segment": row["segment"],
                    "stimulus_id": row["stimulus_id"],
                    "respondent_idx": int(row["respondent_idx"]),
                    "sample_idx": int(row["sample_idx"]),
                    "pmf": [float(row[f"P{i}"]) for i in range(1, 6)],
                    "e_value": float(row["E"]),
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


def stimulus_display_text(stimulus: dict) -> str:
    """
    §1.1 v1.4 (report_template.md, п.14): подпись стимула для отчётных таблиц —
    `text`, если непустой; иначе `label` (ОБЯЗАТЕЛЕН в study.yaml для image-only
    стимулов, см. run_study.py::validate_and_resolve_stimuli — у image-only
    стимула text-а может не быть вовсе); иначе id (защитный fallback на случай
    невалидного study.yaml, не должен срабатывать после валидации run_study.py).
    """
    text = (stimulus.get("text") or "").strip()
    if text:
        return text
    label = (stimulus.get("label") or "").strip()
    if label:
        return label
    return stimulus.get("id", "")


def separability_label(p_win: float) -> str:
    """
    Трёхуровневый ярлык разделимости по P(A>B) парного бутстрепа (§1.3.3):
        p_win >= SEPARABILITY_HIGH (0.9)  -> "уверенный разрыв"
        SEPARABILITY_MID <= p_win < 0.9   -> "на грани"
        p_win < SEPARABILITY_MID (0.7)    -> "в пределах шума"
    Контракт для render_client.py [B4] и будущих шаблонов [B3] — не менять
    формулировки без синхронной правки там же.
    """
    if p_win >= SEPARABILITY_HIGH:
        return "уверенный разрыв"
    if p_win >= SEPARABILITY_MID:
        return "на грани"
    return "в пределах шума"


def build_e_matrix(
    resp_rows: list[dict], segment_id: str, stimulus_ids: list[str]
) -> tuple[np.ndarray, list[int]]:
    """
    Собирает (n_respondents, n_stimuli) матрицу E[шкала] по pmf_by_respondent-строкам
    ОДНОГО сегмента, столбцы — в порядке stimulus_ids. Респонденты (respondent_idx)
    обязаны иметь запись для КАЖДОГО stimulus_id — это гарантируется build_tasks
    (generate.py: каждый респондент сегмента отвечает на ВСЕ стимулы) и является
    предпосылкой честного парного бутстрепа (§1.3.1: "ресэмпл профилей" — профиль =
    респондент = одна и та же строка матрицы для всех стимулов-столбцов сразу).
    Неполные данные — явная ошибка (ValueError с перечислением недостающих пар), а
    не молчаливое обнуление/пропуск, который исказил бы саму статистику.
    """
    seg_rows = [r for r in resp_rows if r["segment"] == segment_id]
    by_respondent: dict[int, dict[str, float]] = {}
    for r in seg_rows:
        by_respondent.setdefault(r["respondent_idx"], {})[r["stimulus_id"]] = r["e_value"]

    respondent_ids = sorted(by_respondent)
    if not respondent_ids:
        raise ValueError(f"build_e_matrix: нет данных pmf_by_respondent для сегмента {segment_id!r}")

    matrix = np.zeros((len(respondent_ids), len(stimulus_ids)), dtype=np.float64)
    missing: list[str] = []
    for i, rid in enumerate(respondent_ids):
        row = by_respondent[rid]
        for j, sid in enumerate(stimulus_ids):
            if sid not in row:
                missing.append(f"respondent_idx={rid}, stimulus_id={sid}")
                continue
            matrix[i, j] = row[sid]
    if missing:
        preview = missing[:5]
        more = f" (+{len(missing) - 5} ещё)" if len(missing) > 5 else ""
        raise ValueError(
            f"build_e_matrix: сегмент {segment_id!r} — неполные пары респондент×стимул "
            f"(парный бутстреп требует ВСЕ стимулы у КАЖДОГО респондента): {preview}{more}"
        )
    return matrix, respondent_ids


@dataclass
class PairwiseCheck:
    higher_id: str
    lower_id: str
    p_win: float
    label: str


def compute_segment_pairwise_stats(
    resp_rows: list[dict],
    segment_id: str,
    stimulus_ids_by_e_desc: list[str],
    bootstrap_iters: int,
    seed: int,
) -> dict:
    """
    Парный бутстреп (§1.3.1) для ОДНОГО сегмента: P(A>B) для каждой пары СОСЕДНИХ по
    рангу стимулов (как в v1/v1.2 — сравнение "со следующим по рейтингу"), плюс
    вероятность 1-го места для КАЖДОГО стимула сегмента. `stimulus_ids_by_e_desc` —
    id стимулов сегмента, УЖЕ отсортированные по точечной оценке E по убыванию
    (сортировку делает вызывающий код — render_ranking_section).

    Возвращает {"e_matrix", "respondent_ids", "pairwise": [PairwiseCheck, ...],
    "place_probabilities": {stimulus_id: float}}.
    """
    e_matrix, respondent_ids = build_e_matrix(resp_rows, segment_id, stimulus_ids_by_e_desc)
    boot_means = ssr_core.joint_paired_bootstrap_means(e_matrix, n_iters=bootstrap_iters, seed=seed)

    pairwise = []
    for i in range(len(stimulus_ids_by_e_desc) - 1):
        p_win = ssr_core.pairwise_win_probability(boot_means, i, i + 1)
        pairwise.append(
            PairwiseCheck(
                higher_id=stimulus_ids_by_e_desc[i],
                lower_id=stimulus_ids_by_e_desc[i + 1],
                p_win=p_win,
                label=separability_label(p_win),
            )
        )

    place_probs = ssr_core.place_probabilities(boot_means)
    place_by_id = {sid: float(place_probs[i]) for i, sid in enumerate(stimulus_ids_by_e_desc)}

    return {
        "e_matrix": e_matrix,
        "respondent_ids": respondent_ids,
        "pairwise": pairwise,
        "place_probabilities": place_by_id,
    }


# ============================================================================
# Устойчивость (§1.3.2/1.3.4) — сплит-half по сэмплам, seed-переустойчивость
# бутстрепа, межпрогонное согласие, внутрипрогонная нестабильность сэмплов.
# ============================================================================


def top_n_sets_agree(order_a: Sequence[str], order_b: Sequence[str], top_n: int) -> bool:
    """True, если top_n-МНОЖЕСТВА (без учёта порядка внутри) двух ранжирований
    одного и того же набора id совпадают. Оба обязаны быть перестановками ОДНОГО
    множества (та же предпосылка, что у ssr_core.kendall_tau) — иначе ValueError."""
    if set(order_a) != set(order_b):
        raise ValueError("top_n_sets_agree: разные множества id между ранжированиями")
    return set(order_a[:top_n]) == set(order_b[:top_n])


def _mean_e_per_respondent_stimulus(rows: list[dict]) -> dict[tuple[int, str], float]:
    """Группирует sample-level строки по (respondent_idx, stimulus_id) и усредняет
    E по попавшим в группу сэмплам."""
    groups: dict[tuple[int, str], list[float]] = {}
    for r in rows:
        groups.setdefault((r["respondent_idx"], r["stimulus_id"]), []).append(r["e_value"])
    return {k: float(np.mean(v)) for k, v in groups.items()}


def split_half_by_samples(
    sample_rows: list[dict], segment_id: str
) -> Optional[tuple[list[dict], list[dict]]]:
    """
    Делит РАЗНЫЕ sample_idx сегмента на две группы (чередование по отсортированному
    списку различных sample_idx — при samples_per_respondent=2 это ровно
    нечётные/чётные) для сплит-half по сэмплам (§1.3.2). None, если у сегмента
    меньше 2 РАЗНЫХ sample_idx (сплит структурно невозможен — graceful
    degradation, а не ошибка: samples_per_respondent=1 — легальная конфигурация).
    """
    seg_rows = [r for r in sample_rows if r["segment"] == segment_id]
    sample_ids = sorted({r["sample_idx"] for r in seg_rows})
    if len(sample_ids) < 2:
        return None
    half_a_ids = set(sample_ids[0::2])
    half_b_ids = set(sample_ids[1::2])
    half_a = [r for r in seg_rows if r["sample_idx"] in half_a_ids]
    half_b = [r for r in seg_rows if r["sample_idx"] in half_b_ids]
    return half_a, half_b


def _ranking_from_half(half_rows: list[dict], stimulus_ids: list[str]) -> Optional[list[str]]:
    """
    Ранжирование стимулов по СРЕДНЕМУ E (не бутстреп — тут нужен только порядок
    данных одной половины сплит-халфа, не доверительный интервал) для sample-level
    строк ОДНОЙ половины. None при неполных данных половины (редкий крайний
    случай — не должен возникать при штатном build_tasks, но не должен и падать).
    """
    e_by_pair = _mean_e_per_respondent_stimulus(half_rows)
    respondent_ids = sorted({rid for rid, _ in e_by_pair})
    if not respondent_ids:
        return None
    means = []
    for sid in stimulus_ids:
        values = [e_by_pair.get((rid, sid)) for rid in respondent_ids]
        if any(v is None for v in values):
            return None
        means.append(float(np.mean(values)))
    order = sorted(range(len(stimulus_ids)), key=lambda i: means[i], reverse=True)
    return [stimulus_ids[i] for i in order]


def check_split_half_stability(
    sample_rows: list[dict],
    segment_id: str,
    primary_top_n_set: set,
    stimulus_ids_by_e_desc: list[str],
    top_n: int,
) -> Optional[bool]:
    """
    §1.3.2, "сплит-half по сэмплам внутри прогона": делит сэмплы на 2 непересекающиеся
    группы, независимо считает ранжирование в КАЖДОЙ и сравнивает top_n-множество
    каждой половины с top_n-множеством ГЛАВНОГО (полного) расчёта. True — только
    если ОБЕ половины воспроизводят топ-N; None — сплит невозможен (см.
    split_half_by_samples).
    """
    halves = split_half_by_samples(sample_rows, segment_id)
    if halves is None:
        return None
    for half_rows in halves:
        ranking = _ranking_from_half(half_rows, stimulus_ids_by_e_desc)
        if ranking is None:
            return None
        if set(ranking[:top_n]) != primary_top_n_set:
            return False
    return True


def check_bootstrap_reseed_stability(
    e_matrix: np.ndarray,
    stimulus_ids_by_e_desc: list[str],
    bootstrap_iters: int,
    seed: int,
) -> bool:
    """
    §1.3.2, "повтор скоринга с другим bootstrap-seed". ВАЖНОЕ ОТЛИЧИЕ от
    top_n_sets_agree-проверок выше: точечная оценка (среднее E на столбец) НЕ
    зависит от bootstrap-seed вообще — argmax по сырым средним один и тот же при
    любом seed, поэтому сравнивать "top-N по средним" здесь было бы тавтологией
    (всегда True). Осмысленная проверка — держится ли ЯРЛЫК разделимости
    (separability_label) топ-пары (ранг 1 vs ранг 2) стабильным при ДРУГОМ seed:
    если n_iters недостаточно, P(A>B) вблизи порога 0.9/0.7 может "перепрыгнуть"
    границу от одной лишь Monte-Carlo случайности бутстрепа — это и ловится здесь.
    """
    if len(stimulus_ids_by_e_desc) < 2:
        return True
    boot_a = ssr_core.joint_paired_bootstrap_means(e_matrix, n_iters=bootstrap_iters, seed=seed)
    boot_b = ssr_core.joint_paired_bootstrap_means(
        e_matrix, n_iters=bootstrap_iters, seed=seed + BOOTSTRAP_RESEED_OFFSET
    )
    p_a = ssr_core.pairwise_win_probability(boot_a, 0, 1)
    p_b = ssr_core.pairwise_win_probability(boot_b, 0, 1)
    return separability_label(p_a) == separability_label(p_b)


def compute_sample_instability(
    sample_rows: list[dict], segment_id: str, stimulus_ids: list[str]
) -> Optional[dict]:
    """
    §1.3.4: "внутрипрогонная нестабильность сэмплов НЕ маскируется" — для каждого
    респондента сравнивает, какой стимул набрал МАКСИМУМ E отдельно в КАЖДОМ его
    сэмпле (без усреднения по сэмплам, в отличие от штатного pmf_by_respondent);
    респондент считается НЕСТАБИЛЬНЫМ, если его "локальный лидер" отличается между
    сэмплами. None — метрика неприменима (<2 разных sample_idx у сегмента), а НЕ
    "0 расхождений" (это была бы ложная уверенность там, где сравнивать нечего).
    """
    seg_rows = [r for r in sample_rows if r["segment"] == segment_id and r["stimulus_id"] in stimulus_ids]
    by_key: dict[tuple[int, int], dict[str, float]] = {}
    for r in seg_rows:
        by_key.setdefault((r["respondent_idx"], r["sample_idx"]), {})[r["stimulus_id"]] = r["e_value"]

    if len({sample_idx for (_, sample_idx) in by_key}) < 2:
        return None

    by_respondent: dict[int, list[str]] = {}
    for (rid, _sample_idx), e_by_stim in by_key.items():
        winner = max(e_by_stim, key=e_by_stim.get)
        by_respondent.setdefault(rid, []).append(winner)

    n_unstable = sum(1 for winners in by_respondent.values() if len(set(winners)) > 1)
    return {"n_unstable": n_unstable, "n_total": len(by_respondent)}


def compute_reliability_summary(
    *,
    e_matrix: np.ndarray,
    stimulus_ids_by_e_desc: list[str],
    sample_rows: list[dict],
    segment_id: str,
    bootstrap_iters: int,
    seed: int,
    sibling_rankings: Sequence[list[str]] = (),
) -> dict:
    """
    §1.3.2 — собирает ВСЕ применимые проверки устойчивости в единый счёт "X из Y" +
    готовую строку для отчёта. Проверки НЕ взаимозаменяемы по смыслу (см. докстринги
    check_split_half_stability/check_bootstrap_reseed_stability выше), но для
    клиентской сводной фразы "топ-N воспроизведён в X из Y проверок" (spec §1.3.3)
    пулятся в один общий счётчик — технический смысл каждой отдельной проверки
    остаётся в `checks` для приложения/аудита.
    """
    top_n = min(STABILITY_TOP_N_CAP, len(stimulus_ids_by_e_desc))
    primary_top_n_set = set(stimulus_ids_by_e_desc[:top_n])

    checks: list[tuple[str, Optional[bool]]] = []
    checks.append(
        (
            "сплит-half по сэмплам",
            check_split_half_stability(
                sample_rows, segment_id, primary_top_n_set, stimulus_ids_by_e_desc, top_n
            ),
        )
    )
    checks.append(
        (
            "устойчивость к seed бутстрепа",
            check_bootstrap_reseed_stability(e_matrix, stimulus_ids_by_e_desc, bootstrap_iters, seed),
        )
    )
    for k, sibling_order in enumerate(sibling_rankings, start=1):
        try:
            agree = top_n_sets_agree(stimulus_ids_by_e_desc, sibling_order, top_n)
        except ValueError:
            continue  # набор стимулов у прогонов разошёлся — сравнение бессмысленно, пропускаем
        checks.append((f"совпадение с прогоном #{k}", agree))

    applicable = [(name, outcome) for name, outcome in checks if outcome is not None]
    y = len(applicable)
    x = sum(1 for _, outcome in applicable if outcome)

    if y == 0:
        summary_text = (
            f"Топ-{top_n}: проверки устойчивости недоступны в этом прогоне (нужно "
            "samples_per_respondent >= 2 и/или ещё один завершённый прогон того же study)."
        )
    else:
        summary_text = f"Топ-{top_n} воспроизведён в {x} из {y} проверок устойчивости."

    return {"top_n": top_n, "checks": checks, "x": x, "y": y, "summary_text": summary_text}


# ============================================================================
# Негативные контроли — вердикт (spec_synthetic-panel_v1.3.md §1.4, дефект Д5)
# ============================================================================


def compute_controls_verdict(
    *,
    all_segment_rows: list[dict],
    all_resp_rows: list[dict],
    controls_manifest: dict,
    segments: list[str],
    bootstrap_iters: int,
    seed: int,
) -> dict:
    """
    Вердикт негативных контролей ПО ВСЕМ сегментам исследования (строгий гейт —
    ЛЮБОЙ проваленный сегмент проваливает весь прогон, самоконтроль должен быть
    надёжным, а не "в среднем ок"):
      - Плацебо обязан финишировать в НИЖНЕЙ ТРЕТИ рейтинга (ранг > n - ceil(n/3))
        КАЖДОГО сегмента, где считался, — иначе controls_failed.
      - Пара-ловушка обязана быть "в пределах шума" (separability_label) относительно
        оригинала — иначе controls_failed.
    `all_segment_rows`/`all_resp_rows` — УЖЕ разблокированные (реальные id, не
    слепые метки) строки pmf_by_segment/pmf_by_respondent, ВКЛЮЧАЯ строки
    плацебо/ловушки (см. run_study.py::unblind_rows). Возвращает {"applicable":
    False}, если controls_manifest не enabled (study.yaml: controls: off, либо
    прогон до v1.3 без секции controls в manifest.json — обратная совместимость).
    """
    if not controls_manifest or not controls_manifest.get("enabled"):
        return {"applicable": False}

    placebo_real_id = controls_manifest["placebo"]["real_id"]
    decoy_real_id = controls_manifest["decoy"]["real_id"]
    decoy_of = controls_manifest["decoy"]["decoy_of"]

    by_segment_seg_rows: dict[str, list[dict]] = {}
    for r in all_segment_rows:
        by_segment_seg_rows.setdefault(r["segment"], []).append(r)

    per_segment_detail = []
    placebo_passed_all = True
    decoy_passed_all = True

    for segment_id in segments:
        seg_rows_sorted = sorted(
            by_segment_seg_rows.get(segment_id, []), key=lambda r: r["e_value"], reverse=True
        )
        ids_by_rank = [r["stimulus_id"] for r in seg_rows_sorted]
        n_total = len(ids_by_rank)

        placebo_rank = ids_by_rank.index(placebo_real_id) + 1 if placebo_real_id in ids_by_rank else None
        bottom_third_size = max(1, math.ceil(n_total * PLACEBO_BOTTOM_FRACTION))
        rank_in_bottom_third = placebo_rank is not None and placebo_rank > (n_total - bottom_third_size)

        # РЕШЕНО [review v1.3, находка №1 CRITICAL]: ранговая «нижняя треть»
        # вырождается при малом числе стимулов (n_real=2: плацебо, обыгравший
        # реальный стимул, проходил по рангу). Основное правило — вероятностное:
        # плацебо не должен уверенно обыгрывать НИ ОДИН реальный стимул; ранговое
        # правило остаётся дополнительным гейтом только при n_total >= PLACEBO_RANK_RULE_MIN_N.
        real_ids = [sid for sid in ids_by_rank if sid not in (placebo_real_id, decoy_real_id)]
        placebo_beats: list[str] = []
        placebo_gray: list[str] = []
        for rid in real_ids:
            try:
                e_matrix_p, _ = build_e_matrix(all_resp_rows, segment_id, [rid, placebo_real_id])
                boot_p = ssr_core.joint_paired_bootstrap_means(e_matrix_p, n_iters=bootstrap_iters, seed=seed)
                p_placebo_gt_real = ssr_core.pairwise_win_probability(boot_p, 1, 0)
            except ValueError:
                continue
            if p_placebo_gt_real >= PLACEBO_BEATS_REAL_THRESHOLD:
                placebo_beats.append(rid)
            elif p_placebo_gt_real > 0.5:
                placebo_gray.append(rid)
        placebo_ok = (
            placebo_rank is not None
            and not placebo_beats
            and (n_total < PLACEBO_RANK_RULE_MIN_N or rank_in_bottom_third)
        )
        placebo_passed_all = placebo_passed_all and bool(placebo_ok)

        decoy_label = "н/д"
        p_decoy_gt_original: Optional[float] = None
        try:
            e_matrix, _ = build_e_matrix(all_resp_rows, segment_id, [decoy_of, decoy_real_id])
            boot = ssr_core.joint_paired_bootstrap_means(e_matrix, n_iters=bootstrap_iters, seed=seed)
            p_decoy_gt_original = ssr_core.pairwise_win_probability(boot, 1, 0)
            p_original_gt_decoy = ssr_core.pairwise_win_probability(boot, 0, 1)
            decoy_label = separability_label(max(p_decoy_gt_original, p_original_gt_decoy))
            decoy_ok = decoy_label == "в пределах шума"
        except ValueError as exc:
            decoy_ok = False
            decoy_label = f"не удалось вычислить ({exc})"
        decoy_passed_all = decoy_passed_all and bool(decoy_ok)

        per_segment_detail.append(
            {
                "segment": segment_id,
                "placebo_rank": placebo_rank,
                "placebo_n_total": n_total,
                "placebo_bottom_third_size": bottom_third_size,
                "placebo_rank_rule_applied": n_total >= PLACEBO_RANK_RULE_MIN_N,
                "placebo_beats": placebo_beats,
                "placebo_gray": placebo_gray,
                "placebo_ok": placebo_ok,
                "decoy_label": decoy_label,
                "decoy_ok": decoy_ok,
                "p_decoy_gt_original": p_decoy_gt_original,
            }
        )

    controls_failed = not (placebo_passed_all and decoy_passed_all)
    return {
        "applicable": True,
        "placebo_passed": placebo_passed_all,
        "decoy_passed": decoy_passed_all,
        "controls_failed": controls_failed,
        "per_segment": per_segment_detail,
        "decoy_of": decoy_of,
        # spec_synthetic-panel_v1.4.md §2.2 (находка №3 review_v1.3.md, контрастные
        # плацебо): kind выбранного плацебо этого прогона ("neutral"|"irrelevant"|
        # "empty_promise") — берётся из controls_manifest (run_study.py::
        # build_controls_manifest), .get с фолбэком None для прогонов ДО v1.4 (их
        # controls_manifest["placebo"] такого поля не несёт) - render_controls_verdict_detail
        # ниже показывает строку про kind, только если она не None.
        "placebo_kind": (controls_manifest.get("placebo") or {}).get("kind"),
    }


# ============================================================================
# Секция 1 report_template.md — рендер
# ============================================================================

def render_ranking_section(
    rows: list[dict],
    resp_rows: list[dict],
    sample_rows: list[dict],
    study: dict,
    segments: dict[str, dict],
    scale_name_ru: str,
    scale_id: str,
    bootstrap_iters: int,
    bootstrap_seed: int,
    sibling_rankings_by_segment: Optional[dict[str, list[list[str]]]] = None,
) -> str:
    """
    Секция "## 1. Рейтинг стимулов по сегментам" report_template.md (v1.3 §2.1
    п.7-8, RANKING_TABLE_START/END): клиентский слой — ОДИН блок на каждый сегмент,
    стимулы по убыванию E[шкала]; таблица показывает ТОЛЬКО трёхуровневый ярлык
    разделимости (§1.3, separability_label — не независимые CI, Д3) + фразу
    межпрогонной устойчивости; сырые E[шкала]/95% CI/PMF/P(A>B) — в "## Приложение"
    (render_appendix_table_section). Вердикт негативных контролей (§1.4) сюда НЕ
    встраивается — у него отдельные плейсхолдеры {{CONTROLS_STATUS_LINE}}/
    {{CONTROLS_FAILED_BANNER}} в "Паспорт методологии" (см. run_study.py,
    compute_controls_status_line/compute_controls_failed_banner).

    `rows`/`resp_rows`/`sample_rows` обязаны содержать ТОЛЬКО реальные стимулы
    study.yaml (без плацебо/ловушки — см. run_study.py::split_real_and_control_rows).
    """
    sibling_rankings_by_segment = sibling_rankings_by_segment or {}
    stimulus_text_by_id = {s["id"]: stimulus_display_text(s) for s in study["stimuli"]}
    by_segment: dict[str, list[dict]] = {}
    for row in rows:
        by_segment.setdefault(row["segment"], []).append(row)

    blocks = []
    for segment_id in study["segments"]:
        seg_rows_sorted = sorted(by_segment.get(segment_id, []), key=lambda r: r["e_value"], reverse=True)
        segment_name = segments.get(segment_id, {}).get("name", segment_id)
        stimulus_ids_by_e_desc = [r["stimulus_id"] for r in seg_rows_sorted]

        lines = [
            f"### Сегмент: {segment_name}",
            "",
            f"Шкала: {scale_name_ru} ({scale_id})",
            "",
            "| Место | Стимул | Устойчивость разрыва от следующего |",
            "|---:|---|---|",
        ]

        if len(stimulus_ids_by_e_desc) >= 2:
            stats = compute_segment_pairwise_stats(
                resp_rows, segment_id, stimulus_ids_by_e_desc, bootstrap_iters, bootstrap_seed
            )
            pairwise_by_higher = {p.higher_id: p for p in stats["pairwise"]}

            reliability = compute_reliability_summary(
                e_matrix=stats["e_matrix"],
                stimulus_ids_by_e_desc=stimulus_ids_by_e_desc,
                sample_rows=sample_rows,
                segment_id=segment_id,
                bootstrap_iters=bootstrap_iters,
                seed=bootstrap_seed,
                sibling_rankings=sibling_rankings_by_segment.get(segment_id, []),
            )
            rerun_phrase = reliability["summary_text"]

            for i, sid in enumerate(stimulus_ids_by_e_desc):
                label_txt = f"{sid}: {truncate_label(stimulus_text_by_id.get(sid, ''))}"
                label = pairwise_by_higher[sid].label if sid in pairwise_by_higher else "—"
                lines.append(f"| {i + 1} | {label_txt} | {label} ({rerun_phrase}) |")

            instability = compute_sample_instability(sample_rows, segment_id, stimulus_ids_by_e_desc)
            if instability is None:
                sample_line = "неприменимо (samples_per_respondent=1 у этого прогона — сравнивать нечего)."
            elif instability["n_unstable"] == 0:
                sample_line = "расхождений победителя между сэмплами одного профиля не обнаружено."
            else:
                sample_line = (
                    f"у {instability['n_unstable']} из {instability['n_total']} профилей сегмента "
                    "разные сэмплы одного стимула дали разных «условных победителей» при сравнении "
                    "с соседом по рангу — сегмент внутренне неоднороден в реакции на эти стимулы, "
                    "это не ошибка данных."
                )
            lines.append("")
            lines.append(f"Внутрипрогонная устойчивость сэмплов: {sample_line}")
        else:
            lines.append("| 1 | (меньше 2 стимулов с данными в этом сегменте) | — |")

        blocks.append("\n".join(lines))

    return "\n\n---\n\n".join(blocks)


def render_vision_check_detail(vision_verdict: dict) -> str:
    """
    §1.2 v1.4 — построчная детализация пробы зрения ПО СТИМУЛАМ (Definition of
    Done v1.4, п.2: "вердикты по стимулам") для "## Приложение" — дополняет
    аггрегированный {{VISION_CHECK_SECTION}} абзац "Паспорта методологии"
    (тот несёт только общий статус пройдена/не пройдена, см. run_study.py::
    compute_vision_check_section) построчной раскладкой по каждому изображению:
    само описание (без роли персоны) + вердикт по каждому стимулу, который на
    это изображение ссылается. `vision_verdict` — см. run_study.py::
    compute_vision_verdicts (schema там же); вызывается ТОЛЬКО если
    vision_verdict не None (study содержит хотя бы один визуальный стимул) —
    для текстовых study эта функция не вызывается вовсе (см.
    render_appendix_table_section), report.md текстовых study не меняется.
    """
    if vision_verdict.get("vision_failed"):
        lines = ["**Проба зрения — детализация по стимулам (провалена, §1.2):**"]
    else:
        lines = ["**Проба зрения — детализация по стимулам (§1.2, пройдена):**"]
    lines.append("")
    lines.append("| Изображение | Стимулы | Описание (модель БЕЗ роли персоны) | Вердикт |")
    lines.append("|---|---|---|---|")
    for image in vision_verdict.get("per_image", []):
        verdicts = image.get("per_stimulus_verdict") or {}
        if verdicts:
            verdict_txt = "; ".join(
                f"{sid}: {'OK' if v == 'ok' else 'ПРОВАЛ'}" for sid, v in verdicts.items()
            )
        else:
            verdict_txt = "н/д (key_element не задан ни у одного стимула)"
        image_name = Path(str(image.get("image_path", ""))).name
        lines.append(
            f"| {image_name} | {', '.join(image.get('stimulus_ids', []))} | "
            f"{truncate_label(image.get('description', ''), width=90)} | {verdict_txt} |"
        )
    return "\n".join(lines)


def render_appendix_table_section(
    rows: list[dict],
    resp_rows: list[dict],
    study: dict,
    segments: dict[str, dict],
    bootstrap_iters: int,
    bootstrap_seed: int,
    controls_verdict: Optional[dict] = None,
    vision_verdict: Optional[dict] = None,
) -> str:
    """
    "## Приложение" / APPENDIX_TABLE_START/END (report_template.md v1.3 §2.1 п.3/5):
    полная статистика по сегментам — E[шкала], 95% CI, PMF, P(A>B) от следующего по
    рангу СЫРЫМ числом + тот же трёхуровневый ярлык, что и в секции 1 (эта таблица
    РАСКРЫВАЕТ секцию 1, не заменяет её). После таблиц — при наличии применимого
    controls_verdict (§1.4) — построчная детализация плацебо/ловушки по сегментам
    (ранг плацебо, ярлык ловушки), для аудита сверх однострочного
    {{CONTROLS_STATUS_LINE}} из "Паспорт методологии". vision_verdict (§1.2 v1.4,
    None для текстовых study — см. render_vision_check_detail) — построчная
    детализация пробы зрения ПО СТИМУЛАМ, сверх аггрегированного
    {{VISION_CHECK_SECTION}} абзаца "Паспорта методологии".
    """
    stimulus_text_by_id = {s["id"]: stimulus_display_text(s) for s in study["stimuli"]}
    by_segment: dict[str, list[dict]] = {}
    for row in rows:
        by_segment.setdefault(row["segment"], []).append(row)

    blocks = []
    for segment_id in study["segments"]:
        seg_rows_sorted = sorted(by_segment.get(segment_id, []), key=lambda r: r["e_value"], reverse=True)
        segment_name = segments.get(segment_id, {}).get("name", segment_id)
        stimulus_ids_by_e_desc = [r["stimulus_id"] for r in seg_rows_sorted]

        lines = [
            f"#### Сегмент: {segment_name}",
            "",
            "| Место | Стимул | E[шкала] | 95% CI | PMF (1→5) | P(A>B) от следующего | Ярлык |",
            "|---:|---|---:|---:|---|---:|---|",
        ]

        pairwise_by_higher = {}
        if len(stimulus_ids_by_e_desc) >= 2:
            stats = compute_segment_pairwise_stats(
                resp_rows, segment_id, stimulus_ids_by_e_desc, bootstrap_iters, bootstrap_seed
            )
            pairwise_by_higher = {p.higher_id: p for p in stats["pairwise"]}

        for i, row in enumerate(seg_rows_sorted):
            label = f"{row['stimulus_id']}: {truncate_label(stimulus_text_by_id.get(row['stimulus_id'], ''), width=50)}"
            if row["stimulus_id"] in pairwise_by_higher:
                p = pairwise_by_higher[row["stimulus_id"]]
                p_txt, sep_label = f"{p.p_win:.2f}", p.label
            else:
                p_txt, sep_label = "—", "—"
            lines.append(
                f"| {i + 1} | {label} | {row['e_value']:.2f} | "
                f"[{row['ci_low']:.2f}, {row['ci_high']:.2f}] | {ascii_bar(row['pmf'])} | {p_txt} | {sep_label} |"
            )
        blocks.append("\n".join(lines))

    if vision_verdict:
        blocks.append(render_vision_check_detail(vision_verdict))

    if controls_verdict and controls_verdict.get("applicable"):
        blocks.append(render_controls_verdict_detail(controls_verdict))

    return "\n\n---\n\n".join(blocks)


def render_controls_verdict_detail(controls_verdict: dict) -> str:
    """
    §1.4 — построчная детализация негативных контролей по сегментам (для
    "## Приложение", см. render_appendix_table_section). Контракт-маркер для
    будущего линтера [B3] (cjm_lint.py): при controls_failed=true где-то в отчёте
    ОБЯЗАНА присутствовать строка "прогон не прошёл самоконтроль" — это гарантирует
    и {{CONTROLS_FAILED_BANNER}} (см. run_study.py::compute_controls_failed_banner),
    и первая строка этой детализации ниже; не сокращать/переформулировать маркер.
    """
    if controls_verdict.get("controls_failed"):
        lines = ["**Детализация самоконтроля (прогон не прошёл самоконтроль, §1.4):**"]
    else:
        lines = ["**Детализация самоконтроля (§1.4, пройден):**"]

    # spec_synthetic-panel_v1.4.md §2.2: kind выбранного плацебо этого прогона —
    # "neutral" (банк v1.3), "irrelevant"/"empty_promise" (контрастные, добавлены
    # v1.4). Строка не пишется вовсе для прогонов ДО v1.4 (placebo_kind is None —
    # controls_manifest тех прогонов не нёс поля kind).
    placebo_kind = controls_verdict.get("placebo_kind")
    if placebo_kind:
        lines.append(f"Плацебо этого прогона — kind=«{placebo_kind}» (§2.2 v1.4, контрастные плацебо).")

    for detail in controls_verdict.get("per_segment", []):
        placebo_verdict = "OK" if detail["placebo_ok"] else "ПРОВАЛ"
        decoy_verdict = "OK" if detail["decoy_ok"] else "ПРОВАЛ"
        rank_txt = (
            f"{detail['placebo_rank']}/{detail['placebo_n_total']}"
            if detail["placebo_rank"] is not None
            else "н/д"
        )
        beats = detail.get("placebo_beats") or []
        gray = detail.get("placebo_gray") or []
        beats_txt = f"; плацебо уверенно обыграл реальные стимулы: {', '.join(beats)} — провал" if beats else ""
        gray_txt = (
            f"; предупреждение: плацебо статистически неотличим от {', '.join(gray)} "
            f"(слабый стимул или слабый контроль)" if gray else ""
        )
        lines.append(
            f"- {detail['segment']}: плацебо — ранг {rank_txt} ({placebo_verdict}){beats_txt}{gray_txt}; "
            f"пара-ловушка (косметическая копия стимула «{controls_verdict.get('decoy_of', '?')}») "
            f"— {detail['decoy_label']} ({decoy_verdict})."
        )
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
    resp_rows: list[dict],
    sample_rows: list[dict],
    study: dict,
    segments: dict[str, dict],
    scale_name_ru: str,
    scale_id: str,
    header_mapping: dict[str, str],
    bootstrap_iters: int = 1000,
    bootstrap_seed: int = 42,
    controls_verdict: Optional[dict] = None,
    sibling_rankings_by_segment: Optional[dict[str, list[list[str]]]] = None,
    vision_verdict: Optional[dict] = None,
) -> str:
    """
    Собирает финальный текст report.md из references/report_template.md (v1.3,
    см. докстринг-контракт "ЧТО МЕНЯЕТСЯ ДЛЯ REPORT.PY" в самом файле шаблона).

    Подход (комментарий в шаблоне — "Jinja2 не требуется"):
    1. Одноточечные маркеры `<!-- QUALITATIVE -->`/`<!-- KEY_TAKEAWAYS -->`/
       `<!-- NEXT_STEPS -->` защищаются сентинелами от чистки комментариев — они
       должны остаться в report.md буквально как есть (заполняются ОТДЕЛЬНЫМ шагом
       моделью, ведущей скилл, не этой функцией — см. SKILL.md).
    2. RANKING_TABLE_START/END и APPENDIX_TABLE_START/END заменяются ЦЕЛИКОМ
       программно сгенерированным markdown (см. render_ranking_section — клиентский
       слой, только ярлыки; render_appendix_table_section — сырые E/CI/PMF/P(A>B) +
       детализация контролей §1.4).
    3. "## Границы этого отчёта" — ЗАГОЛОВОК (не весь хвост файла: после v1.3 за
       ним ещё следует "## Приложение", которое НЕЛЬЗЯ терять) заменяется
       дословным блоком из disclaimers.md — тот несёт такой же заголовок сам.
    4. Остальные авторские HTML-комментарии (докстринг шаблона, пояснения к
       секциям для будущих читателей report_template.md) вычищаются — не для
       клиента. header_mapping (см. run_study.py) заполняет ОСТАЛЬНЫЕ
       {{...}}-плейсхолдеры статичного текста шаблона (MODE_BADGE,
       CONTROLS_STATUS_LINE, SEGMENT_NAMES_LIST, N_PROFILES_TOTAL и т.п.).
    """
    template = template_path.read_text(encoding="utf-8")
    text = _strip_leading_authoring_comment(template)

    qual_sentinel = "\x00QUALITATIVE_MARKER\x00"
    takeaways_sentinel = "\x00KEY_TAKEAWAYS_MARKER\x00"
    next_steps_sentinel = "\x00NEXT_STEPS_MARKER\x00"
    disclaimer_start_sentinel = "\x00DISCLAIMER_BLOCK_START_MARKER\x00"
    disclaimer_end_sentinel = "\x00DISCLAIMER_BLOCK_END_MARKER\x00"
    text = text.replace("<!-- QUALITATIVE -->", qual_sentinel)
    text = text.replace("<!-- KEY_TAKEAWAYS -->", takeaways_sentinel)
    text = text.replace("<!-- NEXT_STEPS -->", next_steps_sentinel)

    controls_verdict = controls_verdict or {"applicable": False}

    ranking_md = render_ranking_section(
        rows,
        resp_rows,
        sample_rows,
        study,
        segments,
        scale_name_ru,
        scale_id,
        bootstrap_iters,
        bootstrap_seed,
        sibling_rankings_by_segment,
    )
    text = _splice_block(text, "<!-- RANKING_TABLE_START -->", "<!-- RANKING_TABLE_END -->", ranking_md)

    appendix_md = render_appendix_table_section(
        rows, resp_rows, study, segments, bootstrap_iters, bootstrap_seed, controls_verdict, vision_verdict
    )
    text = _splice_block(text, "<!-- APPENDIX_TABLE_START -->", "<!-- APPENDIX_TABLE_END -->", appendix_md)

    # "## Границы этого отчёта": ЗАМЕНА заголовка на дословный блок disclaimers.md
    # (тот несёт этот же заголовок как свою первую строку) — НЕ обрезка хвоста
    # файла: с v1.3 после этого заголовка ещё идёт "## Приложение" (см. выше),
    # которое обязано остаться на месте, в отличие от v1/v1.2, где этот заголовок
    # был последним в файле и обрезка "всё после" была эквивалентна замене.
    #
    # ИНТЕГРАЦИОННЫЙ ФИКС (F2, v1.3 DoD): маркеры <!-- DISCLAIMER_BLOCK_START/END -->
    # оборачивают вставляемый блок сентинелами (тот же приём, что и qual_sentinel
    # выше), а НЕ литеральным текстом маркера — иначе следующий за этим блоком
    # общий regex-чистильщик HTML-комментариев (`<!--.*?-->`) тут же вырезал бы их
    # обратно (это ЦЕЛЫЙ корректный HTML-комментарий сам по себе). Без маркеров в
    # финальном report.md cjm_lint.py::mask_reference_blocks не находит, что
    # маскировать, и правила 1/4 ложно матчят "Brand Lift"/"прогноз продаж" внутри
    # дословно скопированной прозы disclaimers.md (см. render_client.py докстринг,
    # раздел "Известные точки связности" — там же зафиксирован сам дефект). У
    # cjm_report.md/comp_report.md этой проблемы никогда не было: агент, собирающий
    # их вручную, копирует маркеры дословно вместе с текстом блока.
    disclaimer_block = load_disclaimer_block(disclaimers_path, header_mapping)
    wrapped_disclaimer_block = (
        f"{disclaimer_start_sentinel}\n{disclaimer_block}\n{disclaimer_end_sentinel}"
    )
    if "## Границы этого отчёта" not in text:
        raise ValueError(
            "report.py: заголовок '## Границы этого отчёта' не найден в шаблоне — "
            "не могу вставить блок дисклеймеров из disclaimers.md."
        )
    text = text.replace("## Границы этого отчёта", wrapped_disclaimer_block, 1)

    # Оставшиеся мелкие авторские комментарии (без вложенных "-->" внутри, в
    # отличие от ведущего докстринга выше) — чистим обычным нежадным regex.
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = text.replace(qual_sentinel, "<!-- QUALITATIVE -->")
    text = text.replace(takeaways_sentinel, "<!-- KEY_TAKEAWAYS -->")
    text = text.replace(disclaimer_start_sentinel, "<!-- DISCLAIMER_BLOCK_START -->")
    text = text.replace(disclaimer_end_sentinel, "<!-- DISCLAIMER_BLOCK_END -->")
    text = text.replace(next_steps_sentinel, "<!-- NEXT_STEPS -->")
    text = substitute_placeholders(text, header_mapping)

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

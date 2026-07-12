#!/usr/bin/env python3
"""
targeting_export.py — валидация 05_targeting_<segment_id>.yaml + сборка сводной
markdown-таблицы и targeting_matrix.yaml (spec_synthetic-panel_v1.2.md §Модуль 2
п.2-3, задание [B2] п.2).

    python scripts/targeting_export.py --run runs/cjm_<имя>_<ts>/
    python scripts/targeting_export.py --files 05_targeting_a.yaml 05_targeting_b.yaml  # без --run

Схема входа (АВТОРИТЕТНЫЙ источник — references/cjm_prompts_ru.md, Стадия 4.5
«Проекция на таргетинги», раздел «Формат выхода» — схема там прямо помечена
как "ЗАФИКСИРОВАНА как контракт для scripts/targeting_export.py"; на момент
написания ЭТОГО файла cjm_prompts_ru.md УЖЕ содержал финализированную схему —
задание допускало вариант "B1 ещё не дописал -> взять схему из спецификации
§Модуль 2 п.2", но в данном случае сверка была возможна и схема ниже — 1:1 с
cjm_prompts_ru.md, а НЕ самостоятельная реконструкция из более общего текста
spec_synthetic-panel_v1.2.md):

    segment_id: ingredient_rutinshiki        # ОБЯЗАТЕЛЬНО, = id из panel/segments/**
    segment_name: "Те, кто читает составы"   # опционально (для читаемости таблицы)
    generated_at: "2026-07-12T10:00:00+00:00"  # опционально

    demographics: {values: [str, ...], trust: "🟢"/"🟡"/"🔴", source: str}
    income:       {values: [str, ...], trust: ..., source: str}
    content:      {values: [str, ...], trust: ..., source: str}
    purchases:    {values: [str, ...], trust: ..., source: str}

ВАЖНО: четыре оси — TOP-LEVEL ключи документа (НЕ вложены под `axes:`) — см.
cjm_prompts_ru.md, пример в разделе «Формат выхода» Стадии 4.5, буквально.

=== Правило для gender (cjm_prompts_ru.md, Стадия 4.5, «Правило для gender» —
    буквально то же самое, что и задание [B2] п.2 "поле gender отсутствует у
    сегмента -> в таблице «уточнить», не выдумывать") ===

Если у сегмента (`panel/segments/**/<segment_id>.yaml: persona_jitter.gender`)
ПОЛЯ ГЕНДЕРА НЕТ — модель, заполняющая 05_targeting_*.yaml, обязана написать
буквально «уточнить» где-то в `demographics.values` (например: «пол:
уточнить; возраст 20-38 лет») и выставить `demographics.trust: "🔴"`. Это
проверяется здесь МЕХАНИЧЕСКИ (check_gender_honesty) — а не восстанавливается
скриптом самостоятельно: скрипт СВЕРЯЕТ заявленное моделью с реальным полем
сегмента и ОТКАЗЫВАЕТ (ValueError -> exit 1), если сегмент без gender, а
demographics не содержит честного «уточнить»/`trust: 🔴` — то есть похоже на
то, что модель ВЫДУМАЛА пол вместо честного пробела. Это единственная
проверка, которой скрипт не просто валидирует форму, а сверяет содержание
с независимым источником истины (сам сегмент), — намеренно, т.к. именно это
и есть мотивирующий кейс всей стадии 4.5 (см. spec_synthetic-panel_v1.2.md
§Модуль 2 п.1: "для старых сегментов без пола матрица честно пишет
«уточнить» — не выдумывать").

=== Запрещённый контент осей (cjm_prompts_ru.md, Стадия 4.5, «Красная линия»
    + промпт: "ни одного числа аудитории, доли рынка, CPM, охвата или слова
    "миллион(ов)"/"тысяч человек"") ===

Таргетинг-матрица — ТОЛЬКО правила отбора («кому показывать»), никогда не
измерение/прогноз охвата. _validate_axis_cell проверяет values+source на:
  - простые запрещённые термины (охват, cpm, grp, миллион, "тысяч человек",
    "число пользователей") — FORBIDDEN_TARGETING_SUBSTRINGS;
  - «размер/объём/величина аудитории» — КОМБИНИРОВАННАЯ проверка (оба слова
    должны встретиться одновременно), а не голое слово «размер» — иначе
    ложно сработало бы на легитимное "предпочитают средний размер упаковки"
    в оси purchases (это про товар, не про аудиторию);
  - «доля/доли/долю/...» — регэксп по словоформам (SHARE_WORD_RE), НЕ голая
    подстрока «дол» (которая ложно поймала бы «долго»/«должен»/«доллар»/
    «долина»);
  - процент — переиспользует cjm_lint.PERCENT_RE/PERCENT_WORD_RE (в т.ч.
    словом, без знака «%», находка №4 review_v1.1.md) вместо дублирования
    той же регэксп-логики.
Нарушение любого из этих пунктов -> ValueError (человекочитаемый отказ, как и
везде в этом скилле), а не молчаливое исправление.

=== Локальная копия индекса сегментов (НЕ импортируем run_study.py) ===

build_segment_index() ниже — намеренная копия той же (рекурсивный обход
panel/segments/**/*.yaml по stem) логики run_study.py::build_segment_index,
а не импорт этого модуля: run_study.py тянет за собой generate.py/report.py/
ssr_core.py (numpy, потенциально sentence-transformers) — валидатору схемы
эти зависимости не нужны (см. аналогичное решение и обоснование в докстринге
cjm_lint.py: "линтер не требует .venv"). Расхождение семантики держать в
синхроне вручную, если run_study.py::build_segment_index когда-либо
изменится (маловероятно — это чистая функция обхода файловой системы).

=== Выход ===

    <run_dir>/05_targeting_table.md   — markdown-таблица «сегмент × 4 оси»
                                         СТРОГО в формате, ожидаемом
                                         references/cjm_report_template.md §7
                                         (заголовки "Демография 🟢 | Доход 🟢 |
                                         Контент 🟡 | Покупки 🟡", значения
                                         внутри ячейки — через "; ") — готова
                                         к вставке между TARGETING_TABLE_START/END.
    <run_dir>/targeting_matrix.yaml   — машиночитаемый (для баинга), полный
                                         values+trust+source по каждой оси
                                         каждого сегмента (таблица выше —
                                         человекочитаемая проекция ЭТОГО файла,
                                         без trust/source деталей).

Юнит-тесты: scripts/test_targeting_export.py (валидная схема; битая схема —
отсутствующий segment_id/неизвестный segment_id/отсутствующая ось/неверный
trust/запрещённый термин/процент; отсутствующий gender -> «уточнить» обязателен).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import cjm_lint  # noqa: E402 — переиспользуем PERCENT_RE/PERCENT_WORD_RE (не дублируем регэкспы)

REQUIRED_AXES = ("demographics", "income", "content", "purchases")
AXIS_COLUMN_HEADER = {
    "demographics": "Демография 🟢",
    "income": "Доход 🟢",
    "content": "Контент 🟡",
    "purchases": "Покупки 🟡",
}
VALID_TRUST_MARKERS = ("🟢", "🟡", "🔴")

# Простые подстроки — см. докстринг модуля, раздел "Запрещённый контент осей".
FORBIDDEN_TARGETING_SUBSTRINGS = ("охват", "cpm", "grp", "миллион", "тысяч человек", "число пользователей")
# «Размер/объём/величина аудитории» — комбинированная проверка (оба слова из
# разных множеств должны встретиться), см. докстринг за обоснованием.
AUDIENCE_SIZE_WORDS = ("размер", "величина", "объём", "объем")
AUDIENCE_WORD = "аудитор"
# «доля/доли/долю/долей/...» — словоформы, НЕ голая подстрока «дол» (которая
# ложно ловила бы «долго»/«должен»/«доллар»/«долина», см. докстринг).
SHARE_WORD_RE = re.compile(r"\bдол(я|и|ю|е|ей|ям|ями|ях)\b", re.IGNORECASE)


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ============================================================================
# Локальный индекс panel/segments/** (см. докстринг — намеренно НЕ run_study.py)
# ============================================================================


def build_segment_index(segments_root: Path) -> dict[str, list[Path]]:
    """Группирует panel/segments/**/*.yaml по stem файла (без чтения содержимого)."""
    index: dict[str, list[Path]] = {}
    if not segments_root.exists():
        return index
    for path in sorted(segments_root.rglob("*.yaml")):
        index.setdefault(path.stem, []).append(path)
    return index


def resolve_segment_yaml(sid: str, index: dict[str, list[Path]], segments_root: Path) -> Path:
    candidates = index.get(sid, [])
    if not candidates:
        raise ValueError(f"сегмент {sid!r} не найден в {segments_root} (искал рекурсивно **/{sid}.yaml).")
    if len(candidates) > 1:
        listed = "; ".join(str(p) for p in candidates)
        raise ValueError(f"сегмент {sid!r} — конфликт id, найден в нескольких местах: {listed}.")
    return candidates[0]


# ============================================================================
# Валидация схемы 05_targeting_<segment_id>.yaml
# ============================================================================


def _validate_axis_cell(cell: object, axis_name: str, path: Path) -> None:
    if not isinstance(cell, dict):
        raise ValueError(f"{path}: {axis_name!r} должен быть словарём {{values, trust, source}}, получено: {cell!r}")

    values = cell.get("values")
    if not values or not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
        raise ValueError(f"{path}: {axis_name}.values должен быть непустым списком непустых строк, получено: {values!r}")

    trust = cell.get("trust")
    if trust not in VALID_TRUST_MARKERS:
        raise ValueError(f"{path}: {axis_name}.trust = {trust!r} — должен быть одним из {VALID_TRUST_MARKERS}.")

    source = cell.get("source")
    if not source or not isinstance(source, str) or not source.strip():
        raise ValueError(f"{path}: {axis_name}.source должен быть непустой строкой (конкретный файл/раздел).")

    joined = " ".join(values) + " " + source
    low = joined.lower()

    forbidden_hit = next((t for t in FORBIDDEN_TARGETING_SUBSTRINGS if t in low), None)
    if forbidden_hit:
        raise ValueError(
            f"{path}: {axis_name} содержит запрещённый термин охватов/размеров аудитории ({forbidden_hit!r}) "
            f"— таргетинг-матрица описывает ТОЛЬКО правила отбора, не прогноз охватов "
            f"(cjm_prompts_ru.md, Стадия 4.5, «Красная линия»)."
        )
    if AUDIENCE_WORD in low and any(w in low for w in AUDIENCE_SIZE_WORDS):
        raise ValueError(
            f"{path}: {axis_name} упоминает размер/объём аудитории — запрещено (правила отбора, не измерение)."
        )
    if SHARE_WORD_RE.search(joined):
        raise ValueError(f"{path}: {axis_name} содержит «доля/доли/долю» — запрещено на этой стадии.")
    if cjm_lint.PERCENT_RE.search(joined) or cjm_lint.PERCENT_WORD_RE.search(joined):
        raise ValueError(
            f"{path}: {axis_name} содержит процент (в т.ч. словом, без «%») — запрещено: таргетинг-матрица "
            f"не прогнозирует охваты/доли, только правила отбора."
        )


def validate_targeting_record(doc: dict, path: Path, known_segment_ids: Optional[set[str]] = None) -> None:
    """Бросает ValueError с человекочитаемым сообщением при первой найденной проблеме схемы."""
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: документ должен быть словарём, получено: {type(doc).__name__}.")

    sid = doc.get("segment_id")
    if not sid or not isinstance(sid, str):
        raise ValueError(f"{path}: отсутствует обязательное поле segment_id (непустая строка).")
    if known_segment_ids is not None and sid not in known_segment_ids:
        raise ValueError(
            f"{path}: segment_id {sid!r} не найден в panel/segments/** — таргетинг можно строить только "
            f"для существующего сегмента панели."
        )

    missing_axes = [a for a in REQUIRED_AXES if a not in doc]
    if missing_axes:
        raise ValueError(
            f"{path}: отсутствуют обязательные оси {missing_axes} (все 4 фиксированные оси — top-level "
            f"ключи документа: {REQUIRED_AXES}; схема — references/cjm_prompts_ru.md, Стадия 4.5)."
        )
    for axis_name in REQUIRED_AXES:
        _validate_axis_cell(doc[axis_name], axis_name, path)


def check_gender_honesty(doc: dict, segment_doc: dict, path: Path) -> None:
    """
    Сверяет demographics с РЕАЛЬНЫМ persona_jitter.gender сегмента (см. докстринг
    модуля — единственная проверка контента, не только формы). Если у сегмента
    нет поля gender, но demographics не содержит честного «уточнить»/`trust:
    🔴` — похоже на выдуманный пол -> ValueError.
    """
    jitter = (segment_doc or {}).get("persona_jitter") or {}
    has_gender = bool(jitter.get("gender"))
    if has_gender:
        return  # пол в схеме сегмента есть — модель вольна описать его как считает нужным

    demographics = doc.get("demographics") or {}
    values_text = " ".join(str(v) for v in (demographics.get("values") or [])).lower()
    if "уточнить" not in values_text:
        raise ValueError(
            f"{path}: сегмент {doc.get('segment_id')!r} не имеет persona_jitter.gender, но "
            f"demographics.values не содержит честного «уточнить» — похоже на выдуманные данные о "
            f"поле (cjm_prompts_ru.md, Стадия 4.5: «не восстанавливать гендерный состав по описанию/"
            f"возрасту/языку сегмента — догадка здесь неотличима от выдумки»)."
        )
    if demographics.get("trust") != "🔴":
        raise ValueError(
            f"{path}: сегмент {doc.get('segment_id')!r} без persona_jitter.gender — demographics.trust "
            f"должен быть «🔴» (часть значения — «уточнить»), получено: {demographics.get('trust')!r}."
        )


# ============================================================================
# Загрузка одной записи (валидация схемы + сверка с сегментом)
# ============================================================================


@dataclass
class SegmentTargeting:
    segment_id: str
    segment_name: str
    source_path: Path
    demographics: dict
    income: dict
    content: dict
    purchases: dict


def load_segment_targeting(
    targeting_path: Path, segments_root: Path, segment_index: dict[str, list[Path]]
) -> SegmentTargeting:
    doc = load_yaml(targeting_path)
    known_ids = set(segment_index.keys())
    validate_targeting_record(doc, targeting_path, known_segment_ids=known_ids)

    sid = doc["segment_id"]
    seg_path = resolve_segment_yaml(sid, segment_index, segments_root)
    segment_doc = load_yaml(seg_path)

    check_gender_honesty(doc, segment_doc, targeting_path)

    segment_name = doc.get("segment_name") or segment_doc.get("name") or sid
    return SegmentTargeting(
        segment_id=sid,
        segment_name=str(segment_name),
        source_path=targeting_path,
        demographics=doc["demographics"],
        income=doc["income"],
        content=doc["content"],
        purchases=doc["purchases"],
    )


# ============================================================================
# Сборка markdown-таблицы (формат — references/cjm_report_template.md §7, буквально)
# ============================================================================


def render_markdown_table(records: list[SegmentTargeting]) -> str:
    lines = [
        "| Сегмент | " + " | ".join(AXIS_COLUMN_HEADER[a] for a in REQUIRED_AXES) + " |",
        "|---|---|---|---|---|",
    ]
    for r in records:
        cells = {
            "demographics": "; ".join(r.demographics["values"]),
            "income": "; ".join(r.income["values"]),
            "content": "; ".join(r.content["values"]),
            "purchases": "; ".join(r.purchases["values"]),
        }
        row = f"| {r.segment_name} (`{r.segment_id}`) | " + " | ".join(cells[a] for a in REQUIRED_AXES) + " |"
        lines.append(row)
    return "\n".join(lines) + "\n"


def build_targeting_matrix(records: list[SegmentTargeting], run_name: str) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cjm_run": run_name,
        "note": (
            "Правила отбора аудитории для медиазакупки — НЕ прогноз охватов/долей/CPM "
            "(spec_synthetic-panel_v1.2.md §Модуль 2 п.2)."
        ),
        "segments": [
            {
                "segment_id": r.segment_id,
                "segment_name": r.segment_name,
                "demographics": r.demographics,
                "income": r.income,
                "content": r.content,
                "purchases": r.purchases,
            }
            for r in records
        ],
    }


# ============================================================================
# CLI
# ============================================================================


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Валидирует 05_targeting_<segment_id>.yaml и собирает таблицу + targeting_matrix.yaml (spec v1.2 §Модуль 2)."
    )
    p.add_argument("--run", default=None, help="Каталог прогона, напр. runs/cjm_<имя>_<ts>/ (автопоиск 05_targeting_*.yaml)")
    p.add_argument(
        "--files", nargs="*", default=None, help="Явный список 05_targeting_*.yaml (переопределяет автопоиск в --run)"
    )
    p.add_argument("--segments-root", default=None, help="По умолчанию — <корень скилла>/panel/segments")
    p.add_argument("--out-table", default=None, help="По умолчанию — <run>/05_targeting_table.md")
    p.add_argument("--out-matrix", default=None, help="По умолчанию — <run>/targeting_matrix.yaml")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    skill_root = Path(__file__).resolve().parent.parent

    run_dir: Optional[Path] = None
    if args.run:
        run_dir = Path(args.run)
        if not run_dir.is_absolute():
            alt = skill_root / args.run
            run_dir = alt if alt.exists() else run_dir
        if not run_dir.exists() or not run_dir.is_dir():
            print(f"ОШИБКА: каталог прогона не найден: {args.run}", file=sys.stderr)
            sys.exit(1)

    if args.files:
        files = [Path(f) for f in args.files]
    elif run_dir is not None:
        files = sorted(run_dir.glob("05_targeting_*.yaml"))
    else:
        print("ОШИБКА: укажите --run <каталог прогона> и/или --files <файл ...>.", file=sys.stderr)
        sys.exit(1)

    if not files:
        print(
            f"ОШИБКА: не найдено ни одного 05_targeting_*.yaml"
            + (f" в {run_dir}" if run_dir else "")
            + " (стадия «Проекция на таргетинги» обязательна для каждого сегмента ядра — "
            "см. references/cjm_prompts_ru.md, Стадия 4.5).",
            file=sys.stderr,
        )
        sys.exit(1)

    missing = [f for f in files if not f.exists()]
    if missing:
        for f in missing:
            print(f"ОШИБКА: файл не найден: {f}", file=sys.stderr)
        sys.exit(1)

    segments_root = Path(args.segments_root) if args.segments_root else (skill_root / "panel" / "segments")
    segment_index = build_segment_index(segments_root)

    records: list[SegmentTargeting] = []
    for f in files:
        try:
            records.append(load_segment_targeting(f, segments_root, segment_index))
        except ValueError as exc:
            print(f"ОШИБКА: {exc}", file=sys.stderr)
            sys.exit(1)

    run_name = run_dir.name if run_dir else "targeting_export"
    table_md = render_markdown_table(records)
    matrix = build_targeting_matrix(records, run_name)

    default_dir = run_dir if run_dir else files[0].parent
    out_table = Path(args.out_table) if args.out_table else (default_dir / "05_targeting_table.md")
    out_matrix = Path(args.out_matrix) if args.out_matrix else (default_dir / "targeting_matrix.yaml")

    out_table.write_text(table_md, encoding="utf-8")
    out_matrix.write_text(yaml.safe_dump(matrix, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")

    print(f"-- {len(records)} сегмент(ов) обработано:")
    for r in records:
        note = "" if "уточнить" not in " ".join(r.demographics["values"]).lower() else " (пол: уточнить)"
        print(f"   {r.segment_id} — {r.segment_name}{note}")
    print(f"-- таблица (вставить между TARGETING_TABLE_START/END): {out_table}")
    print(f"-- машиночитаемая матрица (для баинга): {out_matrix}")


if __name__ == "__main__":
    main()

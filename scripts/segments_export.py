#!/usr/bin/env python3
"""
segments_export.py — конвертер объединённых CJM-сегментов в panel/segments/<category_slug>/<id>.yaml.

Реализует задачу [B2] п.4 спецификации v1.1: "01_segments_merged.yaml ->
panel/segments/<category_slug>/<id>.yaml по схеме v1 §9 + поля stability, source,
trust". Решение по докстрингу spec §3: конвертер — ОТДЕЛЬНЫЙ модуль
(segments_export.py), но `cjm_to_study.py` (та же зона сборки) ИМПОРТИРУЕТ и
вызывает `export_segments()` по умолчанию (см. cjm_to_study.py) — так стадия 4
одной командой кладёт и сегменты, и study.yaml, но логика конвертации остаётся
тестируемой и переиспользуемой отдельно (в т.ч. как самостоятельный CLI ниже).

    python scripts/segments_export.py --segments-merged runs/cjm_<имя>_<ts>/01_segments_merged.yaml
        [--category-slug <slug>] [--out-root panel/segments] [--only <id> ...]
        [--include-unstable] [--dry-run]

=== Схема входа: 01_segments_merged.yaml (моё рабочее допущение о формате —
    см. "issues" итогового отчёта сборщика: не зафиксировано отдельным разделом
    спецификации механически, формализовано здесь первым и должно быть сверено
    с тем, что реально пишет модель на стадии 1) ===

    cjm_study: cjm_hairloss_demo        # str, = studies/cjm_*.yaml: name
    runs_for_stability: 3               # int, для справки (сколько прогонов сравнивалось)
    generated_at: "2026-07-09T12:00:00+00:00"   # опционально
    segments:                            # ЯДРО — сегменты со stability "3/3"/"2/3"
      - id: molodye_mamy_posle_rodov     # str, snake_case, латиница — как в panel/segments/*.yaml (v1 §9)
        name: "Молодые мамы после родов"
        stability: "3/3"                 # str "N/M" — в скольких из M прогонов сегментации воспроизведён
        trust: "🟢"                       # опционально; дефолт при экспорте — 🟢 для ядра, 🟡 для unstable
        source: "модельное качественное (LLM-сегментация, без соцлистенинга)"
        description: >                   # см. v1 §9 — ВСЕ поля ниже опциональны и передаются как есть
          ...
        axes: {...}
        behavior: {...}
        language: ["...", ...]
        persona_jitter:
          age: [22, 34]
          income_level: [average, above_average]   # ЗАКРЫТЫЙ словарь generate.py, см. check_persona_jitter_vocab
          city_tier: [big_city, mid_city]           # ЗАКРЫТЫЙ словарь generate.py
        brands_context: >
          ...
        disclaimer: >                     # опционально — экспортёр ДОПИСЫВАЕТ свой стандартный
          ...                             # абзац поверх (не заменяет), см. build_segment_yaml
    unstable_segments:                    # "1/3" — по умолчанию НЕ экспортируются (см. --include-unstable)
      - id: ...
        stability: "1/3"
        ...

Единственные ЖЁСТКО обязательные поля записи сегмента — `id` и `name` (без них
невозможно ни имя файла, ни осмысленная persona-строка). Всё остальное —
опционально и передаётся насквозь (v1 §9 схема сама описывает это как
описательные поля; фактически потребляют `id`/`name`/`persona_jitter`/`language`
только generate.py/report.py — см. их код).

=== Схема выхода: panel/segments/<category_slug>/<id>.yaml (v1 §9 + доп. поля) ===

    id, name, mode: persona                    # как в v1 §9
    stability, source, trust                    # НОВЫЕ поля этой итерации (v1.1 §3)
    description, axes, behavior, language,
    persona_jitter, brands_context               # проброшены из входа как есть (если были)
    disclaimer                                    # исходный (если был) + дописанный стандартный абзац

category_slug: по умолчанию = имя `cjm_study` без префикса `cjm_`, slugified
(нижний регистр, не-[a-z0-9] -> "_"). Пример из DoD §5.3: cjm_study=
"cjm_hairloss_demo" -> category_slug="hairloss_demo" ->
panel/segments/hairloss_demo/<id>.yaml.

--include-unstable: неустойчивые (1/3) сегменты пишутся в СОСЕДНЮЮ директорию
panel/segments/<category_slug>_unstable/<id>.yaml (не внутрь основной — чтобы
случайно не оказаться в обычном обороте без явной ссылки по id) с
принудительным trust="🟡" и явной пометкой «НЕУСТОЙЧИВЫЙ» в disclaimer.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

import yaml

CATEGORY_SLUG_STRIP_PREFIX = "cjm_"

# Поля v1 §9, которые просто пробрасываются как есть, если присутствуют во входной записи.
V1_PASSTHROUGH_FIELDS = ("description", "axes", "behavior", "language", "persona_jitter", "brands_context")

# Закрытые словари generate.py (_INCOME_LEVEL_RU/_CITY_TIER_RU) — см. scripts/generate.py.
# Значения ВНЕ этих множеств не роняют прогон (generate.py переводит "как есть" с
# предупреждением в лог), но лучше поймать это на экспорте, а не в середине прогона.
KNOWN_INCOME_LEVELS = ("below_average", "average", "above_average", "high")
KNOWN_CITY_TIERS = ("million_plus", "big_city", "mid_city", "small_town_rural")


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def slugify_category(name: str) -> str:
    """cjm_hairloss_demo -> hairloss_demo (DoD §5.3). Устойчиво к нестандартным символам в name."""
    s = (name or "").strip()
    if s.startswith(CATEGORY_SLUG_STRIP_PREFIX):
        s = s[len(CATEGORY_SLUG_STRIP_PREFIX) :]
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "cjm_segment_map"


def load_merged_segments(path: Path) -> tuple[dict, list[dict], list[dict]]:
    """Возвращает (meta, core_segments, unstable_segments) из 01_segments_merged.yaml."""
    data = load_yaml(path)
    core = data.get("segments") or []
    unstable = data.get("unstable_segments") or []
    meta = {k: v for k, v in data.items() if k not in ("segments", "unstable_segments")}
    if not isinstance(core, list) or not isinstance(unstable, list):
        raise ValueError(f"{path}: `segments`/`unstable_segments` должны быть списками записей сегментов.")
    return meta, core, unstable


def validate_segment_record(segment: dict, path: Path) -> None:
    if not isinstance(segment, dict):
        raise ValueError(f"{path}: запись сегмента должна быть словарём, получено: {segment!r}")
    missing = [f for f in ("id", "name") if not segment.get(f)]
    if missing:
        raise ValueError(f"{path}: сегмент без обязательных полей {missing}: {segment!r}")


def check_persona_jitter_vocab(segment: dict) -> list[str]:
    """Возвращает список предупреждений (не бросает исключение) о значениях вне закрытых словарей generate.py."""
    warnings: list[str] = []
    jitter = segment.get("persona_jitter") or {}
    for key, allowed in (("income_level", KNOWN_INCOME_LEVELS), ("city_tier", KNOWN_CITY_TIERS)):
        values = jitter.get(key) or []
        bad = [v for v in values if v not in allowed]
        if bad:
            warnings.append(
                f"сегмент {segment.get('id')!r}: persona_jitter.{key} содержит значения вне словаря "
                f"generate.py {allowed}: {bad} — generate.py переведёт их 'как есть' с предупреждением "
                f"в лог (см. generate.py::_translate_jitter_token), персона-строка будет читаться хуже."
            )
    return warnings


def build_segment_yaml(segment: dict, unstable: bool = False) -> dict:
    out: dict = {
        "id": segment["id"],
        "name": segment["name"],
        "mode": "persona",  # v1; data_grounded — фаза B (не меняется этой итерацией, см. spec_v1 §9)
        "stability": segment.get("stability", "?"),
        "source": segment.get("source", "модельное качественное (LLM-сегментация, источник не указан)"),
        "trust": segment.get("trust") or ("🟡" if unstable else "🟢"),
    }
    for f in V1_PASSTHROUGH_FIELDS:
        if segment.get(f) is not None:
            out[f] = segment[f]

    extra_disclaimer = (
        f"Persona-режим: качественная симуляция сегмента, полученная в режиме segment_map "
        f"(AI CJM), устойчивость {out['stability']}."
    )
    if unstable:
        extra_disclaimer += (
            " НЕУСТОЙЧИВЫЙ (воспроизведён только в 1 прогоне сегментации из нескольких) — "
            "не использовать для клиентских выводов без повторного подтверждения в следующих "
            "прогонах сегментации (spec_synthetic-panel_v1.1_segment_map.md §2, стадия 1)."
        )
    extra_disclaimer += (
        " Не откалиброван на реальных данных; не использовать для количественных выводов "
        "(доли, охваты, точный purchase intent) без независимого подтверждения."
    )
    existing = (segment.get("disclaimer") or "").strip()
    out["disclaimer"] = f"{existing}\n\n{extra_disclaimer}".strip() if existing else extra_disclaimer
    return out


def render_segment_file(doc: dict, segments_merged_path: Path, cjm_study: str) -> str:
    header = (
        f'# Сегмент "{doc["name"]}" — сгенерировано scripts/segments_export.py из merged-сегментов\n'
        f"# CJM-прогона «{cjm_study}» (устойчивость {doc['stability']}).\n"
        f"# Источник: {segments_merged_path}.\n"
        f"# НЕ редактировать вручную — при повторном прогоне сегментации файл будет перегенерирован\n"
        f"# повторным запуском cjm_to_study.py/segments_export.py для этого прогона.\n"
    )
    body = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100)
    return header + body


def export_segments(
    segments_merged_path: Path,
    skill_root: Path,
    category_slug: Optional[str] = None,
    only: Optional[list[str]] = None,
    include_unstable: bool = False,
    out_root: Optional[Path] = None,
    dry_run: bool = False,
) -> dict:
    """
    Точка входа для импорта (cjm_to_study.py) и для CLI ниже.

    Возвращает summary: {"category_slug", "target_dir", "written": [Path, ...],
    "warnings": [str, ...]}. Бросает ValueError на структурных проблемах входа
    (отсутствующие id/name, дублирующиеся id между ядром/unstable) — вызывающий
    код (main() ниже или cjm_to_study.py) должен ловить и печатать понятно.
    """
    meta, core, unstable = load_merged_segments(segments_merged_path)
    cjm_study = meta.get("cjm_study") or segments_merged_path.parent.name
    slug = category_slug or slugify_category(cjm_study)
    root = out_root or (skill_root / "panel" / "segments")
    target_dir = root / slug
    unstable_dir = root / f"{slug}_unstable"

    warnings: list[str] = []
    written: list[Path] = []
    ids_seen: set[str] = set()

    def process(records: list[dict], unstable_flag: bool, dest_dir: Path) -> None:
        for seg in records:
            validate_segment_record(seg, segments_merged_path)
            sid = seg["id"]
            if only and sid not in only:
                continue
            if sid in ids_seen:
                raise ValueError(
                    f"{segments_merged_path}: дублирующийся id сегмента между ядром и "
                    f"неустойчивыми (или внутри одного списка): {sid!r}"
                )
            ids_seen.add(sid)
            warnings.extend(check_persona_jitter_vocab(seg))
            doc = build_segment_yaml(seg, unstable=unstable_flag)
            out_path = dest_dir / f"{sid}.yaml"
            if not dry_run:
                dest_dir.mkdir(parents=True, exist_ok=True)
                out_path.write_text(render_segment_file(doc, segments_merged_path, cjm_study), encoding="utf-8")
            written.append(out_path)

    process(core, False, target_dir)
    if include_unstable:
        process(unstable, True, unstable_dir)

    return {
        "category_slug": slug,
        "target_dir": target_dir,
        "written": written,
        "warnings": warnings,
    }


# ============================================================================
# CLI
# ============================================================================


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Конвертирует 01_segments_merged.yaml в panel/segments/<category_slug>/<id>.yaml."
    )
    p.add_argument("--segments-merged", required=True, help="Путь к 01_segments_merged.yaml")
    p.add_argument("--category-slug", default=None, help="По умолчанию — из cjm_study без префикса cjm_")
    p.add_argument("--out-root", default=None, help="По умолчанию — <корень скилла>/panel/segments")
    p.add_argument("--only", nargs="*", default=None, help="Экспортировать только эти id (по умолчанию — все ядро)")
    p.add_argument(
        "--include-unstable",
        action="store_true",
        help="Также экспортировать неустойчивые (1/3) сегменты в panel/segments/<slug>_unstable/",
    )
    p.add_argument("--dry-run", action="store_true", help="Ничего не писать на диск, только показать план")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    skill_root = Path(__file__).resolve().parent.parent

    path = Path(args.segments_merged)
    if not path.exists():
        print(f"ОШИБКА: файл не найден: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        result = export_segments(
            path,
            skill_root,
            category_slug=args.category_slug,
            only=args.only,
            include_unstable=args.include_unstable,
            out_root=Path(args.out_root) if args.out_root else None,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        sys.exit(1)

    for w in result["warnings"]:
        print(f"ПРЕДУПРЕЖДЕНИЕ: {w}", file=sys.stderr)

    prefix = "(dry-run, ничего не записано) " if args.dry_run else ""
    print(f"-- category_slug: {result['category_slug']} ({result['target_dir']})")
    for p in result["written"]:
        print(f"-- {prefix}{p}")
    print(f"\nГотово: {len(result['written'])} сегмент(ов).")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
comp_to_study.py — 04_messages.yaml (стадия C4, режим competitive_positioning)
-> studies/*.yaml (claims_ranking), spec_synthetic-panel_v1.2.md §Модуль 3 п.2
(стадия C4.3), задание [B2] п.4.

    python scripts/comp_to_study.py --run runs/comp_<имя>_<ts>/
    python scripts/run_study.py --study studies/<сгенерированный>.yaml --stage all

=== Решение "cjm_to_study.py расширить ИЛИ отдельный comp_to_study.py?"
    (задание [B2] п.4 явно оставляло выбор открытым — спецификация §Модуль 3
    тоже) ===

РЕШЕНО: отдельный файл. Обоснование:
  1. references/competitive_prompts_ru.md (зона [B3], уже написан на момент
     реализации этого файла) УЖЕ фиксирует именно эту команду буквально —
     `python scripts/comp_to_study.py --run runs/comp_<имя>_<ts>/` — с явной
     пометкой: "Имя моста (comp_to_study.py как отдельный скрипт, а не флаг
     существующего cjm_to_study.py) — рабочее решение этой версии
     документации; если при реализации выбран другой вариант ... команду и
     это примечание нужно поправить здесь и в SKILL.md §5 синхронно." Раз
     реализация СОВПАДАЕТ с уже задокументированным решением — ничего
     поправлять не нужно, синхронизация уже на месте.
  2. Форма входа принципиально другая: cjm_to_study.py экспортирует ЗАНОВО
     СОЗДАННЫЕ сегменты (segments_export.export_segments() из
     01_segments_merged.yaml) И строит study.yaml; comp_to_study.py НЕ
     экспортирует сегменты вовсе — сегменты конкурентного режима УЖЕ
     существуют в panel/segments/** (переданы по id в comp_<имя>.yaml,
     проверены competitive_init.py при scaffold'е). Общего кода для экспорта
     сегментов между режимами просто нет, а RTB-метаданные (targeted_barrier/
     targeted_benefit/rtb_type) cjm-режима и метаданные сообщений C4
     (territory/targeted_competitor/targeted_barrier/rtb_type) — структурно
     похожи, но с разными именами полей и разным способом получения списка
     "каких сегментов это касается" (manifest["test_segments"] здесь против
     01_segments_merged.yaml здесь vs там).
  3. Раздутие cjm_to_study.py веткой if/else на "это cjm-прогон или
     comp-прогон" ухудшило бы читаемость уже большого файла ради сомнительной
     экономии ~150 строк, которые и так не идентичны между режимами.
Общий, ДЕЙСТВИТЕЛЬНО переиспользуемый код (нормализация id/text кандидатов,
проверка "минимум 2 стимула") вынесен в cjm_to_study.py как
normalize_candidates() и импортируется отсюда — не дублируется (см. импорт
ниже). record_stage() (запись в manifest.json) переиспользуется из
cjm_init.py — она НЕ завязана на cjm-специфичные поля, это общий generic
хелпер для любого manifest.json скилла.

=== Вход: --run <run_dir> (создан competitive_init.py) ===

Внутри run_dir ожидаются:
    manifest.json        — создан competitive_init.py; используется для имени
                            прогона, `test_segments` (авторитетный список id,
                            разрешённых для SSR-теста — см. ниже) и record_stage.
    04_messages.yaml      — сообщения стадии C4 (эту часть создаёт модель,
    (или .yml/.json,        ведущая прогон, по references/competitive_prompts_ru.md
     или --messages PATH)   §C4.2-C4.3, ПЕРЕД вызовом comp_to_study.py).

=== Схема 04_messages.yaml — форма А: общий пул (см.
    references/competitive_prompts_ru.md, C4.3, буквально) ===

    segments: [vzrosloe_akne_ne_po_vozrastu, ostorozhnye_boyus_huzhe]  # id из manifest["test_segments"]
    messages:
      - id: msg1
        text: "..."
        territory: territory_1            # опционально
        targeted_competitor: "Скинорен"    # опционально
        targeted_barrier: "..."            # опционально
        rtb_type: "..."                    # опционально
      - id: msg2
        text: "..."
      # минимум 2 кандидата (run_study.py откажет study.yaml с <2 стимулами)

=== Форма Б: посегментно (когда тексты намеренно разные для каждого сегмента
    — "аналогично форме Б в cjm_prompts_ru.md §4.3", competitive_prompts_ru.md
    C4.3, буквально) ===

    by_segment:
      vzrosloe_akne_ne_po_vozrastu:
        - {id: msg1, text: "...", territory: "...", targeted_competitor: "...", ...}
        - {id: msg2, text: "..."}
      ostorozhnye_boyus_huzhe:
        - {id: msg1, text: "..."}
        - {id: msg2, text: "..."}

Ровно одна из двух форм должна присутствовать. Элементы списка — либо голые
строки (id присваивается автоматически msg1..N), либо {id, text, ...метаданные}.

=== segments/by_segment-ключи ДОЛЖНЫ быть подмножеством manifest["test_segments"]
    (НЕ manifest["segments"]) ===

manifest["test_segments"] — авторитетный список сегментов, которые competitive_init.py
уже проверил как подмножество `segments` (общий список качественного анализа
C0-C3) при scaffold'е прогона. comp_to_study.py сверяет 04_messages.yaml именно
с этим списком (а не заново со всем panel/segments/**) — дешевле и точнее
отражает намерение "какие сегменты реально SSR-тестируются в этом прогоне".

=== Метаданные сообщений (territory/targeted_competitor/targeted_barrier/
    rtb_type) — та же природа, что RTB-метаданные cjm_to_study.py (находка №3,
    review_v1.1.md), другие имена полей ===

Схема run_study.py для stimuli[] строго {id, text} — лишние ключи молча
отбрасываются, не пишутся в manifest прогона run_study.py. Метаданные
переносятся в `notes` итогового study.yaml человекочитаемым текстом (см.
extract_comp_metadata/format_comp_metadata_note) — тот, кто дальше собирает
comp_report.md (references/competitive_report_template.md, раздел 6 "Тест
сообщений"), читает их оттуда. Отсутствие полей -> "—" в notes (тот же
принцип, что и в cjm_to_study.py/шаблонах отчётов этого скилла).

=== Дальше ===

    python scripts/run_study.py --study studies/<сгенерированный>.yaml --stage all

(agent-режим — тот же протокол v1: 2-5 предложений от первого лица, БЕЗ CoT,
БЕЗ числовой оценки в тексте ответа — не переопределяется здесь, см.
references/competitive_prompts_ru.md, преамбула "CoT".)

Юнит-тесты: scripts/test_comp_to_study.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import cjm_init  # noqa: E402 — переиспользуем record_stage (generic, не cjm-специфичный)
import cjm_to_study  # noqa: E402 — переиспользуем normalize_candidates (id/text, min 2 стимула)

DEFAULT_QUESTION_SCALE = "purchase_intent"  # или "appeal" — см. --question-scale
DEFAULT_RESPONDENTS_PER_SEGMENT = 10  # согласуется с cjm_to_study.py (тот же протокол v1)
DEFAULT_SAMPLES_PER_RESPONDENT = 2

MESSAGES_FILENAMES = ("04_messages.yaml", "04_messages.yml", "04_messages.json")

COMP_METADATA_FIELDS = ("territory", "targeted_competitor", "targeted_barrier", "rtb_type")


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(1)


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_data_file(path: Path) -> dict:
    if path.suffix.lower() == ".json":
        with path.open("r", encoding="utf-8") as f:
            return json.load(f) or {}
    return load_yaml(path)


def load_manifest(run_dir: Path) -> dict:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        fail(
            f"ОШИБКА: {manifest_path} не найден — сначала выполните "
            f"`python scripts/competitive_init.py --study studies/comp_<имя>.yaml` для этого прогона."
        )
    with manifest_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_comp_name(run_dir: Path, manifest: dict) -> str:
    if manifest.get("study_name"):
        return str(manifest["study_name"])
    return re.sub(r"_\d{8}-\d{4}$", "", run_dir.name)


def find_messages_file(run_dir: Path, explicit: Optional[str]) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            fail(f"ОШИБКА: --messages указывает на несуществующий файл: {p}")
        return p
    for name in MESSAGES_FILENAMES:
        candidate = run_dir / name
        if candidate.exists():
            return candidate
    fail(
        f"ОШИБКА: не найден файл сообщений в {run_dir} (искал: {', '.join(MESSAGES_FILENAMES)}).\n"
        f"Стадия C4 (модель формулирует отстроечные сообщения по "
        f"references/competitive_prompts_ru.md, C4.2) должна записать один из этих файлов "
        f"ПЕРЕД вызовом comp_to_study.py, либо укажите путь явно через --messages."
    )
    raise AssertionError("unreachable")  # fail() всегда завершает процесс


# ============================================================================
# Метаданные сообщений (см. докстринг модуля)
# ============================================================================


def extract_comp_metadata(raw_list: list, normalized_ids: list[str]) -> list[dict]:
    out = []
    for i, item in enumerate(raw_list):
        cid = normalized_ids[i] if i < len(normalized_ids) else f"msg{i + 1}"
        meta = {"id": cid}
        for field in COMP_METADATA_FIELDS:
            value = item.get(field) if isinstance(item, dict) else None
            meta[field] = str(value).strip() if value else None
        out.append(meta)
    return out


def format_comp_metadata_note(meta_list: list[dict]) -> str:
    any_present = any(m.get(f) for m in meta_list for f in COMP_METADATA_FIELDS)
    if not any_present:
        return (
            "Метаданные сообщений (territory/targeted_competitor/targeted_barrier/rtb_type) не "
            "указаны в исходном 04_messages.yaml этого прогона — при сборке comp_report.md "
            "(раздел 6, «Тест сообщений») подставлять «—» в соответствующие столбцы."
        )
    lines = ["Метаданные сообщений C4 (справочно, не используются run_study.py):"]
    for m in meta_list:
        territory = m.get("territory") or "—"
        competitor = m.get("targeted_competitor") or "—"
        barrier = m.get("targeted_barrier") or "—"
        rtb_type = m.get("rtb_type") or "—"
        lines.append(
            f"  - {m['id']}: территория «{territory}» -> конкурент «{competitor}» -> "
            f"барьер «{barrier}» -> тип «{rtb_type}»"
        )
    return "\n".join(lines)


# ============================================================================
# Валидация сегментов против manifest["test_segments"]
# ============================================================================


def validate_segments_against_manifest(ids: list[str], manifest: dict, messages_path: Path, run_dir: Path) -> None:
    allowed = manifest.get("test_segments")
    if not allowed:
        fail(
            f"ОШИБКА: manifest.json прогона {run_dir} не содержит test_segments (создан не "
            f"competitive_init.py?) — не могу проверить, какие сегменты разрешено SSR-тестировать."
        )
    allowed_set = set(allowed)
    unknown = [sid for sid in ids if sid not in allowed_set]
    if unknown:
        fail(
            f"ОШИБКА: {messages_path} ссылается на сегмент(ы) {unknown}, отсутствующие в "
            f"test_segments манифеста ({sorted(allowed_set)}). Добавьте их в "
            f"studies/comp_<имя>.yaml: test_segments и пересоздайте прогон через "
            f"competitive_init.py, либо исправьте 04_messages.yaml."
        )


# ============================================================================
# Построение study.yaml
# ============================================================================


def build_studies(
    messages_data: dict,
    comp_name: str,
    manifest: dict,
    messages_path: Path,
    run_dir: Path,
    respondents_per_segment: int,
    samples_per_respondent: int,
    question_scale: str,
) -> list[dict]:
    has_shared = "messages" in messages_data
    has_per_segment = "by_segment" in messages_data
    if has_shared and has_per_segment:
        fail(
            f"ОШИБКА: {messages_path} содержит и `messages` (форма А, общий пул), и `by_segment` "
            f"(форма Б, посегментно) — используйте ТОЛЬКО ОДНУ форму (см. docstring comp_to_study.py)."
        )
    if not has_shared and not has_per_segment:
        fail(
            f"ОШИБКА: {messages_path} должен содержать либо `messages`+`segments` (форма А), либо "
            f"`by_segment` (форма Б) — см. docstring comp_to_study.py."
        )

    studies: list[dict] = []

    if has_shared:
        segments_list = messages_data.get("segments")
        if not segments_list or not isinstance(segments_list, list):
            fail(
                f"ОШИБКА: {messages_path} — форма А (`messages`) требует также непустой список "
                f"`segments: [...]` — какие тестируемые сегменты озвучивают эти сообщения."
            )
        validate_segments_against_manifest(segments_list, manifest, messages_path, run_dir)
        candidates = cjm_to_study.normalize_candidates(messages_data["messages"], f"{messages_path}:messages")
        meta = extract_comp_metadata(messages_data["messages"], [c["id"] for c in candidates])
        studies.append(
            {
                "name": f"{comp_name}_c4",
                "type": "claims_ranking",
                "question_scale": question_scale,
                "stimuli": candidates,
                "segments": list(segments_list),
                "respondents_per_segment": respondents_per_segment,
                "samples_per_respondent": samples_per_respondent,
                "notes": (
                    f"Автоматически сгенерировано comp_to_study.py из {messages_path.name} "
                    f"(конкурентный прогон {comp_name}, наш бренд: {manifest.get('our_brand', '—')}). "
                    f"Сообщения C4 — 🟡 гипотезы для отстройки, результат встраивается в comp_report.md "
                    f"(spec_synthetic-panel_v1.2.md §Модуль 3 п.2, стадия C4).\n"
                    f"{format_comp_metadata_note(meta)}"
                ),
            }
        )
    else:
        by_segment = messages_data.get("by_segment")
        if not by_segment or not isinstance(by_segment, dict):
            fail(f"ОШИБКА: {messages_path} — `by_segment` должен быть непустым словарём id_сегмента -> список сообщений.")
        validate_segments_against_manifest(list(by_segment.keys()), manifest, messages_path, run_dir)
        for sid, raw_messages in by_segment.items():
            candidates = cjm_to_study.normalize_candidates(raw_messages, f"{messages_path}:by_segment[{sid}]")
            meta = extract_comp_metadata(raw_messages, [c["id"] for c in candidates])
            studies.append(
                {
                    "name": f"{comp_name}_c4_{sid}",
                    "type": "claims_ranking",
                    "question_scale": question_scale,
                    "stimuli": candidates,
                    "segments": [sid],
                    "respondents_per_segment": respondents_per_segment,
                    "samples_per_respondent": samples_per_respondent,
                    "notes": (
                        f"Автоматически сгенерировано comp_to_study.py из {messages_path.name} для "
                        f"сегмента «{sid}» (конкурентный прогон {comp_name}, форма by_segment). "
                        f"Сообщения C4 — 🟡 гипотезы для отстройки (spec_synthetic-panel_v1.2.md "
                        f"§Модуль 3 п.2, стадия C4).\n"
                        f"{format_comp_metadata_note(meta)}"
                    ),
                }
            )

    return studies


def write_study_yaml(study: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{study['name']}.yaml"
    header = (
        f"# study.yaml сгенерирован scripts/comp_to_study.py — НЕ редактировать вручную,\n"
        f"# перегенерируется повторным запуском для этого конкурентного прогона.\n"
    )
    body = yaml.safe_dump(study, allow_unicode=True, sort_keys=False, width=100)
    out_path.write_text(header + body, encoding="utf-8")
    return out_path


# ============================================================================
# main
# ============================================================================


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="04_messages.yaml (C4) + manifest.json -> studies/*.yaml (spec v1.2 §Модуль 3 п.2)."
    )
    p.add_argument("--run", required=True, help="Каталог прогона competitive_positioning, напр. runs/comp_<имя>_<ts>/")
    p.add_argument(
        "--messages",
        default=None,
        help="Явный путь к файлу сообщений (по умолчанию ищется 04_messages.yaml/.yml/.json в --run).",
    )
    p.add_argument("--out-dir", default=None, help="Куда писать study.yaml (по умолчанию <корень скилла>/studies).")
    p.add_argument("--respondents-per-segment", type=int, default=DEFAULT_RESPONDENTS_PER_SEGMENT)
    p.add_argument("--samples-per-respondent", type=int, default=DEFAULT_SAMPLES_PER_RESPONDENT)
    p.add_argument(
        "--question-scale",
        default=DEFAULT_QUESTION_SCALE,
        help="id шкалы из references/anchors_ru.yaml — purchase_intent (по умолчанию) или appeal.",
    )
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    skill_root = Path(__file__).resolve().parent.parent

    run_dir = Path(args.run)
    if not run_dir.is_absolute():
        alt = skill_root / args.run
        run_dir = alt if alt.exists() else run_dir
    if not run_dir.exists() or not run_dir.is_dir():
        fail(f"ОШИБКА: каталог прогона не найден: {args.run}")

    manifest = load_manifest(run_dir)
    comp_name = resolve_comp_name(run_dir, manifest)

    messages_path = find_messages_file(run_dir, args.messages)
    messages_data = load_data_file(messages_path)

    studies = build_studies(
        messages_data,
        comp_name,
        manifest,
        messages_path,
        run_dir,
        args.respondents_per_segment,
        args.samples_per_respondent,
        args.question_scale,
    )

    studies_out_dir = Path(args.out_dir) if args.out_dir else (skill_root / "studies")
    written_study_paths: list[Path] = []
    for study in studies:
        out_path = write_study_yaml(study, studies_out_dir)
        written_study_paths.append(out_path)
        if len(studies) == 1:
            copy_name = "04_messages_study.yaml"
        else:
            copy_name = f"04_messages_study_{study['segments'][0]}.yaml"
        (run_dir / copy_name).write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"-- {out_path} (копия: {run_dir / copy_name})")

    try:
        cjm_init.record_stage(
            run_dir,
            "c4_study_generated",
            {
                "messages_path": str(messages_path),
                "shape": "shared" if "messages" in messages_data else "by_segment",
                "studies": [str(p) for p in written_study_paths],
            },
        )
    except FileNotFoundError as exc:
        print(f"ПРЕДУПРЕЖДЕНИЕ: manifest.json не обновлён ({exc})", file=sys.stderr)

    print("\nГотово. Дальше для каждого сгенерированного study.yaml:")
    for p in written_study_paths:
        print(f"  python scripts/run_study.py --study {p} --stage all")


if __name__ == "__main__":
    main()

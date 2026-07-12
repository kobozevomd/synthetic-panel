#!/usr/bin/env python3
"""
cjm_to_study.py — 01_segments_merged.yaml + RTB-кандидаты -> studies/*.yaml (claims_ranking).

Реализует spec_synthetic-panel_v1.1_segment_map.md §2, стадия 4 + задание [B2] п.3.
CLI зафиксирован в SKILL.md §4:

    python scripts/cjm_to_study.py --run runs/cjm_<имя>_<ts>/

=== v1.2, режим competitive_positioning: comp_to_study.py — ОТДЕЛЬНЫЙ файл,
    не расширение этого (spec_synthetic-panel_v1.2.md §Модуль 3, задание [B2]
    п.4 явно оставляло выбор открытым) ===

Мост «сообщения C4 -> study.yaml» для режима "Конкурентная отстройка" реализован
как scripts/comp_to_study.py — НЕ как новая ветка/флаг здесь. Полное обоснование
решения (включая согласование с уже написанным на момент реализации
references/competitive_prompts_ru.md, который прямо фиксирует команду
`python scripts/comp_to_study.py --run ...`) — в докстринге comp_to_study.py,
раздел "Решение...". Коротко: разная форма входа (там сегменты УЖЕ существуют
в panel/segments/**, экспорта нет вовсе), разные имена метаданных сообщений
(territory/targeted_competitor вместо targeted_barrier/targeted_benefit),
разный источник "каких сегментов это касается" (manifest["test_segments"]
против 01_segments_merged.yaml). Общий код (normalize_candidates — id/text,
проверка "минимум 2 стимула") ИМПОРТИРУЕТСЯ comp_to_study.py отсюда, а не
дублируется — см. её докстринг/импорты.

По умолчанию ОДНОЙ командой (см. SKILL.md: "кладёт сегменты ядра в
panel/segments/<category_slug>/<id>.yaml ... и собирает RTB-кандидатов в
studies/*.yaml"):
    1. Экспортирует ВСЕ сегменты ЯДРА (не только тестируемые — они должны быть
       доступны и для будущих studies/*.yaml других типов) через
       segments_export.export_segments() в panel/segments/<category_slug>/.
    2. Строит один или несколько studies/*.yaml типа claims_ranking из RTB-
       кандидатов, кладёт копию в run_dir как 04_rtb_study.yaml (один сгенерированный
       файл) либо 04_rtb_study_<segment_id>.yaml (несколько — форма by_segment).
    3. Дописывает manifest.json прогона (stage "rtb_study_generated").

=== Вход: --run <run_dir> ===

Внутри run_dir ожидаются (создаются предыдущими стадиями, см. SKILL.md §4):
    manifest.json               — создан cjm_init.py (стадия 0); используется для
                                   имени CJM-прогона и как место для record_stage.
    01_segments_merged.yaml      — создан на стадии 1 (схема — см. segments_export.py
                                   docstring: meta.cjm_study, segments[], unstable_segments[]).
    04_rtb_candidates.yaml       — RTB-кандидаты стадии 4 (эту зону моделирует модель,
    (или .yml / .json,             ведущая скилл, по шаблону references/cjm_prompts_ru.md,
     или --rtb-candidates PATH)    ПЕРЕД вызовом cjm_to_study.py). ДВЕ поддерживаемые формы
                                   (см. ниже) — какую использовать, решает автор файла.

=== Схема 04_rtb_candidates.yaml — форма А: общий пул (РЕКОМЕНДУЕТСЯ по умолчанию,
    так как позволяет панели сравнить реакцию РАЗНЫХ сегментов на ОДНИ и те же
    формулировки — это и есть основная ценность панели, см. DoD §5.2: "4 RTB-
    кандидата x 2 сегмента x 10 респондентов x 2 сэмпла" = ровно 4 ОБЩИХ стимула,
    не 4-на-каждый-сегмент) ===

    segments: [molodye_mamy_posle_rodov, drugoi_segment]   # id из ядра 01_segments_merged.yaml
    rtb_candidates:
      - id: rtb1
        text: "Для пациентов, выбирающих ..., наш продукт предлагает ... благодаря ..., что подтверждено ..."
        targeted_barrier: "..."   # опционально (см. ниже) — барьер, который адресует кандидат
        targeted_benefit: "..."   # опционально — benefit, выведенный на первый план
        rtb_type: "..."           # опционально — тип доказательства/авторитета
      - id: rtb2
        text: "..."
      # (rtb_candidates_per_segment из cjm_<имя>.yaml — ориентир по количеству, не жёсткая проверка здесь)

-> ОДИН studies/<cjm_name>_rtb.yaml с этими стимулами и этими сегментами.

=== Форма Б: посегментно (когда RTB намеренно разный текст на сегмент и
    кросс-тест одного текста на чужом сегменте не имеет смысла) ===

    by_segment:
      molodye_mamy_posle_rodov:
        - {id: rtb1, text: "...", targeted_barrier: "...", targeted_benefit: "...", rtb_type: "..."}
        - {id: rtb2, text: "..."}
      drugoi_segment:
        - {id: rtb1, text: "..."}
        - {id: rtb2, text: "..."}

-> ПО ОДНОМУ studies/<cjm_name>_rtb_<segment_id>.yaml НА КАЖДЫЙ ключ (сегменты
   этого study.yaml — список из ОДНОГО id).

Ровно одна из двух форм (`rtb_candidates`+`segments` ИЛИ `by_segment`) должна
присутствовать — не обе и не ни одна. Каждый список кандидатов — элементы либо
голые строки (id присваивается автоматически rtb1..rtbN), либо {id, text}.
Минимум 2 кандидата на сегмент/пул (run_study.py всё равно откажет study.yaml
с <2 стимулами — проверяем здесь заранее, чтобы дать понятную ошибку раньше).

=== targeted_barrier/targeted_benefit/rtb_type (находка №3, MAJOR,
    docs/review_v1.1.md) ===

references/cjm_prompts_ru.md §4.2-4.3 требует эти три поля на КАЖДОГО кандидата
в НОВЫХ прогонах (нужны для таблицы §6.1 cjm_report_template.md — «Целевой
барьер»/«Целевой benefit»/«Тип RTB»), но здесь, в cjm_to_study.py, они строго
ОПЦИОНАЛЬНЫ — старые 04_rtb_candidates.yaml без этих полей (например, пилот
cjm_hairloss_demo_20260710-0017) читаются как прежде, без ошибки. Эти поля НЕ
попадают в stimuli[] (схема run_study.py v1 для стимула — строго {id, text},
не меняется этим файлом) — вместо этого переносятся в `notes` итогового
study.yaml человекочитаемым текстом (см. extract_rtb_metadata/
format_rtb_metadata_note ниже) для того, кто дальше вручную собирает
cjm_report.md §6.1. Отсутствующее у конкретного кандидата поле → в notes для
него подставляется «—» (тот же принцип, что и в самом cjm_report_template.md).

Каждый id тестируемого сегмента ДОЛЖЕН быть в ЯДРЕ 01_segments_merged.yaml
(segments[], не unstable_segments[]) — иначе явный отказ (нельзя RTB-тестировать
неустойчивый сегмент по умолчанию, см. spec §2).

=== Дальше ===

    python scripts/run_study.py --study studies/<сгенерированный>.yaml --stage all

(agent-режим — тот же протокол генерации ответов персонами, что и в режимах 1-4,
включая запрет CoT и числовых оценок в тексте ответа — см. SKILL.md §2.)
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

import cjm_init  # noqa: E402 — переиспользуем record_stage (та же зона сборки)
import segments_export  # noqa: E402

DEFAULT_QUESTION_SCALE = "purchase_intent"
DEFAULT_RESPONDENTS_PER_SEGMENT = 10  # см. DoD §5.2 пилота
DEFAULT_SAMPLES_PER_RESPONDENT = 2  # согласуется с config.yaml: llm.samples_per_respondent

RTB_CANDIDATES_FILENAMES = ("04_rtb_candidates.yaml", "04_rtb_candidates.yml", "04_rtb_candidates.json")


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


# ============================================================================
# Разрешение имени CJM-прогона + путей
# ============================================================================


def resolve_cjm_name(run_dir: Path, merged_meta: dict) -> str:
    """manifest.json (study_name) -> meta.cjm_study -> имя каталога прогона без временной метки."""
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        try:
            with manifest_path.open("r", encoding="utf-8") as f:
                manifest = json.load(f)
            if manifest.get("study_name"):
                return str(manifest["study_name"])
        except (json.JSONDecodeError, OSError):
            pass
    if merged_meta.get("cjm_study"):
        return str(merged_meta["cjm_study"])
    return re.sub(r"_\d{8}-\d{4}$", "", run_dir.name)


def find_rtb_candidates_file(run_dir: Path, explicit: Optional[str]) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            fail(f"ОШИБКА: --rtb-candidates указывает на несуществующий файл: {p}")
        return p
    for name in RTB_CANDIDATES_FILENAMES:
        candidate = run_dir / name
        if candidate.exists():
            return candidate
    fail(
        f"ОШИБКА: не найден файл RTB-кандидатов в {run_dir} (искал: {', '.join(RTB_CANDIDATES_FILENAMES)}).\n"
        f"Стадия 4 (модель формулирует RTB-кандидаты по references/cjm_prompts_ru.md) должна "
        f"записать один из этих файлов ПЕРЕД вызовом cjm_to_study.py (см. схему в docstring этого "
        f"файла — форма А `rtb_candidates`+`segments` или форма Б `by_segment`), либо укажите путь "
        f"явно через --rtb-candidates."
    )
    raise AssertionError("unreachable")  # для типизации — fail() всегда завершает процесс


# ============================================================================
# Нормализация кандидатов
# ============================================================================


def normalize_candidates(raw_list, context: str) -> list[dict]:
    if not isinstance(raw_list, list):
        fail(f"ОШИБКА: {context} должен быть списком, получено: {type(raw_list).__name__}")
    out: list[dict] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(raw_list):
        if isinstance(item, str):
            cid, text = f"rtb{i + 1}", item
        elif isinstance(item, dict):
            cid = str(item.get("id") or f"rtb{i + 1}")
            text = item.get("text")
        else:
            fail(f"ОШИБКА: {context}[{i}] — неверный формат (ожидается строка или {{id, text}}): {item!r}")
            return []  # unreachable, для типизации
        if not text or not str(text).strip():
            fail(f"ОШИБКА: {context}[{i}] (id={cid}) не содержит непустого поля text.")
        if cid in seen_ids:
            fail(f"ОШИБКА: {context} — повторяющийся id кандидата: {cid!r}.")
        seen_ids.add(cid)
        out.append({"id": cid, "text": str(text).strip()})
    if len(out) < 2:
        fail(
            f"ОШИБКА: {context} — нужно минимум 2 RTB-кандидата, получено {len(out)} "
            f"(run_study.py откажется валидировать study.yaml с <2 стимулами, spec_synthetic-panel_v1.md §6)."
        )
    return out


# ============================================================================
# RTB-метаданные (targeted_barrier/targeted_benefit/rtb_type) — находка №3,
# MAJOR, docs/review_v1.1.md: cjm_report_template.md §6.1 требует эти три поля
# на кандидата, а схема run_study.py v1 для stimuli[] строго {id, text}
# (см. run_study.py::load_or_init_manifest, строка со списком-конструктором
# "[{'id': s['id'], 'text': s['text']} for s in study['stimuli']]" — лишние
# ключи там просто молча отбрасываются, а не сохраняются, так что класть эти
# поля прямо в stimuli[] было бы бесполезно, а не только рискованно). Поэтому
# они читаются из ИСХОДНОГО (до normalize_candidates) 04_rtb_candidates.yaml
# отдельно и переносятся в `notes` study.yaml как читаемый человеком текст —
# run_study.py это поле не парсит, значит ничего не сломает; тот, кто дальше
# вручную собирает cjm_report.md §6.1, читает их оттуда. Отсутствие полей у
# старого 04_rtb_candidates.yaml (например, пилот cjm_hairloss_demo, где эти
# три поля есть только как комментарий, не как YAML-поля) не считается
# ошибкой — cjm_report_template.md §6.1 в этом случае предписывает «—».
# ============================================================================

RTB_METADATA_FIELDS = ("targeted_barrier", "targeted_benefit", "rtb_type")


def extract_rtb_metadata(raw_list, normalized_ids: list[str]) -> list[dict]:
    """
    Параллельно normalize_candidates проходит по ТОМУ ЖЕ исходному raw_list и
    достаёт опциональные targeted_barrier/targeted_benefit/rtb_type по индексу,
    сопоставляя с уже присвоенными id из normalized_ids (голые строки-кандидаты
    не несут метаданных вовсе -> все три поля None для них).
    """
    out = []
    for i, item in enumerate(raw_list):
        cid = normalized_ids[i] if i < len(normalized_ids) else f"rtb{i + 1}"
        meta = {"id": cid}
        for field in RTB_METADATA_FIELDS:
            value = item.get(field) if isinstance(item, dict) else None
            meta[field] = str(value).strip() if value else None
        out.append(meta)
    return out


def format_rtb_metadata_note(meta_list: list[dict]) -> str:
    """Текстовый блок для notes study.yaml — см. комментарий раздела выше."""
    any_present = any(m.get(f) for m in meta_list for f in RTB_METADATA_FIELDS)
    if not any_present:
        return (
            "RTB-метаданные (targeted_barrier/targeted_benefit/rtb_type) не указаны в "
            "исходном 04_rtb_candidates.yaml этого прогона — при сборке cjm_report_template.md "
            "§6.1 подставлять «—» в столбцы «Целевой барьер»/«Целевой benefit»/«Тип RTB» "
            "(обратная совместимость со старыми прогонами, см. находку №3 review_v1.1.md)."
        )
    lines = ["RTB-метаданные для cjm_report_template.md §6.1 (справочно, не используются run_study.py):"]
    for m in meta_list:
        barrier = m.get("targeted_barrier") or "—"
        benefit = m.get("targeted_benefit") or "—"
        rtb_type = m.get("rtb_type") or "—"
        lines.append(f"  - {m['id']}: барьер «{barrier}» -> benefit «{benefit}» -> тип «{rtb_type}»")
    return "\n".join(lines)


# ============================================================================
# Валидация тестируемых сегментов против ядра/неустойчивых
# ============================================================================


def validate_test_segment_ids(ids: list[str], core_ids: set[str], unstable_ids: set[str], merged_path: Path) -> None:
    for sid in ids:
        if sid in unstable_ids and sid not in core_ids:
            fail(
                f"ОШИБКА: сегмент {sid!r} помечен как неустойчивый (1/3) в {merged_path} и не входит "
                f"в ядро — по умолчанию RTB-тест на нём не проводится (spec §2, стадия 1: «1/3» -> "
                f"в приложение с пометкой «неустойчивый»). Выберите сегмент из ядра, либо, если тест "
                f"неустойчивого сегмента осознанно нужен, экспортируйте его вручную "
                f"(scripts/segments_export.py --include-unstable) и постройте study.yaml вручную."
            )
        if sid not in core_ids:
            fail(f"ОШИБКА: сегмент {sid!r} не найден ни в ядре, ни в неустойчивых сегментах {merged_path}.")


# ============================================================================
# Построение study.yaml
# ============================================================================


def build_studies(
    rtb_data: dict,
    cjm_name: str,
    core_ids: set[str],
    unstable_ids: set[str],
    merged_path: Path,
    rtb_path: Path,
    respondents_per_segment: int,
    samples_per_respondent: int,
    question_scale: str,
) -> list[dict]:
    has_shared = "rtb_candidates" in rtb_data
    has_per_segment = "by_segment" in rtb_data
    if has_shared and has_per_segment:
        fail(
            f"ОШИБКА: {rtb_path} содержит и `rtb_candidates` (форма А, общий пул), и `by_segment` "
            f"(форма Б, посегментно) — используйте ТОЛЬКО ОДНУ форму (см. docstring cjm_to_study.py)."
        )
    if not has_shared and not has_per_segment:
        fail(
            f"ОШИБКА: {rtb_path} должен содержать либо `rtb_candidates`+`segments` (форма А), либо "
            f"`by_segment` (форма Б) — см. docstring cjm_to_study.py."
        )

    studies: list[dict] = []

    if has_shared:
        segments_list = rtb_data.get("segments")
        if not segments_list or not isinstance(segments_list, list):
            fail(
                f"ОШИБКА: {rtb_path} — форма А (`rtb_candidates`) требует также непустой список "
                f"`segments: [...]` — какие сегменты ядра тестировать этим общим набором кандидатов."
            )
        validate_test_segment_ids(segments_list, core_ids, unstable_ids, merged_path)
        candidates = normalize_candidates(rtb_data["rtb_candidates"], f"{rtb_path}:rtb_candidates")
        rtb_meta = extract_rtb_metadata(rtb_data["rtb_candidates"], [c["id"] for c in candidates])
        studies.append(
            {
                "name": f"{cjm_name}_rtb",
                "type": "claims_ranking",
                "question_scale": question_scale,
                "stimuli": candidates,
                "segments": list(segments_list),
                "respondents_per_segment": respondents_per_segment,
                "samples_per_respondent": samples_per_respondent,
                "notes": (
                    f"Автоматически сгенерировано cjm_to_study.py из {merged_path.name} + {rtb_path.name} "
                    f"(CJM-прогон {cjm_name}). RTB-кандидаты — 🟡 гипотезы для проверки панелью, "
                    f"результат встраивается в cjm_report.md (см. spec_synthetic-panel_v1.1_segment_map.md §2).\n"
                    f"{format_rtb_metadata_note(rtb_meta)}"
                ),
            }
        )
    else:
        by_segment = rtb_data.get("by_segment")
        if not by_segment or not isinstance(by_segment, dict):
            fail(f"ОШИБКА: {rtb_path} — `by_segment` должен быть непустым словарём id_сегмента -> список кандидатов.")
        validate_test_segment_ids(list(by_segment.keys()), core_ids, unstable_ids, merged_path)
        for sid, raw_candidates in by_segment.items():
            candidates = normalize_candidates(raw_candidates, f"{rtb_path}:by_segment[{sid}]")
            rtb_meta = extract_rtb_metadata(raw_candidates, [c["id"] for c in candidates])
            studies.append(
                {
                    "name": f"{cjm_name}_rtb_{sid}",
                    "type": "claims_ranking",
                    "question_scale": question_scale,
                    "stimuli": candidates,
                    "segments": [sid],
                    "respondents_per_segment": respondents_per_segment,
                    "samples_per_respondent": samples_per_respondent,
                    "notes": (
                        f"Автоматически сгенерировано cjm_to_study.py из {merged_path.name} + {rtb_path.name} "
                        f"для сегмента «{sid}» (CJM-прогон {cjm_name}, форма by_segment). RTB-кандидаты — "
                        f"🟡 гипотезы для проверки панелью (см. spec_synthetic-panel_v1.1_segment_map.md §2).\n"
                        f"{format_rtb_metadata_note(rtb_meta)}"
                    ),
                }
            )

    return studies


def write_study_yaml(study: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{study['name']}.yaml"
    header = (
        f"# study.yaml сгенерирован scripts/cjm_to_study.py — НЕ редактировать вручную,\n"
        f"# перегенерируется повторным запуском для этого CJM-прогона.\n"
    )
    body = yaml.safe_dump(study, allow_unicode=True, sort_keys=False, width=100)
    out_path.write_text(header + body, encoding="utf-8")
    return out_path


# ============================================================================
# main
# ============================================================================


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="01_segments_merged.yaml + RTB-кандидаты -> studies/*.yaml (spec §2, стадия 4)."
    )
    p.add_argument("--run", required=True, help="Каталог прогона segment_map, напр. runs/cjm_<имя>_<ts>/")
    p.add_argument(
        "--rtb-candidates",
        default=None,
        help="Явный путь к файлу RTB-кандидатов (по умолчанию ищется 04_rtb_candidates.yaml/.yml/.json в --run).",
    )
    p.add_argument("--category-slug", default=None, help="Override для segments_export (по умолчанию из имени CJM-прогона).")
    p.add_argument("--out-dir", default=None, help="Куда писать study.yaml (по умолчанию <корень скилла>/studies).")
    p.add_argument("--respondents-per-segment", type=int, default=DEFAULT_RESPONDENTS_PER_SEGMENT)
    p.add_argument("--samples-per-respondent", type=int, default=DEFAULT_SAMPLES_PER_RESPONDENT)
    p.add_argument("--question-scale", default=DEFAULT_QUESTION_SCALE, help="id шкалы из references/anchors_ru.yaml")
    p.add_argument(
        "--skip-segments-export",
        action="store_true",
        help="Не экспортировать сегменты ядра в panel/segments/ (по умолчанию экспортируются всегда).",
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

    merged_path = run_dir / "01_segments_merged.yaml"
    if not merged_path.exists():
        fail(
            f"ОШИБКА: {merged_path} не найден — сначала должна быть завершена стадия 1 "
            f"(сегментация + объединение со stability-метками, spec §2)."
        )

    meta, core_segments, unstable_segments = segments_export.load_merged_segments(merged_path)
    for seg in core_segments + unstable_segments:
        segments_export.validate_segment_record(seg, merged_path)
    core_ids = {s["id"] for s in core_segments}
    unstable_ids = {s["id"] for s in unstable_segments}

    cjm_name = resolve_cjm_name(run_dir, meta)
    rtb_path = find_rtb_candidates_file(run_dir, args.rtb_candidates)
    rtb_data = load_data_file(rtb_path)

    studies = build_studies(
        rtb_data,
        cjm_name,
        core_ids,
        unstable_ids,
        merged_path,
        rtb_path,
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
            copy_name = "04_rtb_study.yaml"
        else:
            copy_name = f"04_rtb_study_{study['segments'][0]}.yaml"
        (run_dir / copy_name).write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"-- {out_path} (копия: {run_dir / copy_name})")

    segments_result = None
    if not args.skip_segments_export:
        try:
            segments_result = segments_export.export_segments(
                merged_path,
                skill_root,
                category_slug=args.category_slug,
                include_unstable=False,
            )
        except ValueError as exc:
            fail(f"ОШИБКА при экспорте сегментов ядра: {exc}")
        for w in segments_result["warnings"]:
            print(f"ПРЕДУПРЕЖДЕНИЕ: {w}", file=sys.stderr)
        print(f"-- сегменты ядра экспортированы в {segments_result['target_dir']} ({len(segments_result['written'])} шт.)")

    try:
        cjm_init.record_stage(
            run_dir,
            "rtb_study_generated",
            {
                "rtb_candidates_path": str(rtb_path),
                "shape": "shared" if "rtb_candidates" in rtb_data else "by_segment",
                "studies": [str(p) for p in written_study_paths],
                "segments_exported": (
                    [str(p) for p in segments_result["written"]] if segments_result else []
                ),
            },
        )
    except FileNotFoundError as exc:
        print(f"ПРЕДУПРЕЖДЕНИЕ: manifest.json не обновлён ({exc})", file=sys.stderr)

    print("\nГотово. Дальше для каждого сгенерированного study.yaml:")
    for p in written_study_paths:
        print(f"  python scripts/run_study.py --study {p} --stage all")


if __name__ == "__main__":
    main()

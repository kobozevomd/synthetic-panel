#!/usr/bin/env python3
"""
cjm_init.py — scaffold-скрипт режима segment_map ("Карта сегментов / AI CJM").

Реализует spec_synthetic-panel_v1.1_segment_map.md §2 (схема входа/выхода) и
задачу [B2] п.1: валидация studies/cjm_*.yaml, создание каталога прогона,
manifest.json, печать плана стадий.

    python scripts/cjm_init.py --study studies/cjm_<имя>.yaml [--config PATH] [--run-dir DIR]

(CLI зафиксирован также в SKILL.md §4 — держать сигнатуру в синхроне с тем, что
там документировано для агента, ведущего скилл.)

Ничего в этом скрипте НЕ выполняет сами аналитические стадии 0-5 (это делает
модель, ведущая скилл, по шаблонам references/cjm_prompts_ru.md — зона [B1]/[B3]).
cjm_init.py только: (1) валидирует schema/принципы входного cjm_*.yaml,
(2) создаёт runs/<name>_<YYYYMMDD-HHMM>/ и manifest.json, (3) печатает план стадий.

=== Схема studies/cjm_*.yaml (вход, spec §2) ===

    name: cjm_hairloss_demo            # str, желательно с префиксом cjm_
    type: segment_map                  # ЕДИНСТВЕННОЕ разрешённое значение в этом скрипте
    category: "средства от выпадения волос"   # str, человекочитаемая категория
    brand: "ДемоБренд (вымышленный)"          # str, бренд/препарат (может быть демо)
    vertical: pharma_otc               # pharma_rx | pharma_otc | fmcg | auto | other
    runs_for_stability: 3              # int >= 3 (принцип §1.4 — несъёмный минимум)
    segments_target_range: [5, 12]      # [int, int], low <= high — целевой диапазон,
                                        # финальное число сегментов решает устойчивость
    data_inputs:
      drug_instruction: null           # путь к тексту инструкции (фарма) или null
      social_listening: null           # путь к CSV/тексту вербатимов или null
      category_data: null              # путь к заметке с категорийными данными или null
    rtb_candidates_per_segment: 4       # int >= 1
    test_segments: 2                    # int >= 1 — сколько устойчивых сегментов ядра
                                         # прогнать через SSR-тест RTB на стадии 4

Отсутствующий top-level ключ, `data_inputs` не-словарь, некорректные типы/диапазоны
— ОШИБКА валидации схемы (exit 1) с человекочитаемым сообщением. Если в
`data_inputs.*` указан НЕ-null путь, он должен существовать на диске (проверяется
относительно текущей директории, затем относительно каталога study.yaml, затем
относительно корня скилла) — иначе тоже ошибка: дешевле поймать опечатку в пути
сейчас, чем потом в середине аналитической стадии 0/3.

=== Отказы (человекочитаемые, exit 1) — из задания [B2] буквально ===

    1. `type` != "segment_map"                       -> REFUSAL_WRONG_TYPE
    2. `vertical: pharma_rx` без `data_inputs.drug_instruction` -> REFUSAL_RX_NO_INSTRUCTION

Дополнительно (сверх буквального задания, но напрямую из несъёмного принципа §1.4
спецификации — задокументировано здесь и в issues финального отчёта сборщика):

    3. `runs_for_stability` < 3                       -> REFUSAL_STABILITY_MINIMUM

=== manifest.json (создаётся/обновляется этим скриптом) ===

    {
      "cjm_spec_version": "1.1",
      "study_name": "...", "study_path": "...", "study_type": "segment_map",
      "category": "...", "brand": "...", "vertical": "...",
      "runs_for_stability": 3, "segments_target_range": [5, 12],
      "data_inputs": {"drug_instruction": null, "social_listening": null, "category_data": null},
      "rtb_candidates_per_segment": 4, "test_segments": 2,
      "pharma_gate": {"required": bool, "rx_warning_required": bool},
      "environment": {"python_version": "...", "platform": "..."},
      "config_snapshot": {... содержимое config.yaml ...},
      "created_at": "ISO8601 UTC",
      "stages": {}   # заполняется прогрессивно по мере прохождения стадий 0-5
                     # (агентом вручную/через Edit ЛИБО через record_stage() ниже —
                     # обе стадии равноправны, manifest.json — обычный JSON-файл)
    }

Если runs/<name>_<ts>/manifest.json уже существует (повторный вызов в ту же
минуту) — файл ЗАГРУЖАЕТСЯ и переиспользуется, а не перезаписывается с нуля
(симметрично run_study.py:load_or_init_manifest) — так повторный вызов не теряет
уже записанные "stages" предыдущего вызова того же прогона.

record_stage(run_dir, stage_name, data) — маленький импортируемый хелпер для
других скриптов моей же зоны (cjm_to_study.py), которым нужно дописать секцию в
manifest.json уже существующего прогона без дублирования load/save-логики.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

CJM_STUDY_TYPE = "segment_map"
ALLOWED_VERTICALS = ("pharma_rx", "pharma_otc", "fmcg", "auto", "other")
PHARMA_VERTICALS = ("pharma_rx", "pharma_otc")
DATA_INPUT_KEYS = ("drug_instruction", "social_listening", "category_data")
MIN_RUNS_FOR_STABILITY = 3
CJM_SPEC_VERSION = "1.1"

REQUIRED_SCALAR_FIELDS = [
    "name",
    "type",
    "category",
    "brand",
    "vertical",
    "runs_for_stability",
    "segments_target_range",
    "rtb_candidates_per_segment",
    "test_segments",
]

REFUSAL_WRONG_TYPE = """\
ОТКАЗ: {study_path} — поле `type` = {type_val!r}.

cjm_init.py поддерживает ИСКЛЮЧИТЕЛЬНО type: segment_map (режим "Карта сегментов /
AI CJM", spec_synthetic-panel_v1.1_segment_map.md §2).

Что делать:
  - Если вам нужен режим segment_map (AI CJM) — исправьте поле `type` на `segment_map`.
  - Если вам нужен один из типов v1 (claims_ranking, concept_screening,
    segment_reactions, audience_probe) — используйте штатный scripts/run_study.py,
    а НЕ cjm_init.py (см. SKILL.md, раздел «Поток работы»).
"""

REFUSAL_RX_NO_INSTRUCTION = """\
ОТКАЗ: {study_path} — vertical: pharma_rx требует заполненного
data_inputs.drug_instruction (путь к официальной инструкции препарата), сейчас: null.

Почему: фарма-гейт (принцип §1.6 спецификации v1.1) для Rx-препаратов обязан
опираться на РЕАЛЬНУЮ инструкцию — «обобщённая демо-инструкция категории»
допустима только для vertical: pharma_otc (см. DoD §5, п.2). Кроме того,
коммуникация по рецептурным (Rx) препаратам ограничена ФЗ-38 ст. 24 (реклама
рецептурных препаратов — только для специалистов здравоохранения; пациентские
коммуникационные тесты для Rx структурно ограничены), и этот прогон должен нести
предупреждение об этом в итоговом отчёте (§1.6) — без реальной инструкции фильтр
аудиторий (стадия 0) невозможно провести добросовестно.

Что делать:
  - Укажите путь к тексту инструкции в data_inputs.drug_instruction, ЛИБО
  - Если препарат безрецептурный (OTC) — исправьте vertical на pharma_otc (тогда
    допустима обобщённая инструкция категории с явной пометкой «демо»).
"""

REFUSAL_STABILITY_MINIMUM = """\
ОТКАЗ: {study_path} — runs_for_stability = {value} (< {minimum}).

Принцип §1.4 спецификации v1.1 (несъёмный): устойчивость сегментации проверяется
МИНИМУМ {minimum} независимыми прогонами; в ядро попадают сегменты, воспроизведённые
в ≥2 прогонах. Меньше {minimum} прогонов не позволяет отличить устойчивый сегмент от
случайного артефакта одного прогона — даже в демо/пилоте это правило не
ослабляется (допустимо только приближение НЕЗАВИСИМОСТИ контекстов, не снижение
числа прогонов, см. DoD §5, п.2 и SKILL.md §4).

Что делать: увеличьте runs_for_stability минимум до {minimum}.
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(1)


# ============================================================================
# Валидация
# ============================================================================


def validate_schema(study: dict, study_path: Path) -> None:
    """Базовая валидация схемы (обязательные поля, типы). exit 1 при нарушении."""
    missing = [f for f in REQUIRED_SCALAR_FIELDS if study.get(f) in (None, "")]
    if missing:
        fail(
            f"ОШИБКА: {study_path} — отсутствуют/пусты обязательные поля: {missing} "
            f"(схема — spec_synthetic-panel_v1.1_segment_map.md §2)."
        )

    if "data_inputs" not in study or not isinstance(study.get("data_inputs"), dict):
        fail(
            f"ОШИБКА: {study_path} — поле `data_inputs` отсутствует или не является "
            f"словарём. Ожидается словарь с ключами {DATA_INPUT_KEYS} (значения — путь "
            f"или null), см. §2."
        )
    for k in DATA_INPUT_KEYS:
        study["data_inputs"].setdefault(k, None)

    try:
        study["runs_for_stability"] = int(study["runs_for_stability"])
    except (TypeError, ValueError):
        fail(f"ОШИБКА: {study_path} — runs_for_stability должен быть целым числом, получено: {study.get('runs_for_stability')!r}")

    try:
        study["rtb_candidates_per_segment"] = int(study["rtb_candidates_per_segment"])
        study["test_segments"] = int(study["test_segments"])
    except (TypeError, ValueError):
        fail(
            f"ОШИБКА: {study_path} — rtb_candidates_per_segment/test_segments должны быть "
            f"целыми числами, получено: {study.get('rtb_candidates_per_segment')!r}/"
            f"{study.get('test_segments')!r}"
        )
    if study["rtb_candidates_per_segment"] < 1 or study["test_segments"] < 1:
        fail(f"ОШИБКА: {study_path} — rtb_candidates_per_segment и test_segments должны быть >= 1.")

    rng = study.get("segments_target_range")
    if (
        not isinstance(rng, list)
        or len(rng) != 2
        or not all(isinstance(x, int) for x in rng)
        or rng[0] > rng[1]
        or rng[0] < 1
    ):
        fail(
            f"ОШИБКА: {study_path} — segments_target_range должен быть списком [low, high] "
            f"целых чисел с low <= high и low >= 1, получено: {rng!r} (spec §2: рекомендуемый "
            f"дефолт [5, 12], п. §1.3)."
        )


def validate_type(study: dict, study_path: Path) -> None:
    if study.get("type") != CJM_STUDY_TYPE:
        fail(REFUSAL_WRONG_TYPE.format(study_path=study_path, type_val=study.get("type")))


def validate_vertical(study: dict, study_path: Path) -> None:
    if study.get("vertical") not in ALLOWED_VERTICALS:
        fail(
            f"ОШИБКА: {study_path} — vertical = {study.get('vertical')!r} не входит в "
            f"допустимые значения {ALLOWED_VERTICALS} (spec §2)."
        )


def validate_pharma_gate(study: dict, study_path: Path) -> None:
    if study["vertical"] == "pharma_rx" and not study["data_inputs"].get("drug_instruction"):
        fail(REFUSAL_RX_NO_INSTRUCTION.format(study_path=study_path))


def validate_stability_minimum(study: dict, study_path: Path) -> None:
    if study["runs_for_stability"] < MIN_RUNS_FOR_STABILITY:
        fail(
            REFUSAL_STABILITY_MINIMUM.format(
                study_path=study_path, value=study["runs_for_stability"], minimum=MIN_RUNS_FOR_STABILITY
            )
        )


def _resolve_existing_path(candidate: str, study_path: Path, skill_root: Path) -> Optional[Path]:
    """Пробует candidate как есть, затем относительно папки study.yaml, затем корня скилла."""
    for base in (Path.cwd(), study_path.resolve().parent, skill_root):
        p = Path(candidate)
        resolved = p if p.is_absolute() else (base / p)
        if resolved.exists():
            return resolved
    return None


def validate_data_input_paths(study: dict, study_path: Path, skill_root: Path) -> None:
    for key in DATA_INPUT_KEYS:
        value = study["data_inputs"].get(key)
        if not value:
            continue
        if _resolve_existing_path(str(value), study_path, skill_root) is None:
            fail(
                f"ОШИБКА: {study_path} — data_inputs.{key} = {value!r} не найден на диске "
                f"(проверено относительно текущей директории, {study_path.parent}, {skill_root})."
            )


# ============================================================================
# Каталог прогона + manifest
# ============================================================================


def build_run_dir(skill_root: Path, study_name: str, run_dir_arg: Optional[str]) -> Path:
    if run_dir_arg:
        rd = Path(run_dir_arg)
        return rd if rd.is_absolute() else (Path.cwd() / rd)
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    return skill_root / "runs" / f"{study_name}_{ts}"


def load_or_init_manifest(run_dir: Path, study: dict, config: dict, study_path: Path) -> dict:
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "cjm_spec_version": CJM_SPEC_VERSION,
        "study_name": study["name"],
        "study_path": str(study_path),
        "study_type": study["type"],
        "category": study["category"],
        "brand": study["brand"],
        "vertical": study["vertical"],
        "runs_for_stability": study["runs_for_stability"],
        "segments_target_range": study["segments_target_range"],
        "data_inputs": study["data_inputs"],
        "rtb_candidates_per_segment": study["rtb_candidates_per_segment"],
        "test_segments": study["test_segments"],
        "pharma_gate": {
            "required": study["vertical"] in PHARMA_VERTICALS,
            "rx_warning_required": study["vertical"] == "pharma_rx",
        },
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        },
        "config_snapshot": config,
        "created_at": now_iso(),
        "stages": {},
    }


def save_manifest(run_dir: Path, manifest: dict) -> None:
    manifest_path = run_dir / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")


def record_stage(run_dir: Path, stage_name: str, data: dict) -> dict:
    """
    Импортируемый хелпер: дописывает manifest["stages"][stage_name] = {**data,
    "completed_at": now} в уже существующий runs/<...>/manifest.json и сохраняет.
    Используется cjm_to_study.py (та же зона сборки) — не дублирует load/save.
    Бросает FileNotFoundError, если manifest.json ещё не создан (сначала нужно
    пройти cjm_init.py).
    """
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"{manifest_path} не найден — сначала выполните "
            f"`python scripts/cjm_init.py --study <cjm_*.yaml>` для этого прогона."
        )
    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)
    manifest.setdefault("stages", {})[stage_name] = {**data, "completed_at": now_iso()}
    save_manifest(run_dir, manifest)
    return manifest


# ============================================================================
# План стадий
# ============================================================================


def build_stage_plan_lines(study: dict, run_dir: Path) -> list[str]:
    vertical = study["vertical"]
    is_pharma = vertical in PHARMA_VERTICALS
    lo, hi = study["segments_target_range"]

    lines = [
        f"Пайплайн segment_map: {study['name']} (vertical={vertical}, категория: {study['category']})",
        f"Каталог прогона: {run_dir}",
        "",
        "Стадии:",
    ]
    if is_pharma:
        rx_note = " + предупреждение о ФЗ-38 ст.24 в отчёте (Rx)" if vertical == "pharma_rx" else ""
        lines.append(
            f"  0. Фарма-гейт [ОБЯЗАТЕЛЬНА, гейт для всех следующих стадий]{rx_note}"
            f" -> 00_filter.md, 00_filter.yaml"
        )
    else:
        lines.append(f"  0. Фарма-гейт — пропускается (vertical={vertical}, не pharma_*)")
    lines.append(
        f"  1. Сегментация — {study['runs_for_stability']} независимых прогона(ов), "
        f"целевой диапазон {lo}-{hi} сегментов (устойчивость — результат, не вход) "
        f"-> 01_segmentation_run1..{study['runs_for_stability']}.md, 01_segments_merged.yaml"
    )
    lines.append(
        "  2. CJM для сегментов ядра (ранние/поздние маркеры -> симптомы -> диагноз; "
        "осознание/поиск решения/процесс лечения) -> 02_cjm_<segment_id>.md"
    )
    data_present = [k for k in DATA_INPUT_KEYS if study["data_inputs"].get(k)]
    data_note = ", ".join(data_present) if data_present else "нет входных данных — все поля 🔴 «нет данных — оценка»"
    lines.append(f"  3. Данные ({data_note}) -> 03_data_merge.md")
    lines.append(
        f"  4. Тест RTB — {study['test_segments']} сегмент(ов) ядра x "
        f"{study['rtb_candidates_per_segment']} RTB-кандидат(ов) -> "
        f"scripts/cjm_to_study.py --run {run_dir} -> studies/*.yaml -> "
        f"scripts/run_study.py --stage all -> рейтинг с разделимостью в cjm_report.md"
    )
    lines.append("  5. (опционально) Медиазадачи — не входит в стандартный прогон, если не запрошен отдельно")
    lines.append("")
    lines.append(
        "Финал: cjm_report.md (карта доверия 🟢/🟡/🔴) -> ОБЯЗАТЕЛЬНО прогнать "
        f"`python scripts/cjm_lint.py --report {run_dir / 'cjm_report.md'}` перед сдачей клиенту "
        "(exit 0 — прогон завершён, exit 1 — список нарушений)."
    )
    return lines


# ============================================================================
# main
# ============================================================================


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Scaffold прогона режима segment_map (spec_synthetic-panel_v1.1_segment_map.md §2)."
    )
    p.add_argument("--study", required=True, help="Путь к studies/cjm_<имя>.yaml")
    p.add_argument("--config", default=None, help="Путь к config.yaml (по умолчанию <корень скилла>/config.yaml)")
    p.add_argument(
        "--run-dir",
        default=None,
        help="Явно указать директорию прогона (по умолчанию runs/<name>_<YYYYMMDD-HHMM>/).",
    )
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    skill_root = Path(__file__).resolve().parent.parent

    config_path = Path(args.config) if args.config else skill_root / "config.yaml"
    if not config_path.exists():
        fail(f"ОШИБКА: config.yaml не найден: {config_path}")
    config = load_yaml(config_path)

    study_path = Path(args.study)
    if not study_path.exists():
        alt = skill_root / args.study
        if alt.exists():
            study_path = alt
        else:
            fail(f"ОШИБКА: study.yaml не найден: {args.study}")
    study = load_yaml(study_path)

    validate_schema(study, study_path)
    validate_type(study, study_path)
    validate_vertical(study, study_path)
    validate_pharma_gate(study, study_path)
    validate_stability_minimum(study, study_path)
    validate_data_input_paths(study, study_path, skill_root)

    run_dir = build_run_dir(skill_root, study["name"], args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_or_init_manifest(run_dir, study, config, study_path)
    save_manifest(run_dir, manifest)

    print(f"-- OK: {study_path} прошёл валидацию schema/принципов segment_map.")
    print(f"-- manifest.json: {run_dir / 'manifest.json'}")
    print()
    print("\n".join(build_stage_plan_lines(study, run_dir)))


if __name__ == "__main__":
    main()

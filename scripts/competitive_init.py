#!/usr/bin/env python3
"""
competitive_init.py — scaffold-скрипт режима competitive_positioning
("Конкурентная отстройка", SKILL.md режим 6, spec_synthetic-panel_v1.2.md §Модуль 3,
задание [B2] п.3).

    python scripts/competitive_init.py --study studies/comp_<имя>.yaml [--config PATH] [--run-dir DIR]

Аналогично cjm_init.py (режим segment_map, v1.1) — валидирует schema/принципы
входного comp_<имя>.yaml, создаёт runs/<name>_<YYYYMMDD-HHMM>/ + manifest.json,
печатает план стадий C0-C4. Сами аналитические стадии выполняет модель, ведущая
прогон, по шаблонам references/competitive_prompts_ru.md (зона [B3]) — этот
скрипт НИЧЕГО из содержательной аналитики не делает.

=== Схема studies/comp_<имя>.yaml (вход) ===

Источник схемы — spec_synthetic-panel_v1.2.md §Модуль 3 п.1 буквально, СВЕРЕНО
с references/competitive_prompts_ru.md (уже существовал на момент написания
этого файла — см. многочисленные примеры `comp_<имя>.yaml: <поле>` по всему
этому промпт-файлу) и references/competitive_report_template.md:

    name: comp_azelik_demo             # str
    type: competitive_positioning       # ЕДИНСТВЕННОЕ разрешённое значение
    category: "средства от акне"        # str
    our_brand: "Азелик"                 # str
    competitors: [Скинорен, ...]        # list[str], 3-6 элементов (spec §Модуль 3 п.1)
    segments: [vzrosloe_akne_..., ...]  # list[str], id из panel/segments/** — ВСЕ сегменты,
                                        # охваченные качественно (C1 восприятие, C3 switch-барьеры)
    territories_hint: [...]/"..."        # опционально — подсказка для C2 (str или list[str])
    messages_per_territory: 4            # int >= 1 — ориентир по количеству сообщений C4
    test_segments: [vzrosloe_akne_...]   # list[str] — ПОДМНОЖЕСТВО segments, тестируемое в C4
                                          # (см. references/competitive_prompts_ru.md, C4.3:
                                          # "segments: [...] # id из comp_<имя>.yaml: test_segments" —
                                          # это ЯВНО список id, а НЕ количество; в отличие от
                                          # одноимённого по названию, но ИНАЧЕ типизированного
                                          # `test_segments: int` в studies/cjm_*.yaml режима
                                          # segment_map — не путать эти два поля между режимами)
    data_inputs:
      brand_cards: null                  # опц. путь к карточкам брендов (человеком заполненным)
      social_listening: null             # опц. путь к соцлистенингу/категорийным данным

Отсутствующий top-level ключ, некорректные типы/диапазоны — ОШИБКА валидации
схемы (exit 1) с человекочитаемым сообщением (см. REFUSAL_*/fail() ниже).

=== Отказы (человекочитаемые, exit 1) ===

    1. отсутствуют/пусты обязательные скалярные поля            -> общая ошибка схемы
    2. `type` != "competitive_positioning"                        -> REFUSAL_WRONG_TYPE
    3. `competitors` не список из 3-6 непустых строк              -> REFUSAL_COMPETITORS_RANGE
    4. `segments` пуст, не список, или содержит id не из
       panel/segments/** (рекурсивно, конфликт — тоже отказ)      -> REFUSAL_UNKNOWN_SEGMENTS
    5. `test_segments` пуст, не список, или содержит id, которого
       нет в `segments` (не подмножество)                         -> REFUSAL_TEST_SEGMENTS_NOT_SUBSET
    6. `messages_per_territory` не целое >= 1                     -> общая ошибка схемы
    7. `data_inputs` не словарь, либо непустой путь не существует
       на диске                                                    -> общая ошибка схемы

Намеренно НЕ включено (в отличие от cjm_init.py): поле `vertical`/фарма-гейт —
spec_synthetic-panel_v1.2.md §Модуль 3 п.1 не описывает его для этого режима
буквально, и ни references/competitive_prompts_ru.md, ни
references/competitive_report_template.md не ссылаются на `vertical` ни разу
(проверено). Если в будущей итерации потребуется фарма-гейт и для конкурентного
режима (Rx-бренды) — это расширение схемы, не восстановление недосмотра здесь.

=== manifest.json (создаётся/обновляется этим скриптом) ===

    {
      "comp_spec_version": "1.2",
      "study_name": "...", "study_path": "...", "study_type": "competitive_positioning",
      "category": "...", "our_brand": "...", "competitors": [...],
      "segments": [...], "test_segments": [...], "territories_hint": ... | null,
      "messages_per_territory": 4,
      "data_inputs": {"brand_cards": null, "social_listening": null},
      "environment": {"python_version": "...", "platform": "..."},
      "config_snapshot": {... содержимое config.yaml ...},
      "created_at": "ISO8601 UTC",
      "stages": {}   # заполняется прогрессивно (агентом вручную ЛИБО через
                     # cjm_init.record_stage() — переиспользуем тот же generic
                     # хелпер, что и cjm_to_study.py/comp_to_study.py: он не
                     # завязан на cjm-специфичные поля, просто дописывает
                     # manifest["stages"][name] в уже существующий manifest.json)
    }

=== Имена файлов стадий C0-C4 (для плана; САМИ файлы создаёт модель,
    ведущая прогон, по references/competitive_prompts_ru.md — этот скрипт их
    не пишет, только печатает план, аналогично cjm_init.py) ===

    C0. 00_brand_knowledge.md + .yaml   (обязательна)
    C1. 01_perception_<segment_id>.md   (по одному на каждый id из `segments`)
    C2. 02_territory_map.md + .yaml
    C3. 03_switch_barriers.md
    C4. 04_messages.yaml -> scripts/comp_to_study.py --run <run_dir> ->
        studies/*.yaml -> scripts/run_study.py --study ... --stage all

Финал — comp_report.md по references/competitive_report_template.md, ОБЯЗАТЕЛЬНО
`python scripts/cjm_lint.py --report <run_dir>/comp_report.md` перед сдачей
(те же правила 1-4 + конкурентная красная зона, см. cjm_lint.py).

Юнит-тесты: scripts/test_competitive_init.py (happy path + минимум 3 отказа,
задание [B2] п.5).
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

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import targeting_export  # noqa: E402 — переиспользуем build_segment_index/resolve_segment_yaml
# (тот же локальный лёгкий индекс panel/segments/**, что и в targeting_export.py —
# см. докстринг ТОГО файла за обоснованием, почему не run_study.py напрямую;
# здесь дополнительно ещё и избегаем ТРЕТЬЕГО дублирования той же логики).

COMP_STUDY_TYPE = "competitive_positioning"
COMP_SPEC_VERSION = "1.2"
DATA_INPUT_KEYS = ("brand_cards", "social_listening")
MIN_COMPETITORS = 3
MAX_COMPETITORS = 6

REQUIRED_SCALAR_FIELDS = ["name", "type", "category", "our_brand", "messages_per_territory"]

REFUSAL_WRONG_TYPE = """\
ОТКАЗ: {study_path} — поле `type` = {type_val!r}.

competitive_init.py поддерживает ИСКЛЮЧИТЕЛЬНО type: competitive_positioning
(режим "Конкурентная отстройка", SKILL.md режим 6, spec_synthetic-panel_v1.2.md §Модуль 3).

Что делать:
  - Если вам нужен этот режим — исправьте поле `type` на `competitive_positioning`.
  - Если вам нужен режим segment_map (AI CJM) — используйте scripts/cjm_init.py.
  - Если вам нужен один из типов v1 (claims_ranking, concept_screening,
    segment_reactions, audience_probe) — используйте штатный scripts/run_study.py.
"""

REFUSAL_COMPETITORS_RANGE = """\
ОТКАЗ: {study_path} — поле `competitors` содержит {n} элемент(ов): {competitors!r}.

Принцип spec_synthetic-panel_v1.2.md §Модуль 3 п.1 (несъёмный): список
конкурентов — от {min_c} до {max_c} брендов. Меньше {min_c} — недостаточно
для содержательной карты территорий (C2) и switch-барьеров (C3); больше
{max_c} — распыляет глубину анализа C0 (проба знания каждого бренда должна
быть добросовестной, не поверхностной по всем сразу).

Что делать: приведите `competitors` к списку из {min_c}-{max_c} непустых строк.
"""

REFUSAL_UNKNOWN_SEGMENTS = """\
ОТКАЗ: {study_path} — `segments` содержит id, не найденные в panel/segments/**:
{missing}

Искал рекурсивно в {segments_root}. Конкурентная отстройка НЕ создаёт сегменты
заново (в отличие от режима segment_map) — она берёт УЖЕ СУЩЕСТВУЮЩИЕ сегменты
панели (собранные вручную или полученные прогоном segment_map, см.
references/competitive_prompts_ru.md, преамбула).

Что делать:
  - Проверьте опечатки в id, либо
  - Сначала создайте сегмент (вручную в panel/segments/<slug>/<id>.yaml, либо
    через прогон segment_map + scripts/cjm_to_study.py), затем вернитесь сюда.
{conflicts}"""

REFUSAL_TEST_SEGMENTS_NOT_SUBSET = """\
ОТКАЗ: {study_path} — `test_segments` содержит id, отсутствующие в `segments`:
{extra}

`test_segments` (список сегментов, которые реально тестируются SSR-тестом
сообщений на стадии C4) ДОЛЖЕН быть подмножеством `segments` (список сегментов,
охваченных качественным анализом C0-C3) — нельзя SSR-тестировать сегмент, для
которого не построено восприятие (C1) и switch-барьеры (C3).

Что делать: либо добавьте отсутствующие id в `segments`, либо уберите их из
`test_segments`.
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
            f"(схема — spec_synthetic-panel_v1.2.md §Модуль 3 п.1)."
        )

    if "data_inputs" not in study or not isinstance(study.get("data_inputs"), dict):
        fail(
            f"ОШИБКА: {study_path} — поле `data_inputs` отсутствует или не является словарём. "
            f"Ожидается словарь с ключами {DATA_INPUT_KEYS} (значения — путь или null)."
        )
    for k in DATA_INPUT_KEYS:
        study["data_inputs"].setdefault(k, None)

    try:
        study["messages_per_territory"] = int(study["messages_per_territory"])
    except (TypeError, ValueError):
        fail(
            f"ОШИБКА: {study_path} — messages_per_territory должен быть целым числом, "
            f"получено: {study.get('messages_per_territory')!r}"
        )
    if study["messages_per_territory"] < 1:
        fail(f"ОШИБКА: {study_path} — messages_per_territory должен быть >= 1.")

    competitors = study.get("competitors")
    if not isinstance(competitors, list) or not all(isinstance(c, str) and c.strip() for c in competitors):
        fail(
            f"ОШИБКА: {study_path} — `competitors` должен быть списком непустых строк, "
            f"получено: {competitors!r}"
        )

    segments = study.get("segments")
    if not isinstance(segments, list) or not segments or not all(isinstance(s, str) and s.strip() for s in segments):
        fail(
            f"ОШИБКА: {study_path} — `segments` должен быть непустым списком непустых строк (id "
            f"из panel/segments/**), получено: {segments!r}"
        )

    test_segments = study.get("test_segments")
    if (
        not isinstance(test_segments, list)
        or not test_segments
        or not all(isinstance(s, str) and s.strip() for s in test_segments)
    ):
        fail(
            f"ОШИБКА: {study_path} — `test_segments` должен быть непустым списком непустых строк "
            f"(подмножество `segments`), получено: {test_segments!r}"
        )

    hint = study.get("territories_hint")
    if hint is not None and not isinstance(hint, (str, list)):
        fail(f"ОШИБКА: {study_path} — territories_hint должен быть строкой, списком строк или отсутствовать.")
    if isinstance(hint, list) and not all(isinstance(h, str) for h in hint):
        fail(f"ОШИБКА: {study_path} — territories_hint как список должен содержать только строки.")


def validate_type(study: dict, study_path: Path) -> None:
    if study.get("type") != COMP_STUDY_TYPE:
        fail(REFUSAL_WRONG_TYPE.format(study_path=study_path, type_val=study.get("type")))


def validate_competitors_range(study: dict, study_path: Path) -> None:
    competitors = study["competitors"]
    if not (MIN_COMPETITORS <= len(competitors) <= MAX_COMPETITORS):
        fail(
            REFUSAL_COMPETITORS_RANGE.format(
                study_path=study_path,
                n=len(competitors),
                competitors=competitors,
                min_c=MIN_COMPETITORS,
                max_c=MAX_COMPETITORS,
            )
        )


def validate_segments_exist(study: dict, study_path: Path, segments_root: Path) -> None:
    index = targeting_export.build_segment_index(segments_root)
    missing_lines = []
    conflict_lines = []
    for sid in study["segments"]:
        candidates = index.get(sid, [])
        if not candidates:
            missing_lines.append(f"  - {sid}")
        elif len(candidates) > 1:
            listed = "; ".join(str(p) for p in candidates)
            conflict_lines.append(f"  - {sid}: конфликт, найден в нескольких местах: {listed}")
    if missing_lines or conflict_lines:
        conflicts_text = ("\nКонфликты id (найдены более чем в одном месте):\n" + "\n".join(conflict_lines) + "\n") if conflict_lines else ""
        fail(
            REFUSAL_UNKNOWN_SEGMENTS.format(
                study_path=study_path,
                missing="\n".join(missing_lines) if missing_lines else "  (нет — только конфликты, см. ниже)",
                segments_root=segments_root,
                conflicts=conflicts_text,
            )
        )


def validate_test_segments_subset(study: dict, study_path: Path) -> None:
    segments_set = set(study["segments"])
    extra = [s for s in study["test_segments"] if s not in segments_set]
    if extra:
        fail(REFUSAL_TEST_SEGMENTS_NOT_SUBSET.format(study_path=study_path, extra=extra))


def _resolve_existing_path(candidate: str, study_path: Path, skill_root: Path) -> Optional[Path]:
    """Пробует candidate как есть, затем относительно папки study.yaml, затем корня скилла
    (та же логика, что cjm_init.py::_resolve_existing_path — независимая копия: два разных
    scaffold-скрипта режимов, минимальная функция, не стоит городить общий импорт ради неё)."""
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
        "comp_spec_version": COMP_SPEC_VERSION,
        "study_name": study["name"],
        "study_path": str(study_path),
        "study_type": study["type"],
        "category": study["category"],
        "our_brand": study["our_brand"],
        "competitors": study["competitors"],
        "segments": study["segments"],
        "test_segments": study["test_segments"],
        "territories_hint": study.get("territories_hint"),
        "messages_per_territory": study["messages_per_territory"],
        "data_inputs": study["data_inputs"],
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


# ============================================================================
# План стадий
# ============================================================================


def build_stage_plan_lines(study: dict, run_dir: Path) -> list[str]:
    lines = [
        f"Пайплайн competitive_positioning: {study['name']} "
        f"(наш бренд: {study['our_brand']}, категория: {study['category']})",
        f"Конкуренты ({len(study['competitors'])}): {', '.join(study['competitors'])}",
        f"Сегменты в анализе ({len(study['segments'])}): {', '.join(study['segments'])}",
        f"Тестируемые в C4 ({len(study['test_segments'])}): {', '.join(study['test_segments'])}",
        f"Каталог прогона: {run_dir}",
        "",
        "Стадии:",
        "  C0. Проба знания брендов [ОБЯЗАТЕЛЬНА] — по каждому бренду фиксируем, что реально "
        "знаем и где пробелы; вердикт «знание достаточно»/«нужна карточка» (без карточки — "
        "бренд 🔴, «низкая надёжность» дальше по C1-C3) -> 00_brand_knowledge.md, "
        "00_brand_knowledge.yaml",
        "  C1. Восприятие по сегментам (качественно, голосом сегмента) -> "
        + ", ".join(f"01_perception_{sid}.md" for sid in study["segments"]),
        "  C2. Карта территорий позиционирования (ВСЕГДА 🟡, требует валидации соцлистенингом/"
        "категорийными данными) -> 02_territory_map.md, 02_territory_map.yaml",
        "  C3. Switch-барьеры (наш <-> каждый конкурент, качественно, включая обратный риск "
        "оттока своих) -> 03_switch_barriers.md",
        f"  C4. Отстроечные сообщения (messages_per_territory={study['messages_per_territory']}, "
        f"ориентир) + SSR-тест -> 04_messages.yaml -> "
        f"scripts/comp_to_study.py --run {run_dir} -> studies/*.yaml -> "
        f"scripts/run_study.py --study <сгенерированный> --stage all",
        "",
        "Финал: comp_report.md (по references/competitive_report_template.md, карта доверия "
        "🟢/🟡/🔴) -> ОБЯЗАТЕЛЬНО прогнать "
        f"`python scripts/cjm_lint.py --report {run_dir / 'comp_report.md'}` перед сдачей "
        "клиенту (правила 1-4 + конкурентная красная зона — exit 0 обязателен).",
    ]
    return lines


# ============================================================================
# main
# ============================================================================


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Scaffold прогона режима competitive_positioning (spec_synthetic-panel_v1.2.md §Модуль 3)."
    )
    p.add_argument("--study", required=True, help="Путь к studies/comp_<имя>.yaml")
    p.add_argument("--config", default=None, help="Путь к config.yaml (по умолчанию <корень скилла>/config.yaml)")
    p.add_argument("--segments-root", default=None, help="По умолчанию — <корень скилла>/panel/segments")
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
    validate_competitors_range(study, study_path)
    segments_root = Path(args.segments_root) if args.segments_root else (skill_root / "panel" / "segments")
    validate_segments_exist(study, study_path, segments_root)
    validate_test_segments_subset(study, study_path)
    validate_data_input_paths(study, study_path, skill_root)

    run_dir = build_run_dir(skill_root, study["name"], args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_or_init_manifest(run_dir, study, config, study_path)
    save_manifest(run_dir, manifest)

    print(f"-- OK: {study_path} прошёл валидацию schema/принципов competitive_positioning.")
    print(f"-- manifest.json: {run_dir / 'manifest.json'}")
    print()
    print("\n".join(build_stage_plan_lines(study, run_dir)))


if __name__ == "__main__":
    main()

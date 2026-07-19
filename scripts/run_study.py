#!/usr/bin/env python3
"""
run_study.py — CLI-оркестратор стадий synthetic-panel (spec_synthetic-panel_v1.md §6,
расширено spec_synthetic-panel_v1.3.md §1.4 "негативные контроли" и §1.5 "режим/модель").

    python scripts/run_study.py --study studies/<name>.yaml --stage all|generate|score|report [--run-dir DIR]

Стадии пишут в runs/<study_name>_<YYYYMMDD-HHMM>/ (или в --run-dir, если указан):
    responses_todo.jsonl, AGENT_TASK.md   — только agent-режим, стадия generate
    responses.jsonl                       — все режимы, стадия generate
    pmf_by_respondent.csv, pmf_by_segment.csv — стадия score
    pmf_by_sample.csv                     — НОВОЕ в v1.3, стадия score (гранулярность
                                             "1 строка = 1 ответ", до усреднения по
                                             сэмплам — нужно report.py для §1.3.2/1.3.4)
    report.md                             — стадия report
    manifest.json                         — обновляется на каждой стадии; ВСЕГДА (к
                                             концу --stage report) содержит верхнеуровневые
                                             mode/model/embedding_model/anchors_version/
                                             controls/controls_verdict (§1.5, см.
                                             compute_run_mode/run_report_stage)

Валидация study.yaml: `type` должен быть в ALLOWED_STUDY_TYPES, иначе — отказ с
объяснением про красную зону метода (см. validate_study_type).

Agent-режим (llm.provider == agent, дефолт config.yaml): стадия generate пишет
responses_todo.jsonl + AGENT_TASK.md и НЕ вызывает никакую LLM (см. generate.py).
`--stage all`/`--stage generate` в этом случае останавливается с кодом выхода 2 и
инструкцией, что делать дальше — заполнить responses.jsonl по AGENT_TASK.md и
продолжить `--stage score --run-dir <тот же run_dir>`. Если responses.jsonl уже
заполнен (агент вернулся в ту же run_dir) — повторный вызов generate не
перезаписывает его поверх, а пропускает генерацию todo (см. run_generate_stage).

Самоидентификация модели в agent-режиме (§1.5, фикс Д4): флаг `--agent-model
"<имя/версия модели этой сессии>"` — модель, ВЕДУЩАЯ скилл (агент, вызывающий этот
CLI), сама указывает, кто она; сохраняется в manifest.json как
`agent_self_report: {model, self_reported: true}`. Это САМОДЕКЛАРАЦИЯ (не
API-подтверждение) — помечается как таковая везде, где показывается (report.md,
manifest.json), в отличие от `model`, приходящей от api-провайдеров (anthropic/
openai/gigachat), которая — реальный dated-id из ответа API.

Загрузчик сегментов (см. build_segment_index/resolve_segment_path/load_segments,
добавлено в v1.1 для режима segment_map — spec_synthetic-panel_v1.1_segment_map.md
§3): panel/segments/**/*.yaml индексируется РЕКУРСИВНО по имени файла (stem), а
не только по плоскому panel/segments/{sid}.yaml — так работают и старые 7
плоских кофейных сегментов, и новые вложенные по категориям
panel/segments/<category_slug>/<id>.yaml (см. scripts/segments_export.py).
Совпадение stem в РАЗНЫХ папках дерева — явная ошибка (exit 1), не молчаливый
выбор первого найденного пути.

Негативные контроли (§1.4, фикс Д5) — см. докстринг раздела "Негативные контроли"
ниже для полного контракта (плацебо/пара-ловушка/слепые id, схема manifest.json:
controls, обратная совместимость study.yaml: controls: off).

ВИЗУАЛЬНЫЕ СТИМУЛЫ И ПРОБА ЗРЕНИЯ (spec_synthetic-panel_v1.4.md §1.1-1.3, Модуль 1)
— полный контракт в докстрингах соответствующих разделов ниже; сводка для B3/F1:
    - study.yaml: stimuli[].image (путь PNG/JPG/JPEG, опционально) + stimuli[].label
      (короткая подпись — ОБЯЗАТЕЛЬНА для image-only, т.е. без непустого text) +
      stimuli[].key_element (опционально — ключевой различающий элемент варианта
      для пробы зрения). См. validate_and_resolve_stimuli (валидация + резолв пути
      в абсолютный, мутация IN PLACE) и check_image_parallelism (предупреждение,
      не блок).
    - manifest.json: НОВЫЕ верхнеуровневые поля `stimulus_kind` ("text"|"image"|
      "mixed", вычисляется ОДИН раз при первой инициализации run_dir — см.
      load_or_init_manifest) и `image_parallelism_warning` (str|null); `vision_check`
      (см. compute_vision_verdicts) — появляется только для визуальных study,
      ОБНОВЛЯЕТСЯ на каждой стадии generate/score/report (В ОТЛИЧИЕ от `controls`,
      не "замораживается" — вердикт пробы зрения не завязан на seed/случайность,
      пересчёт из 00_vision_check.yaml безопасен, см. resolve_vision_verdict).
    - Новый артефакт run_dir/00_vision_check.yaml (+ human-readable 00_vision_check.md)
      — стадия ПЕРЕД генерацией персональных ответов (см. докстринг раздела "Проба
      зрения" ниже за полной схемой/механикой стоп-условий).
    - report_template.md ([B3], v1.4) — 5 новых плейсхолдеров (STIMULUS_KIND_LINE/
      STIMULUS_KIND/VISION_CHECK_SECTION/VISION_CHECK_STATUS_LINE/
      VISION_CHECK_FAILED_BANNER), заполняются ЦЕЛИКОМ этим модулем через
      header_mapping в run_report_stage (см. compute_stimulus_kind_line/
      compute_vision_check_section/compute_vision_check_status_line/
      compute_vision_check_failed_banner) — report.py сам их не вычисляет.
    - §2.2 (находка №3 review_v1.3.md, контрастные плацебо): pick_placebo/
      build_controls_manifest — kind выбранного плацебо ("neutral"|"irrelevant"|
      "empty_promise", references/placebo_bank_ru.yaml, [B2]) фиксируется в
      manifest.json: controls.placebo.kind и виден в детализации самоконтроля
      report.md (report.py::render_controls_verdict_detail).
    - ВАЖНО (найдено сквозным смоук-тестом визуального пилота этой итерации, не
      юнит-тестами): build_effective_study ОБЯЗАН переносить image/label/
      key_element РЕАЛЬНЫХ стимулов через блиндинг (§1.4) — иначе визуальная
      генерация молча превращается в текстовую на ЛЮБОМ study.yaml, т.к. controls
      включены по умолчанию для всех study.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import generate  # noqa: E402
import report  # noqa: E402
import ssr_core  # noqa: E402

try:  # pillow — опционально: нужен только для проверки непараллельности (§1.1 v1.4)
    from PIL import Image  # noqa: E402
    _PIL_AVAILABLE = True
except ImportError:  # pragma: no cover - venv без pillow (setup.sh ставит его)
    Image = None  # type: ignore[assignment]
    _PIL_AVAILABLE = False

logger = logging.getLogger("run_study")

ALLOWED_STUDY_TYPES = {"claims_ranking", "concept_screening", "segment_reactions", "audience_probe"}

RED_ZONE_REFUSAL = """\
ОТКАЗ: study.yaml поле `type` = {type!r} не входит в поддерживаемые типы synthetic-panel.

Разрешено (зелёная/жёлтая зона метода, spec_synthetic-panel_v1.md §6-7, SKILL.md §1):
  claims_ranking      — ранжирование клеймов/сообщений
  concept_screening   — скрининг концептов
  segment_reactions   — сегментные реакции, барьеры/драйверы
  audience_probe      — аудиторная разведка

Метод SSR-панели структурно не годится для задач вроде: MMM/медиамикс/прогноз
продаж, Brand Lift, доли рынка/size of prize, сенсорика (вкус/аромат/текстура),
абсолютные частоты/объёмы потребления и абсолютный purchase intent в процентах —
это красная зона метода независимо от того, как оформлен study.yaml (подробное
обоснование каждого пункта — SKILL.md, раздел «Когда применять и когда
отказаться», и references/methodology.md).

Что делать: если задача на самом деле про относительное сравнение/ранжирование
стимулов или сегментные реакции — переформулируйте study.yaml с одним из
разрешённых `type` выше. Если задача действительно про перечисленное — этот
инструмент для неё не подходит ни при каком типе study.yaml.
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ============================================================================
# Загрузчик сегментов (spec_synthetic-panel_v1.1_segment_map.md, задание [B2] п.5)
# ============================================================================
#
# panel/segments/ теперь может содержать как плоские файлы v1 (panel/segments/
# dessertnye.yaml — 7 кофейных сегментов) так и вложенные по категориям файлы
# режима segment_map (panel/segments/<category_slug>/<id>.yaml, см.
# scripts/segments_export.py). Индекс строится РЕКУРСИВНО по имени файла без
# расширения (stem) — это тот же идентификатор, что study.yaml перечисляет в
# поле `segments:` (список sid, как и раньше в v1). Обратная совместимость: для
# плоских файлов индекс по stem работает идентично старому прямому пути
# `panel/segments/{sid}.yaml`, поведение не меняется. Конфликт (один и тот же
# stem найден в РАЗНЫХ папках дерева) — явная ошибка (exit 1) с перечислением
# всех путей-кандидатов, а не молчаливый выбор "первого попавшегося". Индекс
# строится по ВСЕМУ дереву за один проход (дёшево — только пути, YAML читается
# только для реально запрошенных study.yaml sid), но конфликт проверяется
# только для id, реально запрошенных текущим study.yaml (не аудит всего дерева).


def build_segment_index(segments_root: Path) -> dict[str, list[Path]]:
    """Группирует panel/segments/**/*.yaml по stem файла (без чтения содержимого)."""
    index: dict[str, list[Path]] = {}
    if not segments_root.exists():
        return index
    for path in sorted(segments_root.rglob("*.yaml")):
        index.setdefault(path.stem, []).append(path)
    return index


def resolve_segment_path(index: dict[str, list[Path]], sid: str, segments_root: Path) -> Path:
    candidates = index.get(sid, [])
    if not candidates:
        print(
            f"ОШИБКА: сегмент не найден: {sid} (искал рекурсивно в {segments_root}/**/{sid}.yaml)",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(candidates) > 1:
        listed = "\n".join(f"  {p}" for p in candidates)
        print(
            f"ОШИБКА: конфликт id сегмента {sid!r} — найден в {len(candidates)} местах:\n{listed}\n"
            f"Переименуйте один из файлов (id сегмента должен быть уникален во всём дереве "
            f"{segments_root}/, независимо от вложенности по категориям).",
            file=sys.stderr,
        )
        sys.exit(1)
    return candidates[0]


def load_segments(sids: list[str], skill_root: Path) -> dict[str, dict]:
    segments_root = skill_root / "panel" / "segments"
    index = build_segment_index(segments_root)
    segments: dict[str, dict] = {}
    for sid in sids:
        seg_path = resolve_segment_path(index, sid, segments_root)
        segments[sid] = load_yaml(seg_path)
    return segments


# ============================================================================
# Негативные контроли (spec_synthetic-panel_v1.3.md §1.4, фикс дефекта Д5:
# "Нет негативных контролей")
# ============================================================================
#
# По умолчанию ВКЛЮЧЕНЫ для ЛЮБОГО study.yaml, включая написанные до v1.3
# (обратная совместимость: отсутствие поля `controls` эквивалентно `controls: on`).
# Отключаются явным `controls: off` (или false/no/0/disabled) в study.yaml — с
# печатаемым предупреждением (см. load_or_init_manifest).
#
# Три механизма (контракт для report.py::compute_controls_verdict и для будущего
# B3-линтера, проверяющего наличие вердикта в отчёте — см. compute_controls_failed_banner/
# compute_controls_status_line ниже и report.py::render_controls_verdict_detail):
#   1. ПЛАЦЕБО — references/placebo_bank_ru.yaml, один элемент выбирается
#      детерминированно от (seed, study.name). Обязан финишировать в нижней трети
#      рейтинга КАЖДОГО сегмента — иначе controls_failed.
#   2. ПАРА-ЛОВУШКА — косметическая копия ОДНОГО из РЕАЛЬНЫХ стимулов study.yaml
#      (см. make_decoy_text: правка чисто типографская — пунктуация/кавычки, смысл
#      не меняется). Обязана быть "в пределах шума" (report.py::separability_label)
#      относительно оригинала — иначе controls_failed.
#   3. СЛЕПЫЕ ID — ВСЕ стимулы (реальные + 2 контрольных) на СТАДИИ GENERATE уходят
#      под перемешанными от seed метками "BL1".."BLn" (см. build_effective_study);
#      реальные id восстанавливаются на стадии score/report из
#      manifest["controls"]["blind_to_real"] (см. unblind_rows). Заполняющему
#      responses_todo.jsonl слепые id не мешают: задача всегда "ответь на
#      stimulus_text данной строки", а stimulus_text — это ВСЕГДА реальный текст
#      (слепой id скрывает только АБСТРАКТНУЮ метку, не содержание, и это не нужно
#      знать, чтобы выполнить задание).
#
# Всё вычисляется и фиксируется в manifest.json ОДИН РАЗ при первой инициализации
# прогона (load_or_init_manifest) — как respondents_per_segment/samples_per_respondent
# (см. комментарий в run_report_stage) — и НЕ пересчитывается при повторных вызовах
# --stage score/--stage report на тот же run_dir: иначе разные вызовы могли бы
# получить РАЗНОЕ перемешивание слепых меток (например, если seed в config.yaml
# изменился между вызовами), что сломало бы соответствие с уже сгенерированными
# responses.jsonl.

CONTROLS_OFF_TOKENS = {"off", "false", "no", "0", "disabled", "нет", "выкл"}

PLACEBO_REAL_ID = "__placebo__"
DECOY_REAL_ID = "__decoy__"


def controls_requested(study: dict) -> bool:
    """study.yaml: `controls: off` (или false/no/0/disabled/нет/выкл) отключает
    негативные контроли §1.4. Отсутствие поля (старые study.yaml, до v1.3) ->
    контроли ВКЛЮЧЕНЫ (обратная совместимость: "по умолчанию ВКЛ" из spec)."""
    raw = study.get("controls")
    if raw is None:
        return True
    return str(raw).strip().lower() not in CONTROLS_OFF_TOKENS


def load_placebo_bank(skill_root: Path) -> list[dict]:
    """references/placebo_bank_ru.yaml — банк заведомо пустых/слабых "клеймов",
    минимум 3 (верхний предел сняли в v1.4 §2.2 — банк расширен контрастными
    kind, см. сам файл за полным контрактом требований к каждому плацебо)."""
    path = skill_root / "references" / "placebo_bank_ru.yaml"
    data = load_yaml(path)
    placebos = data.get("placebos") or []
    if len(placebos) < 3:
        raise ValueError(f"{path}: нужно минимум 3 плацебо-заготовки, найдено {len(placebos)}.")
    return placebos


# spec_synthetic-panel_v1.4.md §2.2 (находка №3 review_v1.3.md): контрастные
# kind плацебо — references/placebo_bank_ru.yaml, поле `kind` на элемент.
CONTRASTIVE_PLACEBO_KINDS = {"irrelevant", "empty_promise"}


def pick_placebo(bank: list[dict], seed: int, study_name: str) -> dict:
    """
    Ротация плацебо по seed — см. generate.make_rng (тот же детерминированный
    механизм, что и джиттер персон, просто с другим "namespace" в частях seed).

    §2.2 v1.4: по умолчанию ротация отдаёт предпочтение КОНТРАСТНЫМ kind
    (irrelevant/empty_promise, references/placebo_bank_ru.yaml) — если банк
    содержит хотя бы один такой элемент, rng.choice выбирает ТОЛЬКО среди них
    (старые "neutral" элементы не участвуют). Обратная совместимость: банк БЕЗ
    поля `kind` вовсе (прогон на bank v1.3, до этой правки) или без контрастных
    элементов — pool совпадает со всем bank, поведение и выбор ПОБИТОВО такие
    же, как до v1.4 (тот же rng.choice(bank) с тем же seed/study_name даёт тот
    же индекс — важно для воспроизводимости уже сделанных прогонов).
    """
    rng = generate.make_rng(seed, study_name, "controls_placebo")
    contrastive = [p for p in bank if p.get("kind") in CONTRASTIVE_PLACEBO_KINDS]
    pool = contrastive if contrastive else bank
    return rng.choice(pool)


def make_decoy_text(original_text: str, rng) -> str:
    """
    Косметическая правка текста стимула для пары-ловушки (§1.4): смысл НЕ меняется
    (правка чисто типографская — пунктуация/кавычки), но текст гарантированно не
    байт-в-байт идентичен оригиналу (иначе в agent-режиме, где один агент видит
    ВЕСЬ responses_todo.jsonl разом, дубликат было бы слишком легко механически
    заметить — см. ограничение ниже). Выбор варианта детерминирован от `rng`
    (передаётся вызывающим кодом — build_controls_manifest — уже с нужным seed).

    ОГРАНИЧЕНИЕ (честно, не скрывается): в agent-режиме заполняющий
    responses_todo.jsonl видит ВЕСЬ файл целиком и теоретически может заметить,
    что два текста почти идентичны, и неявно свести их оценки — слепые id (см.
    build_effective_study) снижают, но не устраняют этот риск полностью; для
    API-провайдеров (anthropic/openai) каждый вызов независим (см.
    generate.generate_responses — цикл per-task), там этого риска нет вовсе.
    """
    text = original_text.strip()

    def _toggle_period(t: str) -> str:
        return t[:-1] if t.endswith((".", "!", "?")) else t + "."

    def _toggle_guillemets(t: str) -> str:
        if t.startswith("«") and t.endswith("»") and len(t) > 2:
            return t[1:-1]
        return f"«{t}»"

    variants = [v for v in (_toggle_period(text), _toggle_guillemets(text)) if v != text]
    if not variants:
        variants = [text + " "]  # крайний вырожденный случай — гарантируем отличие
    return rng.choice(variants)


def pick_decoy_source(stimuli: list[dict], seed: int, study_name: str) -> dict:
    rng = generate.make_rng(seed, study_name, "controls_decoy_source")
    return rng.choice(stimuli)


def decoy_source_text(stimulus: dict) -> str:
    """
    §1.4/§1.1 v1.4: текст-основа для косметической правки пары-ловушки
    (make_decoy_text). Пара-ловушка ВСЕГДА чисто текстовая (см. build_effective_study
    докстринг) — даже когда реальный стимул, выбранный pick_decoy_source, image-only
    (нет непустого `text`, как у стимулов "image+label" визуальных исследований,
    spec_synthetic-panel_v1.4.md §1.1). В этом случае используем `label` (короткая
    подпись, ОБЯЗАТЕЛЬНАЯ для image-only стимулов, см. validate_and_resolve_stimuli)
    как ближайший текстовый эквивалент — гарантированно непустой, т.к. схема уже
    провалидирована к моменту вызова (image-only без label не проходит валидацию).
    До v1.4 у ЛЮБОГО стимула был непустой `text` — тогда это просто возвращает его,
    ПОБИТОВО то же поведение, что и раньше (обратная совместимость).
    """
    text = (stimulus.get("text") or "").strip()
    if text:
        return text
    label = (stimulus.get("label") or "").strip()
    if label:
        return label
    raise ValueError(
        f"study.yaml: стимул {stimulus.get('id', '?')!r} выбран источником пары-ловушки "
        "(controls, §1.4), но не несёт ни 'text', ни 'label' — не из чего строить "
        "текстовую пару-ловушку (validate_and_resolve_stimuli должен был это отловить раньше)."
    )


def build_controls_manifest(study: dict, skill_root: Path, seed: int) -> dict:
    """
    Вызывается РОВНО ОДИН РАЗ, при первой инициализации manifest.json прогона (см.
    load_or_init_manifest) — фиксирует плацебо/ловушку/слепые метки на весь прогон.
    Возвращает {"enabled": False, "reason": ...}, если study.yaml просит
    `controls: off` (см. controls_requested).
    """
    if not controls_requested(study):
        return {"enabled": False, "reason": "study.yaml: controls: off"}

    bank = load_placebo_bank(skill_root)
    placebo_entry = pick_placebo(bank, seed, study["name"])
    decoy_source = pick_decoy_source(study["stimuli"], seed, study["name"])
    decoy_rng = generate.make_rng(seed, study["name"], "controls_decoy_text")
    decoy_text = make_decoy_text(decoy_source_text(decoy_source), decoy_rng)

    real_ids = [s["id"] for s in study["stimuli"]]
    clash = {rid for rid in real_ids if rid in (PLACEBO_REAL_ID, DECOY_REAL_ID)}
    if clash:
        raise ValueError(
            f"study.yaml: id стимула(ов) {clash} совпадает со служебным id негативных "
            f"контролей ({PLACEBO_REAL_ID!r}/{DECOY_REAL_ID!r}) — переименуйте стимул(ы) в study.yaml."
        )
    real_ids_in_order = real_ids + [PLACEBO_REAL_ID, DECOY_REAL_ID]

    blind_labels = [f"BL{i + 1}" for i in range(len(real_ids_in_order))]
    shuffle_rng = generate.make_rng(seed, study["name"], "controls_blind_shuffle")
    shuffled_positions = list(range(len(real_ids_in_order)))
    shuffle_rng.shuffle(shuffled_positions)
    blind_to_real = {
        blind_labels[k]: real_ids_in_order[shuffled_positions[k]] for k in range(len(real_ids_in_order))
    }
    real_to_blind = {real_id: blind_id for blind_id, real_id in blind_to_real.items()}

    return {
        "enabled": True,
        "placebo": {
            "real_id": PLACEBO_REAL_ID,
            "bank_id": placebo_entry["id"],
            "text": placebo_entry["text"],
            "blind_id": real_to_blind[PLACEBO_REAL_ID],
            # §2.2 v1.4: kind контрастного плацебо ("neutral"|"irrelevant"|
            # "empty_promise") — фолбэк "neutral" для банков без поля kind
            # (обратная совместимость с bank v1.3).
            "kind": placebo_entry.get("kind", "neutral"),
        },
        "decoy": {
            "real_id": DECOY_REAL_ID,
            "decoy_of": decoy_source["id"],
            "text": decoy_text,
            "blind_id": real_to_blind[DECOY_REAL_ID],
        },
        "blind_to_real": blind_to_real,
        "real_to_blind": real_to_blind,
    }


def build_effective_study(study: dict, controls_manifest: dict) -> dict:
    """
    Возвращает КОПИЮ study с полем `stimuli`, дополненным (если controls.enabled)
    плацебо/ловушкой и переведённым на слепые id — используется ТОЛЬКО для стадии
    generate (build_tasks/write_agent_mode/API-вызовы, см. run_generate_stage).
    Оригинальный `study` (с реальными id) продолжает использоваться для
    отчёта/шапки — см. run_report_stage. Если контроли отключены — возвращает
    study БЕЗ изменений (тот же объект, стимулы не трогаются вовсе).

    ВАЖНО (spec_synthetic-panel_v1.4.md §1.1/1.3): `image`/`label`/`key_element`
    РЕАЛЬНЫХ стимулов обязаны пережить блиндинг — иначе (находка смоук-теста
    визуального пилота v1.4) build_tasks/write_agent_mode получают слепой
    стимул БЕЗ image/label, и вся визуальная генерация молча превращается в
    текстовую, притом что controls включены ПО УМОЛЧАНИЮ для любого study.yaml.
    Плацебо/ловушка — ВСЕГДА чисто текстовые (references/placebo_bank_ru.yaml,
    make_decoy_text) и image/label не несут вовсе.
    """
    if not controls_manifest.get("enabled"):
        return study

    real_to_blind = controls_manifest["real_to_blind"]
    placebo = controls_manifest["placebo"]
    decoy = controls_manifest["decoy"]

    all_real_stimuli = list(study["stimuli"]) + [
        {"id": placebo["real_id"], "text": placebo["text"]},
        {"id": decoy["real_id"], "text": decoy["text"]},
    ]
    blinded_stimuli = []
    for s in all_real_stimuli:
        # .get(...) а не s["text"]: image-only реальные стимулы (§1.1 v1.4, image+label
        # БЕЗ text вовсе, не просто с пустым text="") не несут ключа "text" в study.yaml —
        # прямая индексация здесь падала KeyError на первом же image-only study
        # (integration-баг, найден на реальном end-to-end прогоне visual_smoke, [F2] v1.4).
        blinded = {"id": real_to_blind[s["id"]], "text": s.get("text") or ""}
        for optional_field in ("image", "label", "key_element"):
            if s.get(optional_field):
                blinded[optional_field] = s[optional_field]
        blinded_stimuli.append(blinded)

    effective = dict(study)
    effective["stimuli"] = blinded_stimuli
    return effective


def unblind_rows(rows: list[dict], controls_manifest: Optional[dict]) -> list[dict]:
    """
    Возвращает НОВЫЙ список словарей с полем stimulus_id, переведённым из слепой
    метки обратно в реальный id (controls["blind_to_real"]). Если контроли
    отключены (`controls_manifest.get("enabled")` falsy) ИЛИ manifest прогона
    вообще не содержит `controls` (прогон до v1.3, `controls_manifest=None`) — id
    возвращаются КАК ЕСТЬ (identity-маппинг): ожидаемая обратная совместимость,
    не ошибка.
    """
    if not controls_manifest or not controls_manifest.get("enabled"):
        return rows
    blind_to_real = controls_manifest["blind_to_real"]
    return [
        {**row, "stimulus_id": blind_to_real.get(row["stimulus_id"], row["stimulus_id"])} for row in rows
    ]


def split_real_and_control_rows(
    rows: list[dict], real_stimulus_ids: set
) -> tuple[list[dict], list[dict]]:
    """После unblind_rows: делит строки на (реальные стимулы study.yaml, служебные
    строки плацебо/ловушки) по УЖЕ восстановленному real stimulus_id."""
    real_rows = [r for r in rows if r["stimulus_id"] in real_stimulus_ids]
    control_rows = [r for r in rows if r["stimulus_id"] not in real_stimulus_ids]
    return real_rows, control_rows


def find_sibling_rankings(
    run_dir: Path, study_name: str, real_stimulus_ids: set
) -> dict[str, list[list[str]]]:
    """
    §1.3.2 "Kendall-устойчивость рангов между прогонами" — ищет ЗАВЕРШЁННЫЕ (есть
    pmf_by_segment.csv) прогоны ТОГО ЖЕ study ("<study_name>_*" в том же runs/,
    кроме самого run_dir), читает их СОБСТВЕННЫЙ manifest.json (свой controls-блок
    — на случай если seed/конфиг отличались) для разблокировки id, и возвращает
    {segment_id: [ranking_прогона_1, ranking_прогона_2, ...]} — только по стимулам,
    реально входящим в ТЕКУЩИЙ real_stimulus_ids (сравнение множеств стимулов,
    разошедшихся между прогонами study.yaml, пропускается — см. report.py
    top_n_sets_agree, которое бросает ValueError на несовпадающих множествах;
    здесь фильтруем ДО этого, чтобы не звать его с заведомо разными множествами).
    Прогон без manifest.json/pmf_by_segment.csv (например, ещё не досчитан) —
    молча пропускается, это не ошибка (первый прогон study всегда без "соседей").
    """
    runs_root = run_dir.parent
    result: dict[str, list[list[str]]] = {}
    if not runs_root.exists():
        return result
    for candidate in sorted(runs_root.glob(f"{study_name}_*")):
        if candidate.resolve() == run_dir.resolve():
            continue
        seg_csv = candidate / "pmf_by_segment.csv"
        manifest_path = candidate / "manifest.json"
        if not seg_csv.exists() or not manifest_path.exists():
            continue
        try:
            sibling_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            sibling_rows = unblind_rows(report.read_pmf_by_segment(seg_csv), sibling_manifest.get("controls"))
        except (json.JSONDecodeError, KeyError, ValueError, OSError) as exc:
            logger.warning("find_sibling_rankings: пропускаю %s (%s)", candidate, exc)
            continue

        by_segment: dict[str, list[dict]] = {}
        for r in sibling_rows:
            if r["stimulus_id"] in real_stimulus_ids:
                by_segment.setdefault(r["segment"], []).append(r)
        for segment_id, seg_rows in by_segment.items():
            if {r["stimulus_id"] for r in seg_rows} != real_stimulus_ids:
                continue  # набор стимулов разошёлся между прогонами - сравнение бессмысленно
            ranking = [r["stimulus_id"] for r in sorted(seg_rows, key=lambda r: r["e_value"], reverse=True)]
            result.setdefault(segment_id, []).append(ranking)
    return result


def validate_study_type(study: dict) -> None:
    study_type = study.get("type")
    if study_type not in ALLOWED_STUDY_TYPES:
        print(RED_ZONE_REFUSAL.format(type=study_type), file=sys.stderr)
        sys.exit(1)


def validate_study_schema(study: dict, study_path: Path) -> None:
    required = ["name", "question_scale", "stimuli", "segments", "respondents_per_segment"]
    missing = [f for f in required if not study.get(f)]
    if missing:
        print(
            f"ОШИБКА: {study_path} — отсутствуют обязательные поля: {missing} "
            f"(схема — spec_synthetic-panel_v1.md §7).",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(study["stimuli"]) < 2:
        print(f"ОШИБКА: {study_path} — нужно минимум 2 стимула, получено {len(study['stimuli'])}.", file=sys.stderr)
        sys.exit(1)


# ============================================================================
# Визуальные стимулы — схема/валидация (spec_synthetic-panel_v1.4.md §1.1)
# ============================================================================
#
# study.yaml: элементы `stimuli` получают опциональное поле `image` (путь к файлу
# PNG/JPG/JPEG) и опциональные `label` (короткая подпись — ОБЯЗАТЕЛЬНА, если у
# стимула нет непустого `text`, т.к. иначе нечем подписать таблицу отчёта) и
# `key_element` (текст ключевого различающего элемента варианта — опционален,
# используется ТОЛЬКО пробой зрения §1.2, см. ниже). Допустимы: text-only (как
# раньше, до v1.4), image-only (text отсутствует/пуст, label обязателен),
# смешанные (и text, и image). validate_and_resolve_stimuli ниже — единственное
# место, которое проверяет и РАЗРЕШАЕТ (см. resolve_image_path) поле `image` —
# после этой функции `stimulus["image"]` (если было задано) ВСЕГДА абсолютный,
# существующий, провалидированного формата путь; остальной код (generate.py,
# report.py, vision-check ниже) читает `stimulus["image"]` уже как готовый к
# использованию путь, не занимаясь резолвом заново.

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}

# Эвристические пороги "непараллельности" (§1.1: "предупреждение, не блок") —
# грубая практическая проверка, не научная величина. Соотношение сторон > 20%
# ИЛИ площадь в пикселях > 60% разницы между самым узким/широким или
# самым маленьким/большим изображением стимулов этого study — предупреждение.
IMAGE_ASPECT_RATIO_WARN_FACTOR = 1.2
IMAGE_AREA_WARN_FACTOR = 1.6


def resolve_image_path(image_field: str, study_path: Path, skill_root: Path) -> Path:
    """
    §1.1: разрешает путь к файлу изображения стимула. Порядок попыток: абсолютный
    путь как есть; иначе относительно папки study.yaml; иначе относительно корня
    скилла. Первый СУЩЕСТВУЮЩИЙ вариант побеждает. ValueError (не sys.exit —
    вызывающий код решает, как реагировать) со списком всех опробованных путей,
    если файла нет НИ ПО ОДНОМУ из них, или если найденный файл не PNG/JPG/JPEG.
    """
    raw = Path(image_field)
    candidates = [raw] if raw.is_absolute() else [study_path.parent / raw, skill_root / raw]
    for candidate in candidates:
        if candidate.exists():
            if candidate.suffix.lower() not in IMAGE_EXTENSIONS:
                raise ValueError(
                    f"стимул: изображение {image_field!r} -> {candidate} имеет неподдерживаемый "
                    f"формат {candidate.suffix!r} (разрешены: {sorted(IMAGE_EXTENSIONS)})."
                )
            # .absolute() (НЕ .resolve()): docstring этой функции и
            # validate_and_resolve_stimuli обещают ВСЕГДА абсолютный путь — но при
            # относительном --study (обычный стиль вызова CLI, см. README/SKILL.md
            # примеры) study_path.parent тоже относителен, и candidate оставался
            # относительным (integration-баг, найден на реальном end-to-end прогоне
            # visual_smoke, [F2] v1.4 — юнит-тесты его не ловили, т.к. фикстуры
            # всегда строят абсолютный study_path). .absolute() лишь анчорит путь к
            # cwd процесса (идентична для уже-абсолютных путей — не меняет
            # поведение существующих тестов), НЕ трогает симлинки/".."
            # (в отличие от .resolve(), который на macOS мог бы неожиданно
            # переписать /var/folders-пути тестовых tempdir).
            return candidate.absolute()
    tried = "\n".join(f"  {c}" for c in candidates)
    raise ValueError(f"стимул: файл изображения не найден: {image_field!r}. Проверены пути:\n{tried}")


def check_image_parallelism(dims: list[tuple[str, int, int]]) -> Optional[str]:
    """
    §1.1 "предупреждение о непараллельности": существенно разные размеры/пропорции
    изображений между вариантами стимулов — предупреждение (манифест + отчёт), НЕ
    блокировка прогона (незначительные технические различия не всегда портят
    сравнение смыслов, но крупные — сигнал, что варианты подготовлены
    непоследовательно: разный кроп, разное разрешение экспорта и т.п.).

    dims — [(stimulus_id, width, height), ...] ТОЛЬКО стимулов с image; вызывающий
    код (validate_and_resolve_stimuli) уже отфильтровал < 2 записей (не с чем
    сравнивать — вернуть нечего предупреждать). Чистая функция без файлового
    ввода-вывода — тестируется на голых кортежах, без реальных изображений.
    """
    if len(dims) < 2:
        return None
    by_id = {sid: (w, h) for sid, w, h in dims}
    ratios = {sid: w / h for sid, (w, h) in by_id.items()}
    areas = {sid: w * h for sid, (w, h) in by_id.items()}

    issues: list[str] = []
    r_lo_id, r_lo = min(ratios.items(), key=lambda kv: kv[1])
    r_hi_id, r_hi = max(ratios.items(), key=lambda kv: kv[1])
    if r_lo > 0 and r_hi / r_lo > IMAGE_ASPECT_RATIO_WARN_FACTOR:
        issues.append(
            f"соотношение сторон различается заметно ({r_lo_id!r}: {r_lo:.2f}, {r_hi_id!r}: {r_hi:.2f})"
        )
    a_lo_id, a_lo = min(areas.items(), key=lambda kv: kv[1])
    a_hi_id, a_hi = max(areas.items(), key=lambda kv: kv[1])
    if a_lo > 0 and a_hi / a_lo > IMAGE_AREA_WARN_FACTOR:
        w_lo, h_lo = by_id[a_lo_id]
        w_hi, h_hi = by_id[a_hi_id]
        issues.append(f"разрешение различается заметно ({a_lo_id!r}: {w_lo}x{h_lo}, {a_hi_id!r}: {w_hi}x{h_hi})")
    if not issues:
        return None
    return (
        "изображения стимулов подготовлены непараллельно: " + "; ".join(issues) + " — сравнение "
        "вариантов может быть смещено техническими различиями макетов, а не их содержанием "
        "(предупреждение, не блокировка прогона)."
    )


def compute_stimulus_kind(any_image: bool, all_image: bool) -> str:
    """§1.1: вычисляемое поле stimulus_kind study-уровня для manifest.json —
    "text" (ни один стимул не несёт image), "image" (ВСЕ стимулы несут image),
    "mixed" (часть стимулов с image, часть без)."""
    if not any_image:
        return "text"
    return "image" if all_image else "mixed"


def validate_and_resolve_stimuli(study: dict, study_path: Path, skill_root: Path) -> dict:
    """
    §1.1: валидирует и РАЗРЕШАЕТ (мутирует IN PLACE — заменяет заданный в
    study.yaml путь на абсолютный, см. resolve_image_path) поле `image` каждого
    элемента study['stimuli']. Правила:
      - стимул обязан иметь непустой `text` И/ИЛИ `image` (иначе ValueError);
      - image-only (нет непустого text) обязан иметь непустой `label`;
      - `image`, если задан, обязан существовать и быть PNG/JPG/JPEG
        (resolve_image_path) — ValueError со списком проверенных путей иначе.
    Возвращает {"stimulus_kind": "text"|"image"|"mixed",
    "image_parallelism_warning": Optional[str]} — оба вычисляются ОДИН раз для
    ВСЕГО study и идут в manifest.json (см. load_or_init_manifest) и в
    report_template.md {{STIMULUS_KIND}}/{{STIMULUS_KIND_LINE}} (report.py,
    header_mapping — см. run_report_stage). Raises ValueError на первой
    найденной ошибке схемы (вызывающий код — main() — конвертирует в
    ОШИБКА:.../sys.exit(1), тот же стиль, что и validate_study_schema выше).
    """
    any_image = False
    all_image = True
    dims: list[tuple[str, int, int]] = []

    for s in study["stimuli"]:
        text = (s.get("text") or "").strip()
        image_field = s.get("image")
        has_text = bool(text)
        has_image = bool(image_field)

        if not has_text and not has_image:
            raise ValueError(f"study.yaml: стимул {s.get('id', '?')!r} — нужен непустой 'text' и/или 'image'.")

        if has_image:
            any_image = True
            resolved = resolve_image_path(str(image_field), study_path, skill_root)
            s["image"] = str(resolved)
            if not has_text and not (s.get("label") or "").strip():
                raise ValueError(
                    f"study.yaml: стимул {s['id']!r} — image-only (нет текста), но не задан "
                    "обязательный 'label' (короткая подпись для отчёта, spec_synthetic-panel_v1.4.md §1.1)."
                )
            if _PIL_AVAILABLE:
                try:
                    with Image.open(resolved) as im:
                        dims.append((s["id"], im.width, im.height))
                except Exception as exc:  # файл повреждён/нечитаем — не блокируем прогон целиком
                    logger.warning(
                        "run_study.py: не удалось прочитать размеры изображения %s (%s) — проверка "
                        "непараллельности (§1.1) для этого стимула пропущена.", resolved, exc,
                    )
        else:
            all_image = False

    return {
        "stimulus_kind": compute_stimulus_kind(any_image, all_image),
        "image_parallelism_warning": check_image_parallelism(dims),
    }


def resolve_run_dir(run_dir_arg: Optional[str], skill_root: Path, study_name: str, stage: str) -> Path:
    runs_root = skill_root / "runs"
    if run_dir_arg:
        rd = Path(run_dir_arg)
        return rd if rd.is_absolute() else (Path.cwd() / rd)

    if stage in ("all", "generate"):
        ts = datetime.now().strftime("%Y%m%d-%H%M")
        return runs_root / f"{study_name}_{ts}"

    # score/report без --run-dir: работаем, только если прогон этого study однозначен
    candidates = sorted(runs_root.glob(f"{study_name}_*")) if runs_root.exists() else []
    if len(candidates) == 1:
        print(f"-- --run-dir не указан, найден единственный прогон: {candidates[0]}")
        return candidates[0]
    if not candidates:
        print(
            f"ОШИБКА: --run-dir не указан и не найдено ни одного runs/{study_name}_*/ — "
            f"сначала выполните --stage generate.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        f"ОШИБКА: --run-dir не указан, найдено {len(candidates)} прогонов {study_name}_* — "
        "укажите --run-dir явно, какой из них продолжать:\n" + "\n".join(f"  {c}" for c in candidates),
        file=sys.stderr,
    )
    sys.exit(1)


def _stimulus_manifest_entry(s: dict) -> dict:
    """§1.1 v1.4: запись стимула в manifest.json['stimuli'] — id/text как раньше,
    плюс image (уже разрешённый абсолютный путь)/label/key_element ТОЛЬКО если
    реально заданы (не засорять manifest пустыми полями у текстовых study)."""
    entry = {"id": s["id"], "text": s.get("text", "")}
    for optional_field in ("image", "label", "key_element"):
        if s.get(optional_field):
            entry[optional_field] = s[optional_field]
    return entry


def load_or_init_manifest(
    run_dir: Path,
    study: dict,
    config: dict,
    study_path: Path,
    skill_root: Path,
    stimuli_info: Optional[dict] = None,
) -> dict:
    """
    Читает manifest.json существующего прогона КАК ЕСТЬ (без пересчёта — критично
    для controls: слепые метки/плацебо/ловушка фиксируются ОДИН раз здесь, при
    первой инициализации, см. build_controls_manifest, и НЕ должны пересчитываться
    при повторных --stage score/report, иначе разойдутся с уже сгенерированным
    responses.jsonl).

    stimuli_info — НОВОЕ v1.4 (spec_synthetic-panel_v1.4.md §1.1): результат
    validate_and_resolve_stimuli(study, ...), т.е. {"stimulus_kind", "image_
    parallelism_warning"} — фиксируется в manifest ОДИН раз при первой
    инициализации (тем же приёмом, что и controls выше); None (дефолт) для
    вызовов, где стимулы заведомо текстовые (например, тесты этого модуля,
    писавшиеся до v1.4) — тогда manifest["stimulus_kind"] будет "text" через
    тот же фолбэк, что report.py/report_template.md уже используют для
    прогонов ДО v1.4 (см. compute_stimulus_kind_line ниже).
    """
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    samples_per_respondent = int(
        study.get("samples_per_respondent") or config.get("llm", {}).get("samples_per_respondent", 2)
    )
    seed = int(config.get("report", {}).get("seed", 42))
    controls_manifest = build_controls_manifest(study, skill_root, seed)
    if controls_manifest.get("enabled"):
        print(
            "-- Негативные контроли включены (§1.4 spec_synthetic-panel_v1.3.md): "
            f"плацебо [{controls_manifest['placebo']['bank_id']}] "
            f"(kind={controls_manifest['placebo'].get('kind', 'neutral')!r}) + пара-ловушка "
            f"(косметическая копия стимула {controls_manifest['decoy']['decoy_of']!r}), "
            "слепые id для генерации (соответствие — manifest.json: controls.blind_to_real)."
        )
    else:
        print(
            f"-- ВНИМАНИЕ: негативные контроли ОТКЛЮЧЕНЫ "
            f"({controls_manifest.get('reason', 'controls: off')}). Выводы этого прогона "
            "не проходят самопроверку §1.4 — используйте с осторожностью."
        )
    stimuli_info = stimuli_info or {"stimulus_kind": "text", "image_parallelism_warning": None}
    return {
        "study_name": study["name"],
        "study_path": str(study_path),
        "study_type": study["type"],
        "question_scale": study["question_scale"],
        "segments": list(study["segments"]),
        "stimuli": [_stimulus_manifest_entry(s) for s in study["stimuli"]],
        "stimulus_kind": stimuli_info["stimulus_kind"],
        "image_parallelism_warning": stimuli_info.get("image_parallelism_warning"),
        "respondents_per_segment": int(study["respondents_per_segment"]),
        "samples_per_respondent": samples_per_respondent,
        "config_snapshot": config,
        "controls": controls_manifest,
        "created_at": now_iso(),
        "stages": {},
    }


def save_manifest(run_dir: Path, manifest: dict) -> None:
    manifest_path = run_dir / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ============================================================================
# Проба зрения (spec_synthetic-panel_v1.4.md §1.2)
# ============================================================================
#
# Обязательная стадия ПЕРЕД генерацией ответов персон, ЕСЛИ у study есть хотя бы
# один стимул с `image` (см. build_vision_check_targets — [] для чисто текстовых
# study, вся остальная машинерия ниже становится no-op). Модель БЕЗ роли персоны
# описывает КАЖДОЕ изображение (что видит: продукт, текст на макете, ключевые
# элементы) → артефакт run_dir/00_vision_check.yaml (+md, человекочитаемый
# двойник). Правило вердикта: если `key_element` стимула (study.yaml, опционален)
# не распознан в описании — стимул помечается `vision_failed`; прогон
# продолжается ТОЛЬКО по явному подтверждению (`confirmed_despite_failures: true`
# в 00_vision_check.yaml) — иначе останавливается (exit 2), аналог controls_failed
# по духу (манифест + красная плашка в отчёте), но, В ОТЛИЧИЕ от него, это
# ПРЕДВАРИТЕЛЬНЫЙ гейт ДО генерации, а не пост-хок вердикт — страховка "тестировали
# галлюцинацию, а не дизайн", а не просто пометка задним числом.
#
# Agent-режим: описание пишет ведущая модель САМА — читает файл (Read) по
# `image_path` и заполняет пустые `description`/`key_element_recognized` в
# 00_vision_check.yaml вручную (см. VISION_CHECK_STOP_PENDING — инструкция).
# API-режим (anthropic/openai): вызывается generate.describe_image_via_provider
# (отдельный, нейтральный system prompt — НЕ build_system_prompt персоны) — см.
# run_generate_stage. gigachat: отказ ДО попытки — честная ошибка о неподдержке
# (см. run_generate_stage, до вызова любого провайдера).

VISION_CHECK_YAML_NAME = "00_vision_check.yaml"
VISION_CHECK_MD_NAME = "00_vision_check.md"

# Короткий стоп-список — только чтобы не засчитывать служебные слова "значимыми"
# при эвристике keyword_recognized ниже; НЕ лингвистический разбор, самая частая
# мелкая закрытая группа союзов/предлогов русского языка.
_RU_STOPWORDS_SHORT = {
    "или", "как", "что", "это", "его", "она", "они", "тут", "там", "для", "при",
}


def build_vision_check_targets(study: dict) -> list[dict]:
    """
    §1.2: группирует стимулы study['stimuli'] с непустым `image` по РАЗРЕШЁННОМУ
    пути файла (validate_and_resolve_stimuli уже сделал image абсолютным путём к
    этому моменту, см. main()) — несколько стимулов, ссылающихся на ОДИН и тот же
    файл, описываются/оцениваются ОДИН раз. [] для чисто текстовых study (нет ни
    одного stimulus['image']) — все функции ниже, вызываемые ПОСЛЕ этой, для
    пустого списка целей являются no-op (см. run_generate_stage).
    """
    by_path: dict[str, dict] = {}
    for s in study["stimuli"]:
        image = s.get("image")
        if not image:
            continue
        target = by_path.setdefault(image, {"image_path": image, "stimulus_ids": [], "key_elements": {}})
        target["stimulus_ids"].append(s["id"])
        if s.get("key_element"):
            target["key_elements"][s["id"]] = s["key_element"]
    return [by_path[k] for k in sorted(by_path)]


def _vision_check_stub(targets: list[dict]) -> dict:
    """Начальное (todo) состояние 00_vision_check.yaml — пустые description, для
    заполнения агентом (agent-режим) или generate.fill_vision_check_descriptions
    (API-режим, см. run_generate_stage).

    `vision_check_source` (v1.4 fix, докстринг модуля, "Известное ограничение
    agent-режима пробы зрения" ниже) — None до заполнения; agent-режим обязан
    явно проставить "agent_self_reported" (см. VISION_CHECK_STOP_PENDING),
    API-режим проставляется автоматически ("api_vision", см.
    generate.fill_vision_check_descriptions) — честная запись о том, ЧЕМ именно
    подтверждён просмотр изображения, а не фактическое тому доказательство (см.
    references/methodology.md §6.3, оговорка про agent-режим)."""
    return {
        "meta": {"stage": "vision_check", "n_images": len(targets)},
        "confirmed_despite_failures": False,
        "images": [
            {
                "image_path": t["image_path"],
                "stimulus_ids": list(t["stimulus_ids"]),
                "key_element_by_stimulus": dict(t["key_elements"]),
                "description": "",
                "key_element_recognized": None,
                "vision_check_source": None,
            }
            for t in targets
        ],
    }


def write_vision_check_yaml(data: dict, run_dir: Path) -> Path:
    path = run_dir / VISION_CHECK_YAML_NAME
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return path


def load_vision_check(run_dir: Path) -> Optional[dict]:
    path = run_dir / VISION_CHECK_YAML_NAME
    if not path.exists():
        return None
    return load_yaml(path)


def vision_check_is_pending(vc: dict) -> bool:
    """True, если хотя бы одно изображение ещё без описания (заполнение агентом/
    API-вызовом ещё не произошло) — run_generate_stage останавливает прогон, пока
    это True (см. VISION_CHECK_STOP_PENDING)."""
    return any(not (img.get("description") or "").strip() for img in vc.get("images", []))


def _significant_words(text: str) -> list[str]:
    words = re.findall(r"[a-zа-яё0-9]+", text.lower())
    return [w for w in words if len(w) >= 3 and w not in _RU_STOPWORDS_SHORT]


def keyword_recognized(description: str, key_element: str) -> bool:
    """
    §1.2: эвристика "распознан ли key_element в свободном описании изображения" —
    ВСЕ значимые слова (>=3 символов, за вычетом короткого стоп-списка)
    key_element обязаны встретиться подстрокой в description (регистронезависимо).
    Простая, прозрачная, ДЕТЕРМИНИРОВАННАЯ проверка — не семантическое сравнение
    (не гоняем embedding-модель ради короткой фразы на этой стадии). ИЗВЕСТНОЕ
    ограничение (не скрыто): описание-синоним без общих слов даст ложный
    "vision_failed" — см. compute_vision_verdicts ниже, где явный
    key_element_recognized (agent сам видел изображение и может судить надёжнее
    строкового совпадения по СОБСТВЕННОМУ описанию) приоритетнее этой эвристики.
    Пустой/вырожденный key_element (после фильтрации не осталось значимых слов)
    -> True (нечего проверять, не считаем провалом).
    """
    words = _significant_words(key_element)
    if not words:
        return True
    desc_lower = description.lower()
    return all(w in desc_lower for w in words)


def compute_vision_verdicts(vc: dict) -> dict:
    """
    §1.2: вердикт по каждому изображению + агрегат по прогону. Для каждого
    stimulus_id, ссылающегося на изображение и задавшего непустой key_element:
    `key_element_recognized`, если явно True/False (не None) в 00_vision_check.yaml
    — приоритетнее эвристики (агент, реально видевший изображение, надёжнее
    строкового совпадения по собственному описанию); иначе — keyword_recognized
    на description. Стимулы без key_element — всегда "ok" (нечего проверять).
    Возвращает {"per_image": [...], "vision_failed": bool, "failed_stimulus_ids":
    [...], "n_stimuli_with_image": int, "confirmed_despite_failures": bool}.
    Каждый элемент `per_image` несёт `vision_check_source` (v1.4 fix — см.
    "Известное ограничение agent-режима пробы зрения" в докстринге модуля):
    "agent_self_reported" | "api_vision" | None (не заполнено/старый прогон
    до этой правки) — пропускается ЧЕРЕЗ агрегат без изменений, само по себе
    НЕ влияет ни на один вердикт (не код-уровня проверка, честная запись
    источника, не доказательство).
    """
    per_image = []
    failed_stimulus_ids: list[str] = []
    n_stimuli_with_image = 0
    for img in vc.get("images", []):
        description = img.get("description") or ""
        key_elements = img.get("key_element_by_stimulus") or {}
        explicit = img.get("key_element_recognized")
        per_stimulus_verdict: dict[str, str] = {}
        for sid in img.get("stimulus_ids", []):
            n_stimuli_with_image += 1
            key_element = key_elements.get(sid)
            if not key_element:
                per_stimulus_verdict[sid] = "ok"
                continue
            recognized = bool(explicit) if explicit is not None else keyword_recognized(description, key_element)
            if recognized:
                per_stimulus_verdict[sid] = "ok"
            else:
                per_stimulus_verdict[sid] = "vision_failed"
                failed_stimulus_ids.append(sid)
        per_image.append(
            {
                "image_path": img.get("image_path"),
                "stimulus_ids": list(img.get("stimulus_ids", [])),
                "description": description,
                "key_element_by_stimulus": key_elements,
                "per_stimulus_verdict": per_stimulus_verdict,
                "vision_check_source": img.get("vision_check_source"),
            }
        )
    return {
        "per_image": per_image,
        "vision_failed": bool(failed_stimulus_ids),
        "failed_stimulus_ids": failed_stimulus_ids,
        "n_stimuli_with_image": n_stimuli_with_image,
        "confirmed_despite_failures": bool(vc.get("confirmed_despite_failures", False)),
    }


# v1.4 fix: человекочитаемая расшифровка vision_check_source (см. докстринг
# _vision_check_stub/compute_vision_verdicts и "Известное ограничение agent-
# режима пробы зрения" в модульном докстринге) — используется ТОЛЬКО в
# render_vision_check_markdown, самих вердиктов не меняет.
_VISION_CHECK_SOURCE_RU = {
    "agent_self_reported": (
        "агент (самоотчёт агент-режима — без кодовой проверки, что описание "
        "реально основано на просмотре файла, см. references/methodology.md §6.3)"
    ),
    "api_vision": "отдельный API vision-вызов (provider реально получил пиксели изображения)",
    None: "не указан (старый прогон до v1.4 fix либо источник не заполнен)",
}


def render_vision_check_markdown(verdicts: dict) -> str:
    """Человекочитаемый двойник 00_vision_check.yaml (см. модульный докстринг,
    прецедент — runs/*/00_filter.md рядом с 00_filter.yaml в этом же проекте)."""
    lines = ["# Проба зрения (spec_synthetic-panel_v1.4.md §1.2)", ""]
    if not verdicts["failed_stimulus_ids"]:
        lines.append(
            "Вердикт: ключевые элементы распознаны везде, где заданы (или key_element "
            "не задавался ни для одного стимула)."
        )
    else:
        lines.append(
            f"Вердикт: **vision_failed** для стимулов "
            f"{', '.join(verdicts['failed_stimulus_ids'])} — ключевой элемент не распознан в описании."
        )
        lines.append(
            "Подтверждено продолжение вопреки провалу (confirmed_despite_failures): "
            + ("да" if verdicts["confirmed_despite_failures"] else "НЕТ")
        )
    lines.append("")
    for img in verdicts["per_image"]:
        lines.append(f"## {img['image_path']}")
        lines.append(f"Стимулы: {', '.join(img['stimulus_ids'])}")
        lines.append("")
        lines.append(f"Описание (без роли персоны): {img['description'] or '_(не заполнено)_'}")
        source = img.get("vision_check_source")
        lines.append(f"Источник описания: {_VISION_CHECK_SOURCE_RU.get(source, source)}")
        for sid, key_element in (img["key_element_by_stimulus"] or {}).items():
            mark = "OK" if img["per_stimulus_verdict"].get(sid) == "ok" else "ПРОВАЛ"
            lines.append(f"- key_element {sid}: «{key_element}» -> {mark}")
        lines.append("")
    return "\n".join(lines)


def write_vision_check_markdown(verdicts: dict, run_dir: Path) -> Path:
    path = run_dir / VISION_CHECK_MD_NAME
    path.write_text(render_vision_check_markdown(verdicts), encoding="utf-8")
    return path


VISION_CHECK_STOP_PENDING = """\
== Проба зрения (§1.2): нужно описать изображения ПЕРЕД генерацией ответов персон ==
Study содержит визуальные стимулы. Файл {vc_path} создан с пустыми описаниями.

Что сделать:
1. Для КАЖДОГО элемента `images` в {vc_name} прочитайте файл по пути `image_path`
   (инструмент чтения файла) и впишите в поле `description` объективное описание
   БЕЗ роли персоны: что на изображении — продукт, текст на макете, ключевые
   визуальные элементы, композиция, цвета. Не оценивайте, не изображайте персону.
2. Если для стимула задан `key_element_by_stimulus` — явно впишите
   `key_element_recognized: true`/`false` (распознан ли этот элемент на
   изображении, по вашему суждению — надёжнее, чем автоматическое сравнение слов).
3. Впишите `vision_check_source: agent_self_reported` для КАЖДОГО элемента
   `images` — честная запись, что описание заполнено вами (agent-режим), а не
   отдельным API vision-вызовом. ВАЖНО (известное ограничение метода, см.
   references/methodology.md §6.3): это самоотчёт — код НЕ проверяет, что
   description реально основано на просмотре файла, а не сочинено правдоподобно
   без Read-вызова. Действительно прочитайте файл, прежде чем писать описание.
4. Сохраните файл и повторите:
   python scripts/run_study.py --study {study_path} --stage generate --run-dir {run_dir}
"""

VISION_CHECK_STOP_FAILED = """\
== Проба зрения (§1.2) провалена ==
Ключевой элемент не распознан для стимулов: {failed}.
Это может означать, что макет нечитаем/не соответствует описанию, а не что
персона реагирует на реальный дизайн — продолжать без явного подтверждения
рискованно ("тестировали галлюцинацию, а не дизайн").

Чтобы продолжить всё равно (например, если после ручной проверки описание
корректно, просто key_element сформулирован строже эвристики) — откройте
{vc_path} и установите:
    confirmed_despite_failures: true
Затем повторите ту же команду --stage generate.

Файл с деталями: {vc_md_path}
"""


# ============================================================================
# Стадия generate
# ============================================================================


def run_generate_stage(
    run_dir: Path,
    study: dict,
    config: dict,
    segments: dict[str, dict],
    question: str,
    study_path: Path,
    manifest: dict,
) -> generate.GenerateOutcome:
    provider_name = config.get("llm", {}).get("provider", "agent")
    responses_path = run_dir / "responses.jsonl"

    # §1.2 v1.4: проба зрения — гейт ПЕРЕД любой генерацией ответов персон, ЕСЛИ
    # study содержит визуальные стимулы (build_vision_check_targets возвращает []
    # для чисто текстовых study — блок ниже целиком no-op в этом случае, ничего
    # не меняется для v1.3-стиля прогонов).
    vision_targets = build_vision_check_targets(study)
    if vision_targets:
        if provider_name == "gigachat":
            print(
                "ОШИБКА: study.yaml содержит визуальные стимулы (image), а provider=gigachat "
                "визуальные стимулы пока не поддерживает (см. generate.GigaChatProvider, "
                "spec_synthetic-panel_v1.4.md §1.3). Используйте provider: agent/anthropic/openai.",
                file=sys.stderr,
            )
            sys.exit(1)

        vc_path = run_dir / VISION_CHECK_YAML_NAME
        if not vc_path.exists():
            write_vision_check_yaml(_vision_check_stub(vision_targets), run_dir)

        vc = load_vision_check(run_dir)

        # API-режим может заполнить описания сам (нейтральный vision-вызов,
        # см. generate.describe_image_via_provider) — agent-режим оставляет это
        # ведущей модели (см. VISION_CHECK_STOP_PENDING ниже).
        if provider_name in ("anthropic", "openai") and vc is not None and vision_check_is_pending(vc):
            vision_provider = generate.get_provider(provider_name, config)
            generate.fill_vision_check_descriptions(vc, vision_provider)
            write_vision_check_yaml(vc, run_dir)
            vc = load_vision_check(run_dir)

        if vc is None or vision_check_is_pending(vc):
            verdicts_stub = compute_vision_verdicts(vc or _vision_check_stub(vision_targets))
            write_vision_check_markdown(verdicts_stub, run_dir)
            print(
                VISION_CHECK_STOP_PENDING.format(
                    vc_path=vc_path, vc_name=VISION_CHECK_YAML_NAME, study_path=study_path, run_dir=run_dir,
                ),
                file=sys.stderr,
            )
            sys.exit(2)

        vision_verdict = compute_vision_verdicts(vc)
        write_vision_check_markdown(vision_verdict, run_dir)
        manifest["vision_check"] = vision_verdict

        if vision_verdict["vision_failed"] and not vision_verdict["confirmed_despite_failures"]:
            print(
                VISION_CHECK_STOP_FAILED.format(
                    failed=", ".join(vision_verdict["failed_stimulus_ids"]),
                    vc_path=vc_path,
                    vc_md_path=run_dir / VISION_CHECK_MD_NAME,
                ),
                file=sys.stderr,
            )
            sys.exit(2)

        if vision_verdict["vision_failed"]:
            print(
                "== ВНИМАНИЕ: проба зрения провалена для "
                f"{', '.join(vision_verdict['failed_stimulus_ids'])}, но подтверждено продолжение "
                "(confirmed_despite_failures: true) — ответы персон по этим стимулам ненадёжны, "
                "отчёт пометит это красной плашкой.",
                file=sys.stderr,
            )

    # §1.4: стадия generate работает на "эффективном" study — с добавленными
    # плацебо/ловушкой и переведённым на слепые id (см. build_effective_study).
    # manifest["controls"] фиксирован в load_or_init_manifest ОДИН раз для всего
    # прогона, поэтому effective_study одинаков при любом числе вызовов --stage
    # generate на один и тот же run_dir.
    effective_study = build_effective_study(study, manifest.get("controls") or {"enabled": False})

    if provider_name == "agent" and responses_path.exists():
        n_tasks = sum(1 for line in responses_path.open("r", encoding="utf-8") if line.strip())
        print(f"-- {responses_path.name} уже существует ({n_tasks} строк) — пропускаю генерацию todo.")
        outcome = generate.GenerateOutcome(
            status="completed",
            responses_path=responses_path,
            todo_path=None,
            n_tasks=n_tasks,
            provider="agent",
            temperature_control=False,
        )
    else:
        outcome = generate.generate_responses(
            effective_study, config, segments, question, run_dir, str(study_path)
        )

    llm_cfg = config.get("llm", {})
    manifest.setdefault("stages", {})["generate"] = {
        "provider": outcome.provider,
        "status": outcome.status,
        "n_tasks": outcome.n_tasks,
        "temperature_control": outcome.temperature_control,
        "model": llm_cfg.get("model") if outcome.provider != "agent" else None,
        "temperature": llm_cfg.get("temperature") if outcome.provider != "agent" else None,
        "completed_at": now_iso(),
    }
    return outcome


# ============================================================================
# Стадия score
# ============================================================================


def run_score_stage(run_dir: Path, config: dict, anchor_sets: list[dict[int, str]], manifest: dict) -> None:
    responses_path = run_dir / "responses.jsonl"
    if not responses_path.exists():
        print(
            f"ОШИБКА: {responses_path} не найден — сначала выполните --stage generate "
            "(в agent-режиме — и заполните responses.jsonl по AGENT_TASK.md).",
            file=sys.stderr,
        )
        sys.exit(1)

    # §1.2 v1.4: защита в глубину — если по какой-то причине (например, ручной
    # обход стадии generate) 00_vision_check.yaml существует, но не завершён/не
    # подтверждён после провала, --stage score отказывает, а не молча считает по
    # ненадёжным ответам. Штатный путь (через --stage generate) уже остановил бы
    # прогон раньше — см. run_generate_stage; это ТОЛЬКО страховка, не дублирующий
    # основной путь для study без изображений (vc is None -> no-op).
    vc_path = run_dir / VISION_CHECK_YAML_NAME
    if vc_path.exists():
        vc = load_vision_check(run_dir)
        if vc is not None and vision_check_is_pending(vc):
            print(
                f"ОШИБКА: {vc_path} ещё не заполнен (проба зрения §1.2) — сначала завершите её "
                "(см. --stage generate).",
                file=sys.stderr,
            )
            sys.exit(1)
        if vc is not None:
            vision_verdict = compute_vision_verdicts(vc)
            if vision_verdict["vision_failed"] and not vision_verdict["confirmed_despite_failures"]:
                print(
                    f"ОШИБКА: проба зрения провалена и не подтверждена (confirmed_despite_failures) "
                    f"— см. {vc_path}.",
                    file=sys.stderr,
                )
                sys.exit(1)
            manifest["vision_check"] = vision_verdict

    rows: list[dict] = []
    with responses_path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"ОШИБКА: {responses_path}:{line_no} не парсится как JSON: {exc}", file=sys.stderr)
                sys.exit(1)

    if not rows:
        print(f"ОШИБКА: {responses_path} пуст — нечего скорить.", file=sys.stderr)
        sys.exit(1)

    missing_text = [r.get("rid", "?") for r in rows if not (r.get("text") or "").strip()]
    if missing_text:
        print(
            f"ОШИБКА: {len(missing_text)} строк(и) в {responses_path} без непустого поля 'text' "
            f"(например rid={missing_text[0]}). Заполните responses.jsonl по AGENT_TASK.md полностью.",
            file=sys.stderr,
        )
        sys.exit(1)

    emb_cfg = config.get("embedding", {})
    embedding_model = emb_cfg.get("model", "paraphrase-multilingual-MiniLM-L12-v2")
    print(f"-- Загружаю embedding-модель: {embedding_model} (device={emb_cfg.get('device', 'cpu')})")
    backend = ssr_core.SentenceTransformerBackend(
        model_name=embedding_model,
        device=emb_cfg.get("device", "cpu"),
        prefix=emb_cfg.get("prefix", ""),
    )

    ssr_cfg = config.get("ssr", {})
    epsilon = float(ssr_cfg.get("epsilon", 0.001))
    pmf_temperature = float(ssr_cfg.get("pmf_temperature", 1.0))
    min_anchor_sets = int(ssr_cfg.get("min_anchor_sets", 4))

    engine = ssr_core.SSREngine(
        backend,
        anchor_sets,
        epsilon=epsilon,
        pmf_temperature=pmf_temperature,
        min_anchor_sets=min_anchor_sets,
    )

    texts = [r["text"] for r in rows]
    print(f"-- Считаю PMF для {len(texts)} ответов ({len(anchor_sets)} наборов якорей)...")
    pmf_per_response = engine.score_texts(texts)  # (n, 5) — "PMF ответа" (усреднено по наборам якорей)

    try:
        ssr_core.cross_check_with_ssr_package(texts, anchor_sets, epsilon, pmf_temperature, pmf_per_response)
    except Exception as exc:  # кросс-чек опционален — никогда не роняем score-стадию из-за него
        logger.debug("Кросс-чек semantic-similarity-rating пропущен из-за ошибки: %s", exc)

    # НОВОЕ в v1.3 (§1.3.2/1.3.4): pmf_by_sample.csv — гранулярность "1 строка = 1
    # ответ" (segment, stimulus_id, respondent_idx, sample_idx), т.е. САМ
    # pmf_per_response ДО усреднения по сэмплам, просто экспортированный на диск.
    # Нужен report.py для сплит-халфа по сэмплам и внутрипрогонной нестабильности
    # (расхождение "локального лидера" между сэмплами ОДНОГО респондента) —
    # раньше эта информация терялась на первом же шаге агрегации ниже.
    sample_csv_path = run_dir / "pmf_by_sample.csv"
    with sample_csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["segment", "stimulus_id", "respondent_idx", "sample_idx", "P1", "P2", "P3", "P4", "P5", "E"]
        )
        e_per_response = ssr_core.expected_value(pmf_per_response).flatten()
        sample_export_rows = [
            (r["segment"], r["stimulus_id"], int(r["respondent_idx"]), int(r["sample_idx"]), pmf_per_response[i], e_per_response[i])
            for i, r in enumerate(rows)
        ]
        for segment, stimulus_id, respondent_idx, sample_idx, pmf_row, e_val in sorted(
            sample_export_rows, key=lambda x: (x[0], x[1], x[2], x[3])
        ):
            writer.writerow(
                [segment, stimulus_id, respondent_idx, sample_idx, *[f"{p:.6f}" for p in pmf_row], f"{e_val:.6f}"]
            )

    # Уровень "PMF ответа" -> "PMF респондента": усреднение по сэмплам (sample_idx) одного
    # (segment, stimulus_id, respondent_idx).
    resp_keys = [f"{r['segment']}||{r['stimulus_id']}||{int(r['respondent_idx']):03d}" for r in rows]
    n_samples_by_key: dict[str, int] = {}
    for k in resp_keys:
        n_samples_by_key[k] = n_samples_by_key.get(k, 0) + 1
    resp_pmf_by_key = ssr_core.aggregate_pmfs_by_key(pmf_per_response, resp_keys)

    resp_rows = []
    for key, pmf in resp_pmf_by_key.items():
        segment, stimulus_id, respondent_idx = key.split("||")
        e_val = float(ssr_core.expected_value(pmf)[0])
        resp_rows.append(
            {
                "segment": segment,
                "stimulus_id": stimulus_id,
                "respondent_idx": int(respondent_idx),
                "n_samples": n_samples_by_key[key],
                "pmf": pmf,
                "e_value": e_val,
            }
        )

    # Уровень "PMF респондента" -> "PMF сегмента": усреднение по респондентам одного
    # (segment, stimulus_id). CI считается бутстрепом по E-значениям респондентов той же группы
    # (E — линейный функционал, E(mean(pmf)) == mean(E(pmf)), см. ssr_core.bootstrap_ci docstring).
    seg_keys_per_resp = [f"{r['segment']}||{r['stimulus_id']}" for r in resp_rows]
    resp_pmf_matrix = np.stack([r["pmf"] for r in resp_rows], axis=0)
    seg_pmf_by_key = ssr_core.aggregate_pmfs_by_key(resp_pmf_matrix, seg_keys_per_resp)

    e_values_by_seg_key: dict[str, list[float]] = {}
    n_resp_by_seg_key: dict[str, int] = {}
    for r, seg_key in zip(resp_rows, seg_keys_per_resp):
        e_values_by_seg_key.setdefault(seg_key, []).append(r["e_value"])
        n_resp_by_seg_key[seg_key] = n_resp_by_seg_key.get(seg_key, 0) + 1

    report_cfg = config.get("report", {})
    bootstrap_iters = int(report_cfg.get("bootstrap_iters", 1000))
    seed = int(report_cfg.get("seed", 42))
    ci = float(report_cfg.get("ci", 0.95))

    seg_rows = []
    for seg_key, pmf in seg_pmf_by_key.items():
        segment, stimulus_id = seg_key.split("||")
        e_values = np.array(e_values_by_seg_key[seg_key])
        boot = ssr_core.bootstrap_ci(e_values, n_iters=bootstrap_iters, seed=seed, ci=ci)
        e_val = float(ssr_core.expected_value(pmf)[0])
        seg_rows.append(
            {
                "segment": segment,
                "stimulus_id": stimulus_id,
                "n_respondents": n_resp_by_seg_key[seg_key],
                "pmf": pmf,
                "e_value": e_val,
                "ci_low": boot.ci_low,
                "ci_high": boot.ci_high,
            }
        )

    resp_csv_path = run_dir / "pmf_by_respondent.csv"
    with resp_csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["segment", "stimulus_id", "respondent_idx", "n_samples", "P1", "P2", "P3", "P4", "P5", "E"])
        for r in sorted(resp_rows, key=lambda x: (x["segment"], x["stimulus_id"], x["respondent_idx"])):
            writer.writerow(
                [
                    r["segment"],
                    r["stimulus_id"],
                    r["respondent_idx"],
                    r["n_samples"],
                    *[f"{p:.6f}" for p in r["pmf"]],
                    f"{r['e_value']:.6f}",
                ]
            )

    seg_csv_path = run_dir / "pmf_by_segment.csv"
    with seg_csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["segment", "stimulus_id", "n_respondents", "P1", "P2", "P3", "P4", "P5", "E", "ci_low", "ci_high"])
        for r in sorted(seg_rows, key=lambda x: (x["segment"], x["stimulus_id"])):
            writer.writerow(
                [
                    r["segment"],
                    r["stimulus_id"],
                    r["n_respondents"],
                    *[f"{p:.6f}" for p in r["pmf"]],
                    f"{r['e_value']:.6f}",
                    f"{r['ci_low']:.6f}",
                    f"{r['ci_high']:.6f}",
                ]
            )

    print(f"-- score: готово -> {resp_csv_path.name}, {seg_csv_path.name}, {sample_csv_path.name}")

    # §1.5: config.yaml["embedding"]["validated_stack"] (или, толерантно, верхнеуровневое
    # config.yaml["validated_stack"]) — метка "победившего" эмбеддера после embedder_ab.py
    # ([B2], может ещё не существовать на момент этого прогона — тогда просто False,
    # т.е. mode не станет "validated", даже если провайдер API, см. compute_run_mode).
    validated_stack = bool(
        config.get("embedding", {}).get("validated_stack") or config.get("validated_stack")
    )

    manifest["embedding_model"] = embedding_model  # верхнеуровневый контракт §1.5
    manifest.setdefault("stages", {})["score"] = {
        "embedding_model": embedding_model,
        "embedding_device": emb_cfg.get("device", "cpu"),
        "embedding_prefix": emb_cfg.get("prefix", ""),
        "embedding_validated_stack": validated_stack,
        "epsilon": epsilon,
        "pmf_temperature": pmf_temperature,
        "min_anchor_sets": min_anchor_sets,
        "n_responses_scored": len(rows),
        "bootstrap_iters": bootstrap_iters,
        "bootstrap_seed": seed,
        "ci": ci,
        "completed_at": now_iso(),
    }


# ============================================================================
# Стадия report
# ============================================================================


def compute_run_mode(manifest: dict, controls_verdict: dict) -> str:
    """
    §1.5 — mode: "validated" ТОЛЬКО если ВСЕ выполнено разом: (a) провайдер —
    реальный API (anthropic/openai/gigachat), НЕ agent (agent -> temperature_control
    =false, ответы не воспроизводимы штатным способом — см. generate.py §5); (b)
    эмбеддер зафиксирован как "валидированный стек" ([B2]: config.yaml
    validated_stack — см. run_score_stage, embedding_validated_stack в manifest);
    (c) негативные контроли §1.4 ВКЛЮЧЕНЫ и ПРОЙДЕНЫ (controls_verdict.applicable
    и не controls_failed). Иначе — "exploratory". До того как [B2] выставит
    validated_stack в config.yaml, ЛЮБОЙ прогон честно останется "exploratory" —
    это отражает реальность (эмбеддер ещё не прошёл гейт §1.2), а не недоработка.
    """
    gen_stage = manifest.get("stages", {}).get("generate", {})
    provider = gen_stage.get("provider", "agent")
    score_stage = manifest.get("stages", {}).get("score", {})
    validated_embedding = bool(score_stage.get("embedding_validated_stack"))
    controls_ok = bool(controls_verdict.get("applicable")) and not controls_verdict.get("controls_failed", True)

    if provider != "agent" and validated_embedding and controls_ok:
        return "validated"
    return "exploratory"


# Ровно ДВА текста бейджа (report_template.md v1.3, "ЧТО МЕНЯЕТСЯ ДЛЯ REPORT.PY"
# п.4, [B3]) — не изобретать третий цвет/статус.
MODE_BADGE_RU = {
    "validated": "🟢 ВАЛИДИРОВАННЫЙ",
    "exploratory": "🟡 РАЗВЕДОЧНЫЙ",
}


def compute_controls_status_line(controls_manifest: Optional[dict], controls_verdict: dict) -> str:
    """
    {{CONTROLS_STATUS_LINE}} (report_template.md v1.3, п.6, [B3]) — человеческая
    фраза о результате самоконтроля; используется В ПРЕДЛОЖЕНИИ с точкой в конце
    ("Самоконтроль прогона: {{CONTROLS_STATUS_LINE}}."), поэтому сама фраза БЕЗ
    финальной точки. Три канонических варианта + явный четвёртый для прогонов
    до v1.3 (в шаблоне не описан отдельно, но нужен для обратной совместимости —
    manifest.json таких прогонов не содержит `controls` вовсе).
    """
    if controls_manifest is None:
        return "контроли недоступны (прогон выполнен до v1.3 — самоконтроль не проводился)"
    if not controls_manifest.get("enabled"):
        return "контроли отключены явным флагом study.yaml — самоконтроль не проводился"
    if controls_verdict.get("controls_failed"):
        return "прогон НЕ прошёл самоконтроль (см. приложение) — выводы не использовать"
    return "плацебо и ловушка на своих местах — самоконтроль пройден"


def compute_controls_failed_banner(controls_verdict: dict) -> str:
    """
    {{CONTROLS_FAILED_BANNER}} (report_template.md v1.3, п.6) — пустая строка при
    controls_failed=false; короткий абзац при true. Несёт КОНТРАКТ-МАРКЕР для
    будущего линтера [B3] (cjm_lint.py) — точная фраза "прогон не прошёл
    самоконтроль" ОБЯЗАНА присутствовать где-то в отчёте при controls_failed=true
    (см. также report.py::render_controls_verdict_detail — второе место с той же
    фразой, в "Приложении"); не сокращать/переформулировать этот маркер.
    """
    if not controls_verdict.get("controls_failed"):
        return ""
    return (
        "> **прогон не прошёл самоконтроль, выводы не использовать.** Негативные "
        "контроли §1.4 провалены (плацебо не оказалось в нижней трети рейтинга "
        "и/или пара-ловушка статистически отличима от оригинала) — см. детализацию "
        "в разделе «Приложение»."
    )


# ============================================================================
# Проба зрения — плейсхолдеры report_template.md v1.4 (§1.1/1.2, [B3] — форма
# шаблона; вычисления и manifest-поля — [B1], см. докстринг вверху файла
# "ЧТО МЕНЯЕТСЯ ДЛЯ REPORT.PY (v1.4...)" в самом report_template.md за полным
# контрактом п.10-14). Тот же приём, что и MODE_BADGE/CONTROLS_STATUS_LINE/
# CONTROLS_FAILED_BANNER выше — report.py сам их НЕ вычисляет, получает готовыми
# строками через header_mapping (см. run_report_stage).
# ============================================================================


def compute_stimulus_kind_line(stimulus_kind: str, vision_verdict: Optional[dict]) -> str:
    """
    {{STIMULUS_KIND_LINE}} (report_template.md, п.10) — пометка визуального
    прогона сразу под «Режим прогона». Пустая строка для stimulus_kind == "text"
    (подавляющее большинство прогонов, включая ВСЕ прогоны до v1.4 — фолбэк
    "text" при отсутствии поля в manifest, см. run_report_stage) — НИКАК не
    меняет report.md для текстовых study. 🖼️/📝, НЕ 🟢/🔴 — те зарезервированы
    cjm_lint.py::looks_like_trust_map_report (см. комментарий шаблона, п.10) —
    иначе обычный report.md ложно стал бы "trust-map-документом" для линтера.
    """
    if stimulus_kind not in ("image", "mixed"):
        return ""
    kind_label = "🖼️ ВИЗУАЛЬНЫЕ" if stimulus_kind == "image" else "📝🖼️ СМЕШАННЫЕ"
    passed = bool(vision_verdict) and not vision_verdict.get("vision_failed")
    status = "проба зрения: пройдена" if passed else "проба зрения: НЕ пройдена — см. паспорт методологии"
    return f"**Стимулы:** {kind_label} ({status})"


def compute_vision_check_status_line(vision_verdict: Optional[dict]) -> str:
    """
    {{VISION_CHECK_STATUS_LINE}} (report_template.md, "Технический паспорт
    прогона" + переиспользуется внутри compute_vision_check_section ниже) —
    пустая строка, если прогон не визуальный (vision_verdict is None, т.е.
    stimulus_kind == "text" — см. комментарий шаблона у самого плейсхолдера).
    Два канонических текста, по аналогии с compute_controls_status_line.
    """
    if not vision_verdict:
        return ""
    if vision_verdict.get("vision_failed"):
        n_failed = len(vision_verdict.get("failed_stimulus_ids", []))
        n_with_image = vision_verdict.get("n_stimuli_with_image", n_failed)
        return (
            f"НЕ пройдена для {n_failed} из {n_with_image} вариантов с изображением "
            "(см. 00_vision_check.md) — прогон продолжен только по явному подтверждению "
            "либо соответствующий стимул помечен непригодным; выводы по затронутым "
            "вариантам использовать с осторожностью, не как готовый результат"
        )
    return "пройдена — ключевые элементы всех вариантов распознаны"


def compute_vision_check_section(vision_verdict: Optional[dict]) -> str:
    """
    {{VISION_CHECK_SECTION}} (report_template.md, п.11) — абзац «Паспорта
    методологии» сразу после «Самоконтроль прогона: ...». Пустая строка целиком
    при stimulus_kind == "text" (vision_verdict is None) — весь абзац исчезает,
    report.md текстовых study не меняется. Формулировка — прямая калька примера
    из комментария шаблона (не перефразировать — согласовано с [B3]).
    """
    if not vision_verdict:
        return ""
    status_line = compute_vision_check_status_line(vision_verdict)
    return (
        f"**Проба зрения:** {status_line} — до того как персоны начали реагировать, "
        "модель без роли персоны описала каждое изображение (что видит: продукт, "
        "текст на макете, ключевые элементы) и сверила описание с ключевым "
        "различающим элементом варианта (`key_element`, study.yaml), если он был "
        "задан; полный разбор по каждому варианту — [00_vision_check.md](00_vision_check.md). "
        "Это проверка «панель тестировала макет, а не собственную галлюцинацию о нём», "
        "не оценка дизайна."
    )


def compute_vision_check_failed_banner(vision_verdict: Optional[dict]) -> str:
    """
    {{VISION_CHECK_FAILED_BANNER}} (report_template.md, п.12) — СРАЗУ под
    {{CONTROLS_FAILED_BANNER}} (тот же контракт-приём: пустая строка при
    отсутствии провала пробы зрения; короткий абзац-предупреждение при
    vision_failed=true). Контракт-маркер для будущего линтера: точная фраза
    "проба зрения не пройдена" ОБЯЗАНА присутствовать при срабатывании — не
    сокращать/переформулировать (аналог "прогон не прошёл самоконтроль" выше).
    """
    if not vision_verdict or not vision_verdict.get("vision_failed"):
        return ""
    failed = ", ".join(vision_verdict.get("failed_stimulus_ids", []))
    confirmed_note = (
        "продолжено по явному подтверждению (confirmed_despite_failures)"
        if vision_verdict.get("confirmed_despite_failures")
        else "ВНИМАНИЕ: подтверждения продолжения не было"
    )
    return (
        f"> **проба зрения не пройдена для стимулов: {failed}.** Ключевой различающий "
        "элемент не распознан в объективном описании изображения — есть риск, что "
        f"персона реагировала на непрочитанный/нечитаемый макет ({confirmed_note}). "
        "Выводы по ЭТИМ стимулам использовать с осторожностью — см. «Приложение»/"
        "00_vision_check.md."
    )


def resolve_vision_verdict(run_dir: Path, manifest: dict) -> Optional[dict]:
    """
    §1.2 v1.4 — читает АКТУАЛЬНЫЙ вердикт пробы зрения для --stage report:
    пересчитывает свежий вердикт прямо из 00_vision_check.yaml, если файл
    существует и уже полностью заполнен (самолечение на случай правки файла
    МЕЖДУ стадиями — например, confirmed_despite_failures проставили ПОСЛЕ
    generate, до report); иначе — то, что уже зафиксировано в
    manifest["vision_check"] (generate/score стадией); None — study без
    визуальных стимулов вовсе. В ОТЛИЧИЕ от manifest["controls"] (см.
    load_or_init_manifest) вердикт пробы зрения НЕ завязан на seed/случайность —
    пересчёт из файла на каждой стадии безопасен (не нужно "замораживать").
    """
    vc_path = run_dir / VISION_CHECK_YAML_NAME
    if vc_path.exists():
        vc = load_vision_check(run_dir)
        if vc is not None and not vision_check_is_pending(vc):
            return compute_vision_verdicts(vc)
    return manifest.get("vision_check")


def run_report_stage(
    run_dir: Path,
    study: dict,
    segments: dict[str, dict],
    scale_name_ru: str,
    scale_id: str,
    config: dict,
    manifest: dict,
    skill_root: Path,
) -> None:
    seg_csv_path = run_dir / "pmf_by_segment.csv"
    resp_csv_path = run_dir / "pmf_by_respondent.csv"
    for required_path in (seg_csv_path, resp_csv_path):
        if not required_path.exists():
            print(f"ОШИБКА: {required_path} не найден — сначала выполните --stage score.", file=sys.stderr)
            sys.exit(1)

    # §1.4: разблокировка id (identity-маппинг, если controls отключены/прогон до
    # v1.3 — см. unblind_rows) + разделение на реальные стимулы study.yaml и
    # служебные строки плацебо/ловушки (см. split_real_and_control_rows).
    controls_manifest = manifest.get("controls")
    real_stimulus_ids = {s["id"] for s in study["stimuli"]}

    all_seg_rows = unblind_rows(report.read_pmf_by_segment(seg_csv_path), controls_manifest)
    all_resp_rows = unblind_rows(report.read_pmf_by_respondent(resp_csv_path), controls_manifest)
    sample_csv_path = run_dir / "pmf_by_sample.csv"
    # pmf_by_sample.csv — НОВЫЙ артефакт v1.3 (см. run_score_stage); прогоны,
    # пересчитанные СТАРЫМ score-кодом (до этой правки) или ещё не пересчитанные,
    # его не имеют — graceful degradation: пустой список, §1.3.2/1.3.4 для такого
    # прогона честно покажут "неприменимо"/"недоступны", не упадут.
    all_sample_rows = (
        unblind_rows(report.read_pmf_by_sample(sample_csv_path), controls_manifest)
        if sample_csv_path.exists()
        else []
    )

    rows, _control_seg_rows = split_real_and_control_rows(all_seg_rows, real_stimulus_ids)
    resp_rows, _control_resp_rows = split_real_and_control_rows(all_resp_rows, real_stimulus_ids)
    sample_rows, _control_sample_rows = split_real_and_control_rows(all_sample_rows, real_stimulus_ids)

    report_cfg = config.get("report", {})
    bootstrap_iters = int(report_cfg.get("bootstrap_iters", 1000))
    bootstrap_seed = int(report_cfg.get("seed", 42))

    controls_verdict = report.compute_controls_verdict(
        all_segment_rows=all_seg_rows,
        all_resp_rows=all_resp_rows,
        controls_manifest=controls_manifest or {"enabled": False},
        segments=list(study["segments"]),
        bootstrap_iters=bootstrap_iters,
        seed=bootstrap_seed,
    )
    sibling_rankings_by_segment = find_sibling_rankings(run_dir, study["name"], real_stimulus_ids)

    mode = compute_run_mode(manifest, controls_verdict)
    mode_badge = MODE_BADGE_RU[mode]
    controls_status_line = compute_controls_status_line(controls_manifest, controls_verdict)
    controls_failed_banner = compute_controls_failed_banner(controls_verdict)

    # §1.1/1.2 v1.4: stimulus_kind/vision_verdict — фолбэк "text"/None для
    # прогонов до v1.4 (manifest без этих полей), см. report_template.md
    # "ЧТО МЕНЯЕТСЯ ДЛЯ REPORT.PY (v1.4...)" п.10/13.
    stimulus_kind = manifest.get("stimulus_kind", "text")
    vision_verdict = resolve_vision_verdict(run_dir, manifest)
    if vision_verdict is not None:  # не засорять manifest пустым полем у текстовых study
        manifest["vision_check"] = vision_verdict

    gen_stage = manifest.get("stages", {}).get("generate", {})
    score_stage = manifest.get("stages", {}).get("score", {})

    provider = gen_stage.get("provider", config.get("llm", {}).get("provider", "agent"))
    model_id = gen_stage.get("model")
    agent_self_report = manifest.get("agent_self_report")
    if model_id:
        model_display = model_id
    elif agent_self_report and agent_self_report.get("model"):
        # §1.5 (фикс Д4): самодекларация модели агента, ведущего скилл в agent-режиме
        # (--agent-model, см. main()) — явно помечена как САМОдекларация, не
        # API-подтверждение (в отличие от model_id выше — тот приходит от реального
        # ответа anthropic/openai/gigachat API).
        model_display = f"{agent_self_report['model']} (самодекларация модели-агента, self_reported=true)"
    else:
        model_display = "не зафиксирована (agent-режим, temperature_control=false — см. AGENT_TASK.md)"

    n_segments = len(study["segments"])
    n_stimuli = len(study["stimuli"])
    # ВАЖНО: respondents_per_segment читаем из manifest, а НЕ заново из study.yaml — если
    # генерация была запущена с --respondents-per-segment (override только для прогона,
    # см. main()), а --stage report вызван ОТДЕЛЬНЫМ процессом без этого флага, свежая
    # study["respondents_per_segment"] окажется значением по умолчанию из файла, а не тем,
    # что реально было сгенерировано — числа в шапке отчёта разойдутся с фактическим N.
    # manifest["respondents_per_segment"] зафиксирован один раз в load_or_init_manifest()
    # именно с учётом override и не меняется между стадиями/процессами.
    respondents_per_segment = int(manifest.get("respondents_per_segment", study["respondents_per_segment"]))
    samples_per_respondent = int(manifest.get("samples_per_respondent", 2))
    n_respondents_total = n_segments * respondents_per_segment
    n_responses_total = score_stage.get(
        "n_responses_scored", n_respondents_total * n_stimuli * samples_per_respondent
    )
    segment_names_list = ", ".join(segments.get(sid, {}).get("name", sid) for sid in study["segments"])

    header_mapping = {
        "STUDY_NAME": study["name"],
        "STUDY_TYPE": study["type"],
        "RUN_DATE": manifest.get("created_at", "—"),
        "PROVIDER_MODE": provider,
        "MODEL_ID": model_display,
        "EMBEDDING_MODEL_ID": score_stage.get("embedding_model", config.get("embedding", {}).get("model", "—")),
        "ANCHORS_VERSION": str(manifest.get("anchors_version", 1)),
        # v1.3 §1.6: терминология "респонденты" запрещена в клиентском слое —
        # переименовано в N_PROFILES_TOTAL/PROFILES_PER_SEGMENT (report_template.md
        # "ЧТО МЕНЯЕТСЯ ДЛЯ REPORT.PY", п.2); то же число, то же вычисление.
        "N_PROFILES_TOTAL": str(n_respondents_total),
        "PROFILES_PER_SEGMENT": str(respondents_per_segment),
        "SAMPLES_PER_RESPONDENT": str(samples_per_respondent),
        "N_RESPONSES": str(n_responses_total),
        "N_SEGMENTS": str(n_segments),
        "N_STIMULI": str(n_stimuli),
        "SEGMENT_NAMES_LIST": segment_names_list,
        "SCALE_NAME_RU": scale_name_ru,
        "SCALE_ID": scale_id,
        "MANIFEST_FILENAME": "manifest.json",
        "MANIFEST_PATH": "manifest.json",
        "MODE": mode,
        "MODE_BADGE": mode_badge,
        "CONTROLS_STATUS_LINE": controls_status_line,
        "CONTROLS_FAILED_BANNER": controls_failed_banner,
        # v1.4 §1.1/1.2 (report_template.md, п.10-13, [B3]/[B1] — см. комментарий
        # у resolve_vision_verdict/compute_stimulus_kind_line выше за контрактом):
        "STIMULUS_KIND_LINE": compute_stimulus_kind_line(stimulus_kind, vision_verdict),
        "STIMULUS_KIND": stimulus_kind,
        "VISION_CHECK_SECTION": compute_vision_check_section(vision_verdict),
        "VISION_CHECK_STATUS_LINE": compute_vision_check_status_line(vision_verdict),
        "VISION_CHECK_FAILED_BANNER": compute_vision_check_failed_banner(vision_verdict),
        # report_template.md (v1.3) содержит иллюстративный абзац "Пример
        # `{{ASCII_BAR}}` (не обязателен побуквенно...)" СРАЗУ ПОСЛЕ
        # <!-- APPENDIX_TABLE_END --> (вне маркера, а не внутри него, как было в
        # v1.2 - похоже на редакторский пропуск при переносе секции в
        # "Приложение", см. итоговое сообщение агента). Раз он живой текст (не в
        # HTML-комментарии) - подставляем реальный пример, чтобы не оставлять
        # {{ASCII_BAR}} видимым в клиентском report.md; не трогаем сам файл
        # шаблона (не наша зона).
        "ASCII_BAR": "▁▂▇▄▁",
    }

    template_path = skill_root / "references" / "report_template.md"
    disclaimers_path = skill_root / "references" / "disclaimers.md"

    report_path = report.write_report(
        run_dir,
        template_path=template_path,
        disclaimers_path=disclaimers_path,
        rows=rows,
        resp_rows=resp_rows,
        sample_rows=sample_rows,
        study=study,
        segments=segments,
        scale_name_ru=scale_name_ru,
        scale_id=scale_id,
        header_mapping=header_mapping,
        bootstrap_iters=bootstrap_iters,
        bootstrap_seed=bootstrap_seed,
        controls_verdict=controls_verdict,
        sibling_rankings_by_segment=sibling_rankings_by_segment,
        vision_verdict=vision_verdict,
    )

    # §1.5: manifest.json ВСЕГДА содержит (к концу --stage report) mode/model —
    # верхнеуровневые контрактные поля (embedding_model/anchors_version уже
    # проставлены раньше — см. run_score_stage/main()).
    manifest["mode"] = mode
    manifest["model"] = model_display
    manifest["controls_verdict"] = controls_verdict
    manifest.setdefault("stages", {})["report"] = {
        "report_path": str(report_path),
        "mode": mode,
        "completed_at": now_iso(),
    }
    print(f"-- report: {report_path} (mode={mode})")
    if controls_verdict.get("controls_failed"):
        print(
            "== ВНИМАНИЕ: негативные контроли §1.4 НЕ пройдены (controls_failed=true) — "
            "отчёт собран, но помечен красной плашкой; выводы использовать нельзя без "
            "разбора причины провала (см. report.md, раздел 1). ==",
            file=sys.stderr,
        )


# ============================================================================
# main
# ============================================================================


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="CLI-оркестратор стадий synthetic-panel (spec §6).")
    p.add_argument("--study", required=True, help="Путь к study.yaml, например studies/demo_claims.yaml")
    p.add_argument("--stage", choices=["all", "generate", "score", "report"], default="all")
    p.add_argument(
        "--run-dir",
        default=None,
        help="Явно указать директорию прогона (обязательно для --stage score/report, "
        "если прогонов этого study несколько; см. AGENT_TASK.md за подсказкой).",
    )
    p.add_argument("--config", default=None, help="Путь к config.yaml (по умолчанию <корень скилла>/config.yaml)")
    p.add_argument(
        "--respondents-per-segment",
        type=int,
        default=None,
        help="Override study.yaml: respondents_per_segment для ЭТОГО прогона (не меняет файл study.yaml "
        "на диске; фактическое значение фиксируется в manifest.json — удобно для быстрых smoke-демо).",
    )
    p.add_argument(
        "--agent-model",
        default=None,
        help="Самоидентификация модели-агента в agent-режиме (§1.5 spec_synthetic-panel_v1.3.md, "
        "фикс Д4): передайте имя/версию модели ТЕКУЩЕЙ сессии, ведущей скилл (например "
        "'claude-sonnet-5'). Сохраняется в manifest.json как agent_self_report "
        "{model, self_reported: true} — это САМОдекларация, а не API-подтверждение; можно "
        "передавать на любой стадии, перезаписывает предыдущее значение этого же прогона.",
    )
    return p


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_arg_parser().parse_args()

    skill_root = Path(__file__).resolve().parent.parent

    config_path = Path(args.config) if args.config else skill_root / "config.yaml"
    if not config_path.exists():
        print(f"ОШИБКА: config.yaml не найден: {config_path}", file=sys.stderr)
        sys.exit(1)
    config = load_yaml(config_path)

    study_path = Path(args.study)
    if not study_path.exists():
        alt = skill_root / args.study
        if alt.exists():
            study_path = alt
        else:
            print(f"ОШИБКА: study.yaml не найден: {args.study}", file=sys.stderr)
            sys.exit(1)
    study = load_yaml(study_path)

    validate_study_schema(study, study_path)
    validate_study_type(study)

    # §1.1 v1.4: схема/валидация визуальных стимулов (image/label/key_element) —
    # мутирует study["stimuli"] IN PLACE (image -> абсолютный разрешённый путь),
    # см. validate_and_resolve_stimuli. Печатается ОДИН раз здесь на каждый вызов
    # CLI (не только при первой инициализации run_dir) — предупреждение о
    # непараллельности полезно видеть на любой стадии, не только на generate.
    try:
        stimuli_info = validate_and_resolve_stimuli(study, study_path, skill_root)
    except ValueError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        sys.exit(1)
    if stimuli_info.get("image_parallelism_warning"):
        print(f"-- ПРЕДУПРЕЖДЕНИЕ (§1.1 непараллельность изображений): {stimuli_info['image_parallelism_warning']}")

    if args.respondents_per_segment is not None:
        print(
            f"-- Override: respondents_per_segment {study['respondents_per_segment']} -> "
            f"{args.respondents_per_segment} (только для этого прогона, зафиксировано в manifest.json)"
        )
        study["respondents_per_segment"] = int(args.respondents_per_segment)

    segments = load_segments(study["segments"], skill_root)

    anchors_path = skill_root / "references" / "anchors_ru.yaml"
    try:
        question, anchor_sets = ssr_core.load_anchor_sets(anchors_path, study["question_scale"])
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        sys.exit(1)

    anchors_raw = load_yaml(anchors_path)
    scale_meta = (anchors_raw.get("scales", {}) or {}).get(study["question_scale"], {})
    scale_name_ru = scale_meta.get("name_ru", study["question_scale"])

    run_dir = resolve_run_dir(args.run_dir, skill_root, study["name"], args.stage)
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_or_init_manifest(run_dir, study, config, study_path, skill_root, stimuli_info)

    # §1.5: anchors_version — верхнеуровневый контрактный manifest-field, проставляется
    # на КАЖДОМ вызове (дёшево, читаем то, что уже загрузили выше). Толерантно к месту,
    # где [B2] хранит версию в anchors_ru.yaml: meta.anchors_version (новое имя),
    # meta.version (старое имя, spec v1/v1.2) — если ни того ни другого нет, честный
    # дефолт 1 (файл до версионирования).
    anchors_meta = anchors_raw.get("meta", {}) or {}
    manifest["anchors_version"] = anchors_meta.get("anchors_version", anchors_meta.get("version", 1))

    if args.agent_model:
        manifest["agent_self_report"] = {
            "model": args.agent_model,
            "self_reported": True,
            "recorded_at": now_iso(),
        }
        print(f"-- Самоидентификация модели-агента записана в manifest.json: {args.agent_model!r}")

    if args.stage in ("all", "generate"):
        outcome = run_generate_stage(run_dir, study, config, segments, question, study_path, manifest)
        save_manifest(run_dir, manifest)
        if outcome.status == "todo":
            print(
                "\n== Agent-режим: ответы нужно заполнить вручную ==\n"
                f"1. Прочитайте инструкцию: {run_dir / 'AGENT_TASK.md'}\n"
                f"2. Заполните {run_dir / 'responses_todo.jsonl'} -> сохраните как "
                f"{run_dir / 'responses.jsonl'}\n"
                f"3. Продолжите: python scripts/run_study.py --study {args.study} --stage score "
                f"--run-dir {run_dir}\n"
            )
            sys.exit(2)
        print(f"-- generate: готово ({outcome.n_tasks} ответов, provider={outcome.provider})")
        if args.stage == "generate":
            print(f"\nГотово. Директория прогона: {run_dir}")
            return

    if args.stage in ("all", "score"):
        run_score_stage(run_dir, config, anchor_sets, manifest)
        save_manifest(run_dir, manifest)
        if args.stage == "score":
            print(f"\nГотово. Директория прогона: {run_dir}")
            return

    if args.stage in ("all", "report"):
        run_report_stage(run_dir, study, segments, scale_name_ru, study["question_scale"], config, manifest, skill_root)
        save_manifest(run_dir, manifest)

    print(f"\nГотово. Директория прогона: {run_dir}")


if __name__ == "__main__":
    main()

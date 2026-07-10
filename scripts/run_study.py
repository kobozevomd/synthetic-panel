#!/usr/bin/env python3
"""
run_study.py — CLI-оркестратор стадий synthetic-panel (spec_synthetic-panel_v1.md §6).

    python scripts/run_study.py --study studies/<name>.yaml --stage all|generate|score|report [--run-dir DIR]

Стадии пишут в runs/<study_name>_<YYYYMMDD-HHMM>/ (или в --run-dir, если указан):
    responses_todo.jsonl, AGENT_TASK.md   — только agent-режим, стадия generate
    responses.jsonl                       — все режимы, стадия generate
    pmf_by_respondent.csv, pmf_by_segment.csv — стадия score
    report.md                             — стадия report
    manifest.json                         — обновляется на каждой стадии (модель,
                                             версия эмбеддера, temperature, seed,
                                             промпт-контракт — см. §0.5)

Валидация study.yaml: `type` должен быть в ALLOWED_STUDY_TYPES, иначе — отказ с
объяснением про красную зону метода (см. validate_study_type).

Agent-режим (llm.provider == agent, дефолт config.yaml): стадия generate пишет
responses_todo.jsonl + AGENT_TASK.md и НЕ вызывает никакую LLM (см. generate.py).
`--stage all`/`--stage generate` в этом случае останавливается с кодом выхода 2 и
инструкцией, что делать дальше — заполнить responses.jsonl по AGENT_TASK.md и
продолжить `--stage score --run-dir <тот же run_dir>`. Если responses.jsonl уже
заполнен (агент вернулся в ту же run_dir) — повторный вызов generate не
перезаписывает его поверх, а пропускает генерацию todo (см. run_generate_stage).

Загрузчик сегментов (см. build_segment_index/resolve_segment_path/load_segments,
добавлено в v1.1 для режима segment_map — spec_synthetic-panel_v1.1_segment_map.md
§3): panel/segments/**/*.yaml индексируется РЕКУРСИВНО по имени файла (stem), а
не только по плоскому panel/segments/{sid}.yaml — так работают и старые 7
плоских кофейных сегментов, и новые вложенные по категориям
panel/segments/<category_slug>/<id>.yaml (см. scripts/segments_export.py).
Совпадение stem в РАЗНЫХ папках дерева — явная ошибка (exit 1), не молчаливый
выбор первого найденного пути.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
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


def load_or_init_manifest(run_dir: Path, study: dict, config: dict, study_path: Path) -> dict:
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    samples_per_respondent = int(
        study.get("samples_per_respondent") or config.get("llm", {}).get("samples_per_respondent", 2)
    )
    return {
        "study_name": study["name"],
        "study_path": str(study_path),
        "study_type": study["type"],
        "question_scale": study["question_scale"],
        "segments": list(study["segments"]),
        "stimuli": [{"id": s["id"], "text": s["text"]} for s in study["stimuli"]],
        "respondents_per_segment": int(study["respondents_per_segment"]),
        "samples_per_respondent": samples_per_respondent,
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
        outcome = generate.generate_responses(study, config, segments, question, run_dir, str(study_path))

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

    print(f"-- score: готово -> {resp_csv_path.name}, {seg_csv_path.name}")

    manifest.setdefault("stages", {})["score"] = {
        "embedding_model": embedding_model,
        "embedding_device": emb_cfg.get("device", "cpu"),
        "embedding_prefix": emb_cfg.get("prefix", ""),
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
    if not seg_csv_path.exists():
        print(f"ОШИБКА: {seg_csv_path} не найден — сначала выполните --stage score.", file=sys.stderr)
        sys.exit(1)

    rows = report.read_pmf_by_segment(seg_csv_path)

    gen_stage = manifest.get("stages", {}).get("generate", {})
    score_stage = manifest.get("stages", {}).get("score", {})

    provider = gen_stage.get("provider", config.get("llm", {}).get("provider", "agent"))
    model_id = gen_stage.get("model")
    model_display = model_id if model_id else "не зафиксирована (agent-режим, temperature_control=false — см. AGENT_TASK.md)"

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

    header_mapping = {
        "STUDY_NAME": study["name"],
        "STUDY_TYPE": study["type"],
        "RUN_DATE": manifest.get("created_at", "—"),
        "PROVIDER_MODE": provider,
        "MODEL_ID": model_display,
        "EMBEDDING_MODEL_ID": score_stage.get("embedding_model", config.get("embedding", {}).get("model", "—")),
        "N_RESPONDENTS": str(n_respondents_total),
        "RESPONDENTS_PER_SEGMENT": str(respondents_per_segment),
        "SAMPLES_PER_RESPONDENT": str(samples_per_respondent),
        "N_RESPONSES": str(n_responses_total),
        "N_SEGMENTS": str(n_segments),
        "N_STIMULI": str(n_stimuli),
        "MANIFEST_FILENAME": "manifest.json",
        "MANIFEST_PATH": "manifest.json",
    }

    template_path = skill_root / "references" / "report_template.md"
    disclaimers_path = skill_root / "references" / "disclaimers.md"

    report_path = report.write_report(
        run_dir,
        template_path=template_path,
        disclaimers_path=disclaimers_path,
        rows=rows,
        study=study,
        segments=segments,
        scale_name_ru=scale_name_ru,
        scale_id=scale_id,
        header_mapping=header_mapping,
    )

    manifest.setdefault("stages", {})["report"] = {
        "report_path": str(report_path),
        "completed_at": now_iso(),
    }
    print(f"-- report: {report_path}")


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
    manifest = load_or_init_manifest(run_dir, study, config, study_path)

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

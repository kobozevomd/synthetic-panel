#!/usr/bin/env python3
"""
embedder_ab.py — сравнение эмбеддеров для SSR по гейту §1.2 п.2 (spec_synthetic-panel_v1.3.md).

Сравнивает МИНИМУМ три эмбеддера на якорях v2 (references/anchors_ru.yaml) теми же
метриками, что и test_anchors.py (leave-one-set-out монотонность, E(5)>E(4), rank-recovery):

  1. paraphrase-multilingual-MiniLM-L12-v2 — текущий (config.yaml), без префикса.
  2. intfloat/multilingual-e5-large-instruct — с ЕДИНЫМ инструктивным префиксом на
     ОБЕ роли (ответ и якорь), см. EMBEDDER_CANDIDATES ниже. Это "префикс из конфига"
     в терминах спецификации — config.yaml/ssr_core.SentenceTransformerBackend
     поддерживают ровно один uniform-префикс на все тексты, поэтому кандидат
     тестируется в ТОЙ ЖЕ схеме, в которой он реально будет работать в продакшне.
  3. ai-forever/ru-en-RoSBERTa — модель документирует РАЗНЫЕ префиксы для роли
     "запрос" (search_query:) и "документ" (search_document:), а не единый префикс.
     Тестируется в ДВУХ вариантах:
       3a. асимметрично (search_query на ответах, search_document на якорях) —
           даёт представление о потолке качества модели, НО текущий ssr_core.py
           не умеет применять разные префиксы к разным ролям — выбор этого варианта
           потребует правки ssr_core.py (зона B1, "при необходимости", см. issues);
       3b. единым префиксом "classification: " на обе роли (сама модель называет его
           рекомендацией для симметричных задач — STS/paraphrase/NLI, что ближе к
           природе SSR, чем асимметричный retrieval) — эта схема совместима с
           ТЕКУЩИМ ssr_core.py без единой правки кода, как и (1) и (2).

Использует ЯДРО test_anchors.py (evaluate_gate/format_report/PARAPHRASE_BANK) —
не дублирует логику гейта, только оборачивает разные embedding-бэкенды.

Запуск:
    python scripts/embedder_ab.py                     # все кандидаты, полный отчёт в stdout
    python scripts/embedder_ab.py --only minilm        # один кандидат (для отладки/скорости)
    python scripts/embedder_ab.py --markdown out.md    # плюс готовый markdown-отчёт в файл

Модели скачиваются с HuggingFace при первом запуске и кэшируются в venv
(см. spec_synthetic-panel_v1.3.md §1.2 п.3) — e5-large и RoSBERTa первый раз могут
занять несколько минут.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

_SCRIPTS_DIR = Path(__file__).resolve().parent
_SKILL_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import ssr_core  # noqa: E402
import test_anchors as ta  # noqa: E402


# ============================================================================
# Бэкенд с общим кэшем модели на процесс (нужен для асимметричных префиксов
# RoSBERTa: query-бэкенд и document-бэкенд грузят ОДНУ и ту же модель один раз)
# ============================================================================


class RoleAwareBackend(ssr_core.EmbeddingBackend):
    """
    Как ssr_core.SentenceTransformerBackend, но с process-wide кэшем загруженной
    модели по (model_name, device) — чтобы протестировать РАЗНЫЕ префиксы на одной
    и той же (потенциально тяжёлой) модели без повторной загрузки весов.
    """

    _model_cache: dict[tuple[str, str], object] = {}

    def __init__(self, model_name: str, device: str = "cpu", prefix: str = ""):
        from sentence_transformers import SentenceTransformer  # ленивый импорт, как в ssr_core

        key = (model_name, device)
        if key not in RoleAwareBackend._model_cache:
            RoleAwareBackend._model_cache[key] = SentenceTransformer(model_name, device=device)
        self._model = RoleAwareBackend._model_cache[key]
        self.model_name = model_name
        self.device = device
        self.prefix = prefix or ""

    def encode(self, texts):
        prefixed = [f"{self.prefix}{t}" for t in texts] if self.prefix else list(texts)
        embeddings = self._model.encode(
            prefixed, normalize_embeddings=False, show_progress_bar=False, convert_to_numpy=True
        )
        return np.asarray(embeddings, dtype=np.float64)


# ============================================================================
# Кандидаты
# ============================================================================


@dataclass
class Candidate:
    key: str
    display_name: str
    model_name: str
    response_prefix: str  # применяется к "ответам" (held-out якорным фразам, парафразам)
    anchor_prefix: str  # применяется к "эталонным" якорным фразам (документам)
    note: str = ""
    production_compatible: bool = True  # совместим с текущим ssr_core.py (uniform-префикс)?


# Единый инструктивный префикс для e5-large-instruct — ОДИН и тот же на обе роли
# (config.yaml поддерживает только uniform-префикс; см. модульный докстринг выше).
# Формулировка задачи — по образцу FAQ карточки модели ("Instruct: {task}\nQuery: {text}"),
# обобщённая на симметричную задачу (сама карточка НЕ документирует симметричный
# случай явно — это осознанное расширение по аналогии с общей практикой e5, см.
# docs/embedder_ab_v13.md, раздел "Оговорки").
E5_INSTRUCT_PREFIX = (
    "Instruct: Given a person's statement of opinion or intention, retrieve a reference "
    "statement that expresses the same degree of that opinion or intention\nQuery: "
)

EMBEDDER_CANDIDATES: list[Candidate] = [
    Candidate(
        key="minilm",
        display_name="paraphrase-multilingual-MiniLM-L12-v2 (текущий)",
        model_name="paraphrase-multilingual-MiniLM-L12-v2",
        response_prefix="",
        anchor_prefix="",
        note="Дефолт config.yaml до этой итерации. Без префикса.",
    ),
    Candidate(
        key="e5-large-instruct",
        display_name="intfloat/multilingual-e5-large-instruct (uniform instruct-префикс)",
        model_name="intfloat/multilingual-e5-large-instruct",
        response_prefix=E5_INSTRUCT_PREFIX,
        anchor_prefix=E5_INSTRUCT_PREFIX,
        note="Единый префикс на обе роли — совместимо с config.yaml/ssr_core.py как есть.",
    ),
    Candidate(
        key="rosberta-asymmetric",
        display_name="ai-forever/ru-en-RoSBERTa (search_query / search_document, асимметрично)",
        model_name="ai-forever/ru-en-RoSBERTa",
        response_prefix="search_query: ",
        anchor_prefix="search_document: ",
        note="Официальная схема retrieval-префиксов модели. ТРЕБУЕТ правки ssr_core.py "
        "(поддержка разных префиксов для роли ответа/якоря) для продакшн-использования.",
        production_compatible=False,
    ),
    Candidate(
        key="rosberta-uniform",
        display_name="ai-forever/ru-en-RoSBERTa (classification:, uniform)",
        model_name="ai-forever/ru-en-RoSBERTa",
        response_prefix="classification: ",
        anchor_prefix="classification: ",
        note="Префикс модели для симметричных задач (STS/paraphrase/NLI) — ближе к природе "
        "SSR, чем retrieval, и совместим с config.yaml как есть (uniform).",
    ),
]


@dataclass
class CandidateRunResult:
    candidate: Candidate
    report: "ta.GateReport"
    load_and_eval_seconds: float
    error: Optional[str] = None


def run_candidate(candidate: Candidate, anchors_path, ssr_cfg: dict, verbose: bool = False) -> CandidateRunResult:
    t0 = time.monotonic()
    try:
        response_backend = RoleAwareBackend(candidate.model_name, device="cpu", prefix=candidate.response_prefix)
        anchor_backend = (
            response_backend
            if candidate.response_prefix == candidate.anchor_prefix
            else RoleAwareBackend(candidate.model_name, device="cpu", prefix=candidate.anchor_prefix)
        )
        report = ta.evaluate_gate(
            anchors_path=anchors_path,
            response_backend=response_backend,
            anchor_backend=anchor_backend,
            epsilon=ssr_cfg["epsilon"],
            pmf_temperature=ssr_cfg["pmf_temperature"],
            min_anchor_sets=ssr_cfg["min_anchor_sets"],
            model_name=candidate.model_name,
            prefix=f"response={candidate.response_prefix!r} | anchor={candidate.anchor_prefix!r}",
        )
        elapsed = time.monotonic() - t0
        if verbose:
            print(ta.format_report(report, verbose=True))
            print(f"(время загрузки модели + вычисления: {elapsed:.1f} с)")
        return CandidateRunResult(candidate=candidate, report=report, load_and_eval_seconds=elapsed)
    except Exception as exc:  # noqa: BLE001 — сравнение не должно падать на одном кандидате
        elapsed = time.monotonic() - t0
        print(f"ОШИБКА на кандидате {candidate.key}: {exc}", file=sys.stderr)
        empty_report = ta.GateReport(model_name=candidate.model_name)
        return CandidateRunResult(candidate=candidate, report=empty_report, load_and_eval_seconds=elapsed, error=str(exc))


# ============================================================================
# Сводная таблица + markdown
# ============================================================================


def summarize(results: list[CandidateRunResult]) -> str:
    lines = ["", "=" * 78, "СВОДНАЯ ТАБЛИЦА embedder_ab.py", "=" * 78]
    header = f"{'кандидат':45s} {'шкала':15s} {'монотон.':9s} {'E4':>6s} {'E5':>6s} {'rank-rec':>9s} {'PASS':>5s}"
    lines.append(header)
    for r in results:
        if r.error:
            lines.append(f"{r.candidate.key:45s} ОШИБКА: {r.error}")
            continue
        for scale_id, sr in r.report.scale_results.items():
            lines.append(
                f"{r.candidate.key:45s} {scale_id:15s} {sr.n_monotonic}/{sr.n_sets:<7d} "
                f"{sr.mean_e4:6.3f} {sr.mean_e5:6.3f} "
                f"{'PASS' if sr.rank_recovery.monotonic else 'FAIL':>9s} {'PASS' if sr.passed else 'FAIL':>5s}"
            )
        lines.append(
            f"{r.candidate.key:45s} {'ОБЩИЙ ВЕРДИКТ':15s} {'':9s} {'':>6s} {'':>6s} {'':>9s} "
            f"{'PASS' if r.report.passed else 'FAIL':>5s}   ({r.load_and_eval_seconds:.1f} с)"
        )
        lines.append("-" * 78)
    return "\n".join(lines)


def to_markdown(results: list[CandidateRunResult]) -> str:
    lines = [
        "# A/B сравнение эмбеддеров SSR — v1.3 (spec_synthetic-panel_v1.3.md §1.2 п.3)",
        "",
        "Автоматически сгенерировано `scripts/embedder_ab.py` на якорях v2 "
        "(`references/anchors_ru.yaml`). Гейт — `scripts/test_anchors.py` "
        "(leave-one-set-out монотонность, среднее E(5)>E(4), rank-recovery на held-out "
        "парафразах). Пороги приёмки — §1.2 п.2: >=3/4 монотонных наборов на шкалу И "
        "E(5)>E(4) на всех шкалах И rank-recovery на всех шкалах.",
        "",
        "## Сводная таблица по шкалам",
        "",
        "| Кандидат | Шкала | Монотонных наборов | E(4) | E(5) | Rank-recovery | Шкала: PASS/FAIL |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.error:
            lines.append(f"| {r.candidate.display_name} | — | — | — | — | — | ОШИБКА: {r.error} |")
            continue
        for scale_id, sr in r.report.scale_results.items():
            lines.append(
                f"| {r.candidate.display_name} | {scale_id} | {sr.n_monotonic}/{sr.n_sets} "
                f"| {sr.mean_e4:.3f} | {sr.mean_e5:.3f} "
                f"| {'PASS' if sr.rank_recovery.monotonic else 'FAIL'} "
                f"| {'PASS' if sr.passed else 'FAIL'} |"
            )
    lines += ["", "## Общий вердикт гейта по кандидату", "", "| Кандидат | Вердикт | Время загрузки+расчёта |", "|---|---|---|"]
    for r in results:
        verdict = f"ОШИБКА: {r.error}" if r.error else ("PASS" if r.report.passed else "FAIL")
        lines.append(f"| {r.candidate.display_name} | {verdict} | {r.load_and_eval_seconds:.1f} с |")
    return "\n".join(lines)


# ============================================================================
# CLI
# ============================================================================


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchors", default=str(ta.DEFAULT_ANCHORS_PATH))
    parser.add_argument("--config", default=str(ta.DEFAULT_CONFIG_PATH))
    parser.add_argument(
        "--only", default=None, help="Ключ одного кандидата (см. EMBEDDER_CANDIDATES) — для отладки/скорости"
    )
    parser.add_argument("--markdown", default=None, help="Путь для сохранения markdown-таблиц")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    ssr_cfg = ta.load_ssr_config(args.config)
    candidates = EMBEDDER_CANDIDATES
    if args.only:
        candidates = [c for c in candidates if c.key == args.only]
        if not candidates:
            print(f"Неизвестный ключ кандидата: {args.only!r}", file=sys.stderr)
            return 2

    results = []
    for c in candidates:
        print(f"\n>>> Кандидат: {c.display_name} ({c.model_name})", file=sys.stderr)
        results.append(run_candidate(c, args.anchors, ssr_cfg, verbose=args.verbose))

    print(summarize(results))

    if args.markdown:
        Path(args.markdown).write_text(to_markdown(results), encoding="utf-8")
        print(f"\nMarkdown-таблицы сохранены: {args.markdown}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())

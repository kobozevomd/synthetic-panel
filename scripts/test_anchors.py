#!/usr/bin/env python3
"""
test_anchors.py — гейт монотонности якорных наборов SSR (spec_synthetic-panel_v1.3.md §1.2 п.2).

Чинит Д2 (docs/review найдены Fable+Codex, верифицировано оркестратором): русские якорные
наборы `references/anchors_ru.yaml` были монотонны только "на глаз" (см. docs/review_v1.md,
§3 — "прочитал и мысленно переставил силу каждой фразы") и НИКОГДА не проверялись реальным
вычислением через embedding-модель. Этот файл — тот самый расчёт, которого не хватало.

ВАЖНО, в отличие от test_ssr.py: этот файл требует sentence-transformers И реальную
embedding-модель (скачивание с HuggingFace при первом запуске — см. spec_synthetic-panel_v1.3.md
§1.2 п.3, "сеть до HuggingFace есть; модели кэшируются в venv"). test_ssr.py проверяет чистую
математику SSR на голых numpy-векторах и намеренно не трогает сеть — test_anchors.py проверяет
СЕМАНТИКУ конкретных русских фраз в конкретном embedding-пространстве, что структурно
невозможно без реальной модели. Первый прогон на новой модели может занять до нескольких минут
(скачивание + загрузка весов); повторные прогоны — из кэша, секунды-десятки секунд.

Два способа запуска:

  1) Регрессия (юнит-тест, "все тесты зелёные" из DoD spec_synthetic-panel_v1.3.md):
         python scripts/test_anchors.py
         python -m unittest scripts.test_anchors -v
     Тест использует эмбеддер, ЗАФИКСИРОВАННЫЙ в config.yaml (embedding.model/prefix/device) —
     то есть "выбранный стек" после этапа embedder_ab.py. Если config.yaml ещё указывает на
     эмбеддер, не прошедший гейт (например, до выбора победителя) — тест красный, это ожидаемо
     и есть сигнал "сборка не принята" (см. модульный докстринг спецификации, §1.2 п.2).

  2) CLI-диагностика на произвольном эмбеддере (нужна embedder_ab.py и ручной диагностике Д2):
         python scripts/test_anchors.py --model <hf-имя> --prefix "..." --device cpu -v
     Печатает таблицу E по уровням 1..5 для каждого набора каждой шкалы, вердикт монотонности,
     rank-recovery и общий вердикт гейта. Код возврата: 0 — гейт пройден, 1 — не пройден
     (пригодно для CI/скриптов).

Гейт (порог приёмки, spec_synthetic-panel_v1.3.md §1.2 п.2 — все условия через "И"):
  (a) Leave-one-set-out монотонность E по уровням, ОТДЕЛЬНО для каждого набора каждой шкалы:
      набор j временно исключается из "эталона"; его 5 фраз (уровни 1..5) скорятся как ОТВЕТЫ
      через штатный SSR-пайплайн (pmf_single_anchor_set + average_pmfs) на ОСТАВШИХСЯ наборах
      шкалы (не на себе — иначе тест тривиален: cosine(x,x)=1 всегда даёт "верный" максимум).
      Строгая монотонность — E(1)<E(2)<E(3)<E(4)<E(5) без исключений. Порог: >= 3 из 4 наборов
      монотонны НА КАЖДУЮ шкалу.
  (b) Среднее E(5) > среднее E(4) (усреднение по всем leave-one-out прогонам шкалы, п.(a)) —
      на ВСЕХ шкалах без исключения. Это отдельное, более узкое условие, чем (a): набор может
      быть "монотонным" в других парах уровней и всё равно проваливать именно переход 4->5
      (или наоборот, проходить (b) в среднем при отдельных немонотонных наборах) — Д2 бьёт
      именно по разлипанию 4/5, поэтому проверяется явно и отдельно от общей монотонности.
  (c) Rank-recovery: независимый held-out банк синтетических фраз-парафразов уровня k
      (PARAPHRASE_BANK ниже; ни одна фраза НЕ дублирует anchor_sets — иначе тест был бы
      циркулярным). Парафразы скорятся ПОЛНЫМ продакшн-пайплайном SSR (среднее по ВСЕМ
      наборам шкалы разом, как в ssr_core.SSREngine.score_texts — без leave-one-out, это тест
      на генерализацию за пределы обучающих фраз, а не на взаимную согласованность наборов).
      Среднее E по группе уровня k обязано строго возрастать k=1..5 на каждой шкале.

Гейт зелёный, только если (a) И (b) И (c) выполнены на ВСЕХ шкалах, найденных в файле якорей.
"""

from __future__ import annotations

import argparse
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

import numpy as np
import yaml

# Позволяет запускать файл напрямую (python scripts/test_anchors.py) независимо от cwd.
_SCRIPTS_DIR = Path(__file__).resolve().parent
_SKILL_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import ssr_core  # noqa: E402

DEFAULT_ANCHORS_PATH = _SKILL_ROOT / "references" / "anchors_ru.yaml"
DEFAULT_CONFIG_PATH = _SKILL_ROOT / "config.yaml"

LEVELS = (1, 2, 3, 4, 5)


# ============================================================================
# Held-out банк парафразов для rank-recovery (см. п.(c) в докстринге модуля)
# ============================================================================
#
# Написаны ЗАНОВО, без единого повторения фраз из anchor_sets (иначе rank-recovery
# был бы циркулярным — проверял бы "узнаёт ли модель фразу саму себя", а не
# "обобщается ли шкала на независимо сформулированный текст ответа"). Соблюдены те
# же грамматические требования, что и для anchor_sets (см. references/anchors_ru.yaml,
# преамбула): естественный русский, без брендов/категорий, без прошедшего времени
# смыслового глагола 1-го лица и кратких прилагательных, согласуемых с родом
# говорящего ("купил/купила", "готов/готова" и т.п.) — разрешены настоящее/будущее
# время и безличные конструкции ("нужно подумать", "решено", "зависит от...").
#
# Каждый уровень — 4 фразы разного лексического наполнения (не парафразы друг
# друга внутри уровня, а независимые формулировки одной и той же интенсивности).
PARAPHRASE_BANK: dict[str, dict[int, list[str]]] = {
    "purchase_intent": {
        1: [
            "Нет, это точно не то, на что я потрачу деньги.",
            "Даже случайно такое в корзину не положу.",
            "Ноль шансов, что это когда-нибудь окажется у меня дома.",
            "Мимо, совсем не тот случай, чтобы платить за это.",
        ],
        2: [
            "Скорее пройду мимо, но чисто теоретически бывает и передумаю.",
            "Не горю желанием, разве что случай подвернётся особый.",
            "Маловероятно, что дойдёт до кассы с этим в руках.",
            "Не сказать что совсем нет, но шансы небольшие.",
        ],
        3: [
            "Сложно сказать сразу, нужно ещё подумать над этим.",
            "50 на 50, зависит от настроения и от цены.",
            "Пока сложно определиться, нужно взвесить все за и против.",
            "Однозначного мнения пока нет, есть и за, и против.",
        ],
        4: [
            "В целом склоняюсь к покупке, хотя ещё есть пара вопросов.",
            "Скорее всего возьму, но сначала хочется сравнить с другими вариантами.",
            "Настрой положительный, скорее всего куплю, но чуть позже определюсь окончательно.",
            "Практически решено в пользу покупки, но маленькое сомнение остаётся.",
        ],
        5: [
            "Да, однозначно, беру прямо сейчас.",
            "Беру сразу, вопрос для меня уже закрыт.",
            "Это стопроцентное да, оформляю покупку сейчас же.",
            "Всё, решено — покупаю, и точка.",
        ],
    },
    "appeal": {
        1: [
            "Ничего не откликается, взгляд сразу скользит дальше.",
            "Совсем не моё, никакого желания смотреть повторно.",
            "Реакция нулевая, только пожимаю плечами.",
            "Отталкивает, даже не хочется задерживать взгляд.",
        ],
        2: [
            "Не особо цепляет, хотя что-то отдалённо симпатичное есть.",
            "Скорее равнодушно, чем с интересом смотрю на это.",
            "Слабенько, без огонька, но и не раздражает совсем.",
            "Так, ничего выдающегося, реакция сдержанная.",
        ],
        3: [
            "Ни восторга, ни разочарования — ровное отношение.",
            "Обычное дело, ничего особенного не откликается внутри.",
            "Смотрю спокойно, без явных эмоций в любую сторону.",
            "Пятьдесят на пятьдесят: что-то нравится, что-то не очень.",
        ],
        4: [
            "В целом симпатично, хотя кое-что смущает при первом взгляде.",
            "Приятное чувство возникает, но не без маленькой оговорки.",
            "Многое подкупает, только один нюанс слегка портит впечатление.",
            "Отклик скорее тёплый, чем прохладный, с небольшим но.",
        ],
        5: [
            "Восторг полный, влюбляюсь с первого взгляда.",
            "Это огонь, нравится безоговорочно и сразу.",
            "Абсолютный восторг, обожаю с первого мгновения.",
            "Однозначно да, эмоция сильнейшая и никакого но.",
        ],
    },
    "relevance": {
        1: [
            "Абсолютно чужая история, ко мне вообще не относится.",
            "Ничего общего с моей жизнью не нахожу совсем.",
            "Мимо на сто процентов, это не обо мне.",
            "Не узнаю себя тут ни в одной детали.",
        ],
        2: [
            "Скорее не моя ситуация, хотя что-то отдалённо знакомое мелькает.",
            "Мало пересечений с моей жизнью, разве что по мелочи.",
            "В основном не про меня, лишь пара деталей совпадает случайно.",
            "Слабое совпадение, больше похоже на чужую историю.",
        ],
        3: [
            "Отчасти похоже на мою ситуацию, отчасти нет вовсе.",
            "Наполовину моя история, наполовину что-то постороннее.",
            "Смотря с какой стороны посмотреть — где-то да, где-то нет.",
            "Есть и совпадения, и явные несовпадения с моей жизнью.",
        ],
        4: [
            "В основном это про меня, но пара моментов всё же не совпадает.",
            "Многое узнаю в этом, хотя не всё сходится один в один.",
            "Близко к моей истории, только детали местами расходятся.",
            "По большей части похоже на меня, с небольшой оговоркой.",
        ],
        5: [
            "Это стопроцентно про меня, вплоть до мелочей.",
            "Моя история один в один, точно про меня.",
            "Узнаю себя абсолютно во всём, прямо зеркало.",
            "Это точно обо мне, и вопрос тут закрыт.",
        ],
    },
}


# ============================================================================
# Результаты (dataclasses)
# ============================================================================


@dataclass
class SetMonotonicityResult:
    set_index: int
    label: str
    e_values: list[float]  # E для уровней 1..5, индекс 0..4
    violations: list[tuple[int, int]]  # пары уровней (i, i+1), где E НЕ строго возросло

    @property
    def monotonic(self) -> bool:
        return len(self.violations) == 0


@dataclass
class RankRecoveryResult:
    mean_e_by_level: list[float]  # индекс 0..4 = уровень 1..5
    violations: list[tuple[int, int]]

    @property
    def monotonic(self) -> bool:
        return len(self.violations) == 0


@dataclass
class ScaleGateResult:
    scale_id: str
    set_results: list[SetMonotonicityResult]
    rank_recovery: RankRecoveryResult
    min_monotonic_sets: int = 3

    @property
    def n_sets(self) -> int:
        return len(self.set_results)

    @property
    def n_monotonic(self) -> int:
        return sum(1 for r in self.set_results if r.monotonic)

    @property
    def mean_e4(self) -> float:
        return float(np.mean([r.e_values[3] for r in self.set_results]))

    @property
    def mean_e5(self) -> float:
        return float(np.mean([r.e_values[4] for r in self.set_results]))

    @property
    def e5_gt_e4(self) -> bool:
        return self.mean_e5 > self.mean_e4

    @property
    def passed(self) -> bool:
        return (
            self.n_monotonic >= self.min_monotonic_sets
            and self.e5_gt_e4
            and self.rank_recovery.monotonic
        )


@dataclass
class GateReport:
    scale_results: dict[str, ScaleGateResult] = field(default_factory=dict)
    model_name: str = ""
    prefix: str = ""

    @property
    def passed(self) -> bool:
        return bool(self.scale_results) and all(r.passed for r in self.scale_results.values())


# ============================================================================
# Загрузка config.yaml / anchors_ru.yaml
# ============================================================================


def load_embedding_config(config_path: Path | str = DEFAULT_CONFIG_PATH) -> dict:
    """Читает блок `embedding:` config.yaml. Отсутствующие поля — разумные дефолты."""
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    emb = cfg.get("embedding", {}) or {}
    return {
        "model": emb.get("model", "paraphrase-multilingual-MiniLM-L12-v2"),
        "prefix": emb.get("prefix", "") or "",
        "device": emb.get("device", "cpu"),
    }


def load_ssr_config(config_path: Path | str = DEFAULT_CONFIG_PATH) -> dict:
    """Читает блок `ssr:` config.yaml (epsilon/pmf_temperature/min_anchor_sets)."""
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    ssr_cfg = cfg.get("ssr", {}) or {}
    return {
        "epsilon": float(ssr_cfg.get("epsilon", 0.001)),
        "pmf_temperature": float(ssr_cfg.get("pmf_temperature", 1.0)),
        "min_anchor_sets": int(ssr_cfg.get("min_anchor_sets", 4)),
    }


def discover_scale_ids(anchors_path: Path | str = DEFAULT_ANCHORS_PATH) -> list[str]:
    """Список id шкал, реально присутствующих в anchors_ru.yaml (не хардкод — если появится
    4-я шкала, гейт подхватит её без правки этого файла)."""
    path = Path(anchors_path)
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    scales = data.get("scales", data) if isinstance(data, Mapping) else {}
    return sorted(k for k in scales if isinstance(k, str))


def load_set_labels(anchors_path: Path | str, scale_id: str) -> list[str]:
    """
    Best-effort метки наборов (поле `label`) — только для читаемости диагностического
    вывода, НЕ участвуют в вычислении гейта (за это отвечает ssr_core.load_anchor_sets,
    который метки намеренно игнорирует как служебное поле). Если меток нет/формат
    неожиданный — просто возвращает "набор N", гейт от этого не падает.
    """
    try:
        path = Path(anchors_path)
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        scales = data.get("scales", data) if isinstance(data, Mapping) else {}
        raw_sets = (scales.get(scale_id) or {}).get("anchor_sets") or []
        labels = []
        for i, raw in enumerate(raw_sets):
            label = raw.get("label") if isinstance(raw, Mapping) else None
            labels.append(str(label) if label else f"набор {i + 1}")
        return labels
    except Exception:
        return []


def build_backend(model: str, prefix: str = "", device: str = "cpu") -> ssr_core.SentenceTransformerBackend:
    return ssr_core.SentenceTransformerBackend(model_name=model, device=device, prefix=prefix)


# ============================================================================
# Ядро гейта
# ============================================================================


def leave_one_set_out_monotonicity(
    anchor_sets: Sequence[Mapping[int, str]],
    response_backend: ssr_core.EmbeddingBackend,
    anchor_backend: Optional[ssr_core.EmbeddingBackend] = None,
    epsilon: float = 0.001,
    pmf_temperature: float = 1.0,
    labels: Optional[Sequence[str]] = None,
) -> list[SetMonotonicityResult]:
    """
    Условие (a) гейта (см. докстринг модуля). `anchor_backend` — отдельный бэкенд для
    РОЛИ "эталонный якорь" (документ), если он отличается от роли "проверяемый ответ"
    (query) — нужно для асимметричных схем префиксов (например ru-en-RoSBERTa,
    search_query/search_document, см. embedder_ab.py). По умолчанию совпадает с
    response_backend — единый префикс на обе роли (текущая продакшн-схема ssr_core.py).

    Каждый набор кодируется РОВНО ОДИН РАЗ на роль (не n_sets^2 кодирований) — набор i
    в роли "ответ" кодируется response_backend, в роли "эталон" — anchor_backend; порядок
    leave-one-out не требует перекодирования, только разного комбинирования уже готовых
    эмбеддингов.
    """
    anchor_backend = anchor_backend or response_backend
    n = len(anchor_sets)
    labels = list(labels) if labels else [f"набор {i + 1}" for i in range(n)]
    texts_by_set = [[s[lvl] for lvl in LEVELS] for s in anchor_sets]
    query_embs_by_set = [response_backend.encode(t) for t in texts_by_set]
    doc_embs_by_set = [anchor_backend.encode(t) for t in texts_by_set]

    results = []
    for j in range(n):
        response_embs = query_embs_by_set[j]
        per_set_pmfs = [
            ssr_core.pmf_single_anchor_set(response_embs, doc_embs_by_set[i], epsilon, pmf_temperature)
            for i in range(n)
            if i != j
        ]
        avg_pmf = ssr_core.average_pmfs(per_set_pmfs)
        e_vals = ssr_core.expected_value(avg_pmf).flatten().tolist()
        diffs = [e_vals[k + 1] - e_vals[k] for k in range(4)]
        violations = [(k + 1, k + 2) for k, d in enumerate(diffs) if d <= 0]
        results.append(
            SetMonotonicityResult(set_index=j, label=labels[j], e_values=e_vals, violations=violations)
        )
    return results


def rank_recovery(
    paraphrase_bank_scale: Mapping[int, Sequence[str]],
    anchor_sets: Sequence[Mapping[int, str]],
    response_backend: ssr_core.EmbeddingBackend,
    anchor_backend: Optional[ssr_core.EmbeddingBackend] = None,
    epsilon: float = 0.001,
    pmf_temperature: float = 1.0,
) -> RankRecoveryResult:
    """Условие (c) гейта — см. докстринг модуля. Скорит held-out парафразы ПОЛНЫМ
    ансамблем всех наборов шкалы разом (как в продакшн-пайплайне ssr_core.SSREngine),
    без leave-one-out — тест на генерализацию, а не на взаимную согласованность."""
    anchor_backend = anchor_backend or response_backend
    doc_embs_by_set = [anchor_backend.encode([s[lvl] for lvl in LEVELS]) for s in anchor_sets]

    mean_e_by_level = []
    for lvl in LEVELS:
        texts = list(paraphrase_bank_scale[lvl])
        response_embs = response_backend.encode(texts)
        per_set_pmfs = [
            ssr_core.pmf_single_anchor_set(response_embs, doc_embs, epsilon, pmf_temperature)
            for doc_embs in doc_embs_by_set
        ]
        avg_pmf = ssr_core.average_pmfs(per_set_pmfs)
        e_vals = ssr_core.expected_value(avg_pmf).flatten()
        mean_e_by_level.append(float(e_vals.mean()))

    diffs = [mean_e_by_level[k + 1] - mean_e_by_level[k] for k in range(4)]
    violations = [(k + 1, k + 2) for k, d in enumerate(diffs) if d <= 0]
    return RankRecoveryResult(mean_e_by_level=mean_e_by_level, violations=violations)


def evaluate_scale(
    scale_id: str,
    anchor_sets: Sequence[Mapping[int, str]],
    response_backend: ssr_core.EmbeddingBackend,
    anchor_backend: Optional[ssr_core.EmbeddingBackend] = None,
    epsilon: float = 0.001,
    pmf_temperature: float = 1.0,
    min_monotonic_sets: int = 3,
    labels: Optional[Sequence[str]] = None,
    paraphrase_bank: Optional[Mapping[str, Mapping[int, Sequence[str]]]] = None,
) -> ScaleGateResult:
    bank = (paraphrase_bank or PARAPHRASE_BANK).get(scale_id)
    if not bank:
        raise KeyError(
            f"Нет банка парафразов для rank-recovery шкалы '{scale_id}' в PARAPHRASE_BANK "
            f"(test_anchors.py) — добавьте 4+ фразы на каждый уровень 1..5."
        )
    set_results = leave_one_set_out_monotonicity(
        anchor_sets, response_backend, anchor_backend, epsilon, pmf_temperature, labels
    )
    rr = rank_recovery(bank, anchor_sets, response_backend, anchor_backend, epsilon, pmf_temperature)
    return ScaleGateResult(
        scale_id=scale_id, set_results=set_results, rank_recovery=rr, min_monotonic_sets=min_monotonic_sets
    )


def evaluate_gate(
    anchors_path: Path | str = DEFAULT_ANCHORS_PATH,
    response_backend: Optional[ssr_core.EmbeddingBackend] = None,
    anchor_backend: Optional[ssr_core.EmbeddingBackend] = None,
    epsilon: float = 0.001,
    pmf_temperature: float = 1.0,
    min_anchor_sets: int = 4,
    min_monotonic_sets: int = 3,
    model_name: str = "",
    prefix: str = "",
    paraphrase_bank: Optional[Mapping[str, Mapping[int, Sequence[str]]]] = None,
) -> GateReport:
    """Прогоняет весь гейт §1.2 п.2 на ВСЕХ шкалах, найденных в anchors_path."""
    if response_backend is None:
        raise ValueError("evaluate_gate: нужен response_backend (EmbeddingBackend)")
    report = GateReport(model_name=model_name, prefix=prefix)
    for scale_id in discover_scale_ids(anchors_path):
        _, anchor_sets = ssr_core.load_anchor_sets(anchors_path, scale_id)
        if len(anchor_sets) < min_anchor_sets:
            raise ValueError(
                f"Шкала '{scale_id}': {len(anchor_sets)} набор(ов) якорей < min_anchor_sets={min_anchor_sets}"
            )
        labels = load_set_labels(anchors_path, scale_id)
        report.scale_results[scale_id] = evaluate_scale(
            scale_id,
            anchor_sets,
            response_backend,
            anchor_backend,
            epsilon,
            pmf_temperature,
            min_monotonic_sets,
            labels,
            paraphrase_bank,
        )
    return report


# ============================================================================
# Форматированный вывод (переиспользуется embedder_ab.py)
# ============================================================================


def format_report(report: GateReport, verbose: bool = True) -> str:
    lines = []
    header = f"=== Гейт монотонности якорей: {report.model_name or '(без имени)'}"
    if report.prefix:
        header += f" | prefix={report.prefix!r}"
    header += " ==="
    lines.append(header)
    for scale_id, sr in report.scale_results.items():
        lines.append("")
        lines.append(f"--- Шкала: {scale_id} ---")
        if verbose:
            for r in sr.set_results:
                e_str = " -> ".join(f"{v:.3f}" for v in r.e_values)
                verdict = "OK" if r.monotonic else f"НЕМОНОТОНЕН {r.violations}"
                lines.append(f"  [{r.label:28s}] E(1..5) = {e_str}   {verdict}")
        lines.append(
            f"  Монотонных наборов: {sr.n_monotonic}/{sr.n_sets} "
            f"(порог >= {sr.min_monotonic_sets}) -> {'PASS' if sr.n_monotonic >= sr.min_monotonic_sets else 'FAIL'}"
        )
        lines.append(
            f"  Среднее E(4)={sr.mean_e4:.3f}, среднее E(5)={sr.mean_e5:.3f} "
            f"-> {'PASS (E5>E4)' if sr.e5_gt_e4 else 'FAIL (E5<=E4)'}"
        )
        rr_str = " -> ".join(f"{v:.3f}" for v in sr.rank_recovery.mean_e_by_level)
        lines.append(
            f"  Rank-recovery (held-out парафразы) E(1..5) = {rr_str} "
            f"-> {'PASS' if sr.rank_recovery.monotonic else f'FAIL {sr.rank_recovery.violations}'}"
        )
        lines.append(f"  ИТОГ шкалы '{scale_id}': {'PASS' if sr.passed else 'FAIL'}")
    lines.append("")
    lines.append(f"=== ОБЩИЙ ВЕРДИКТ ГЕЙТА: {'PASS' if report.passed else 'FAIL'} ===")
    return "\n".join(lines)


# ============================================================================
# CLI
# ============================================================================


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Гейт монотонности якорных наборов SSR (§1.2 п.2)")
    parser.add_argument("--anchors", default=str(DEFAULT_ANCHORS_PATH), help="Путь к anchors_ru.yaml")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Путь к config.yaml (дефолты)")
    parser.add_argument("--model", default=None, help="HF-имя embedding-модели (иначе — из config.yaml)")
    parser.add_argument("--prefix", default=None, help="Префикс перед кодированием (иначе — из config.yaml)")
    parser.add_argument("--device", default=None, help="cpu/cuda/mps (иначе — из config.yaml)")
    parser.add_argument("--epsilon", type=float, default=None)
    parser.add_argument("--pmf-temperature", type=float, default=None)
    parser.add_argument("-v", "--verbose", action="store_true", help="Печатать таблицу E по каждому набору")
    args = parser.parse_args(argv)

    emb_cfg = load_embedding_config(args.config)
    ssr_cfg = load_ssr_config(args.config)
    model = args.model or emb_cfg["model"]
    prefix = args.prefix if args.prefix is not None else emb_cfg["prefix"]
    device = args.device or emb_cfg["device"]
    epsilon = args.epsilon if args.epsilon is not None else ssr_cfg["epsilon"]
    pmf_temperature = args.pmf_temperature if args.pmf_temperature is not None else ssr_cfg["pmf_temperature"]

    # Ловушка, в которую реально попал автор этого файла при A/B-сравнении эмбеддеров:
    # --model без --prefix молча наследует prefix ИЗ config.yaml, который может быть
    # настроен под СОВЕРШЕННО ДРУГУЮ модель (например, после того как embedder_ab.py
    # уже зафиксировал победителя) — числа при этом выглядят правдоподобно, но считаются
    # на мусорном входе (чужой префикс приклеен к чужой модели). Явно предупреждаем.
    if args.model is not None and args.prefix is None and model != emb_cfg["model"]:
        print(
            f"ВНИМАНИЕ: --model {model!r} задан явно, но --prefix не передан — "
            f"унаследован prefix={emb_cfg['prefix']!r} из {args.config} (там настроен для "
            f"{emb_cfg['model']!r}, ДРУГОЙ модели). Если это не тот префикс, который нужен "
            f"для {model!r} — передайте --prefix явно (пустой строкой, если модель без префикса).",
            file=sys.stderr,
        )

    print(f"Загружаю эмбеддер: {model} (prefix={prefix!r}, device={device})...", file=sys.stderr)
    backend = build_backend(model, prefix, device)
    report = evaluate_gate(
        anchors_path=args.anchors,
        response_backend=backend,
        epsilon=epsilon,
        pmf_temperature=pmf_temperature,
        min_anchor_sets=ssr_cfg["min_anchor_sets"],
        model_name=model,
        prefix=prefix,
    )
    print(format_report(report, verbose=args.verbose))
    return 0 if report.passed else 1


# ============================================================================
# Unittest-обёртка (регрессия — "выбранный стек" из config.yaml)
# ============================================================================


class TestAnchorGateOnConfiguredStack(unittest.TestCase):
    """
    Гейт §1.2 п.2 на эмбеддере, зафиксированном в config.yaml — то есть "выбранном стеке"
    после embedder_ab.py. ТРЕБУЕТ СЕТЬ при первом запуске (скачивание модели), если она ещё
    не в кэше venv — это осознанное отличие от test_ssr.py (см. модульный докстринг).

    Если этот тест красный — config.yaml указывает на эмбеддер/anchors_ru.yaml, не прошедшие
    гейт §1.2 п.2: см. DoD spec_synthetic-panel_v1.3.md, "красный гейт = сборка не принята".
    """

    @classmethod
    def setUpClass(cls):
        cls.emb_cfg = load_embedding_config()
        cls.ssr_cfg = load_ssr_config()
        cls.backend = build_backend(cls.emb_cfg["model"], cls.emb_cfg["prefix"], cls.emb_cfg["device"])

    def test_gate_passes_on_configured_stack(self):
        report = evaluate_gate(
            anchors_path=DEFAULT_ANCHORS_PATH,
            response_backend=self.backend,
            epsilon=self.ssr_cfg["epsilon"],
            pmf_temperature=self.ssr_cfg["pmf_temperature"],
            min_anchor_sets=self.ssr_cfg["min_anchor_sets"],
            model_name=self.emb_cfg["model"],
            prefix=self.emb_cfg["prefix"],
        )
        print("\n" + format_report(report, verbose=True))
        failing = [sid for sid, sr in report.scale_results.items() if not sr.passed]
        self.assertTrue(
            report.passed,
            f"Гейт §1.2 п.2 провален на шкалах {failing} (эмбеддер {self.emb_cfg['model']!r}). "
            f"См. вывод format_report выше.",
        )


class TestParaphraseBankIntegrity(unittest.TestCase):
    """Быстрые проверки самого банка парафразов (без эмбеддингов, чисто структурные)."""

    def test_every_scale_has_all_five_levels_with_enough_phrases(self):
        for scale_id in discover_scale_ids():
            self.assertIn(scale_id, PARAPHRASE_BANK, f"нет банка парафразов для шкалы {scale_id}")
            bank = PARAPHRASE_BANK[scale_id]
            for lvl in LEVELS:
                self.assertIn(lvl, bank, f"{scale_id}: нет уровня {lvl} в банке парафразов")
                self.assertGreaterEqual(
                    len(bank[lvl]), 2, f"{scale_id}[{lvl}]: меньше 2 фраз в банке парафразов"
                )

    def test_paraphrases_do_not_duplicate_anchor_phrases(self):
        anchors_path = DEFAULT_ANCHORS_PATH
        for scale_id in discover_scale_ids(anchors_path):
            _, anchor_sets = ssr_core.load_anchor_sets(anchors_path, scale_id)
            anchor_texts = {s[lvl] for s in anchor_sets for lvl in LEVELS}
            bank = PARAPHRASE_BANK.get(scale_id, {})
            for lvl, texts in bank.items():
                for t in texts:
                    self.assertNotIn(
                        t, anchor_texts, f"{scale_id}[{lvl}]: парафраз дублирует anchor_sets дословно: {t!r}"
                    )


if __name__ == "__main__":
    # Различаем "CLI-диагностика" и "unittest": если переданы CLI-флаги диагностики
    # (--model/--prefix/--device/--anchors отличный от дефолта и т.п.) — работаем как CLI.
    # По умолчанию (без аргументов) — обычный unittest-прогон, как у всех test_*.py в проекте.
    _cli_flags = {"--model", "--prefix", "--device", "--anchors", "--config", "--epsilon", "--pmf-temperature"}
    if any(a in _cli_flags or a.startswith(tuple(f"{f}=" for f in _cli_flags)) for a in sys.argv[1:]):
        sys.exit(main())
    else:
        unittest.main(verbosity=2)

"""
calibrate.py — калибровка SSR PMF к реальным данным (Слой C, фаза B — НЕ реализовано в v1).

Статус: интерфейс зафиксирован (контракт для будущей реализации), тела функций —
заглушки, которые явно бросают NotImplementedError, а не тихо возвращают
правдоподобный, но пустой результат (тот же принцип, что и GigaChatProvider в
generate.py — каркас, а не бесшумная имитация работы).

Зачем нужен этот слой (см. references/methodology.md, §1, Слой C): сырой PMF из
SSR не привязан к конкретной категории/рынку и систематически смещает абсолютные
величины (пример из methodology.md: LLM "из коробки" завышают заявленное ежедневное
потребление кофе — 91% против 56% у реальных респондентов, MAE 19,8 п.п.). Калибровка
подгоняет PMF к историческим holdout-данным клиента ОТДЕЛЬНО по каждому сегменту —
см. references/disclaimers.md, расширенный блок для калиброванного режима: улучшение
средней точности калибровкой может одновременно увеличивать разброс точности МЕЖДУ
сегментами, поэтому и подбор коэффициентов, и отчётность метрик — посегментные, а не
одно среднее число на всю панель.

Когда этот модуль перестаёт быть заглушкой:
    - runs/<study>/manifest.json приобретает секцию "calibration";
    - references/disclaimers.md, блок DISCLAIMER_BLOCK_CALIBRATED_START/END
      добавляется report.py к базовому блоку (не заменяет его — см. disclaimers.md);
    - is_calibration_active() ниже начинает возвращать True для откалиброванных прогонов.

Метрики приёмки (см. panel/sources.md §2 шаг 5; references/methodology.md):
    rank-order correlation (цель >= 0.75), winner/loser match (>= 80%),
    MAE top-2-box (<= 5-7 п.п.), segment MAE (<= 8-10 п.п.), KS distribution
    similarity, test-retest stability. Human holdout ОБЯЗАН быть независим от
    выборки, на которой подбирались коэффициенты калибровки (иначе метрика
    измеряет переобучение, а не реальную точность — см. disclaimers.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass
class HumanBenchmark:
    """Один исторический human-holdout тест клиента для калибровки одного сегмента."""

    segment_id: str
    category: str
    period: str
    # Эмпирическое распределение реальных ответов по шкале 1..5 (например, доли
    # top-2-box/нейтрально/bottom-2-box исходного опроса), индекс 0..4 = точка 1..5,
    # сумма == 1.0.
    human_pmf: np.ndarray
    n_respondents: int
    source: str  # напр. "Ромир скан-панель 2026Q1" — см. panel/sources.md, §3


@dataclass
class CalibrationResult:
    segment_id: str
    method: str  # напр. "platt_scaling_per_segment" / "isotonic_regression" — не выбрано в v1
    rank_order_correlation: float
    winner_loser_match: float
    mae_top2box: float
    ks_similarity: float
    calibrated_at: str  # ISO8601
    recalibration_window_days: int


def fit_calibration(
    synthetic_pmf_by_segment: dict[str, np.ndarray],
    benchmarks: Sequence[HumanBenchmark],
) -> dict[str, CalibrationResult]:
    """
    TODO(фаза B): подобрать коэффициенты калибровки синтетического PMF к human_pmf
    ОТДЕЛЬНО по каждому сегменту (см. модульный докстринг про разброс точности
    между сегментами).

    Кандидаты метода (не выбраны в v1, оставлено на фазу B): Platt scaling на
    logit(PMF), isotonic regression E[шкала] -> E_human, либо полная 5x5
    перевзвешивающая матрица PMF -> PMF, подобранная на holdout.
    """
    raise NotImplementedError(
        "calibrate.py: калибровка — фаза B, не реализована в v1 (persona-режим без "
        "калибровки, см. spec_synthetic-panel_v1.md §1, §4 Слой C, references/methodology.md). "
        "Интерфейс (HumanBenchmark/CalibrationResult/fit_calibration/apply_calibration) "
        "зафиксирован для будущей реализации."
    )


def apply_calibration(raw_pmf: np.ndarray, calibration: CalibrationResult) -> np.ndarray:
    """TODO(фаза B): применить подобранные коэффициенты к новому сырому PMF того же сегмента."""
    raise NotImplementedError("calibrate.py: apply_calibration — фаза B, не реализована в v1. См. fit_calibration.")


def is_calibration_active(manifest: dict) -> bool:
    """
    Единственная функция модуля, безопасная для вызова в v1: проверяет, есть ли в
    manifest.json секция "calibration". В v1 всегда False (report.py поэтому не
    добавляет DISCLAIMER_BLOCK_CALIBRATED — см. references/disclaimers.md).
    """
    return bool(manifest.get("calibration"))


if __name__ == "__main__":
    print(
        "calibrate.py — заглушка с зафиксированным интерфейсом (фаза B, не реализовано в v1).\n"
        "См. docstring модуля и references/methodology.md, §1 (Слой C)."
    )

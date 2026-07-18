"""
ssr_core.py — математика Semantic Similarity Rating (SSR) для синтетической ИИ-панели.

Реализует spec_synthetic-panel_v1.md §4. Источник метода: arXiv 2510.08338
(PyMC Labs, "LLMs Reproduce Human Purchase Intent via Semantic Similarity
Elicitation of Likert Ratings").

Формула (для эмбеддинга ответа r и якорей a_1..a_5 ОДНОГО набора k):
    s_i   = cosine(r, a_i)                      — сходство ответа с точкой шкалы i
    p_i   = (s_i − min_j s_j) + epsilon          — вычитаем минимум, снимаем базовый уровень
    p_i  /= sum_j p_j                            — нормировка в PMF (сумма = 1)
    (опционально) softmax(log(p) / T)            — температурное сглаживание, T=1.0 = как есть
Затем:
    - усреднение PMF по всем наборам якорей (>= ssr.min_anchor_sets, обычно 4) -> PMF ответа;
    - усреднение по сэмплам одного респондента -> PMF респондента;
    - усреднение по респондентам сегмента -> PMF сегмента;
    - E = sum_i i * p_i (ожидание по шкале 1-5);
    - доверительный интервал E — бутстреп по респондентам (см. bootstrap_ci).

Инъекция embedding-бэкенда: класс EmbeddingBackend — единственная точка, где нужна
реальная модель эмбеддингов. Юнит-тесты (test_ssr.py) работают на голых numpy-векторах
и НИКОГДА не создают SentenceTransformerBackend — поэтому импорт sentence_transformers
сделан ЛЕНИВЫМ (внутри __init__ этого класса), а не на уровне модуля: `import ssr_core`
не требует установленного sentence-transformers/torch.

Контракт схемы references/anchors_ru.yaml (владелец файла — сборщик методологии [B2];
подтверждён по фактическому файлу на момент написания этого модуля):

    meta:
      min_sets_required: 4
    scales:
      <scale_id>:                  # id = значение study.yaml: question_scale
        question: "текст вопроса респонденту (ИДЁТ в промпт генерации)"
        anchor_sets:                # список, len >= ssr.min_anchor_sets
          - label: "..."            # необязательно, для логов
            phrases:
              1: "..."               # ЯКОРНЫЕ ФРАЗЫ — НИКОГДА не идут в промпт генерации
              2: "..."
              3: "..."
              4: "..."
              5: "..."

load_anchor_sets() ниже толерантен к паре разумных вариаций этой схемы (см. docstring
функции) — если реальный файл всё же разойдётся, здесь единственное место, которое
нужно поправить.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

import numpy as np
import yaml

logger = logging.getLogger(__name__)


# ============================================================================
# Embedding-слой: инъецируемый интерфейс
# ============================================================================


class EmbeddingBackend(ABC):
    """Абстрактный интерфейс энкодера текста в векторы (см. §4 спецификации)."""

    @abstractmethod
    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """Возвращает np.ndarray формы (len(texts), dim). Порядок строк = порядок texts."""
        raise NotImplementedError


class SentenceTransformerBackend(EmbeddingBackend):
    """
    Бэкенд на пакете sentence-transformers (прод/демо-режим).

    ВАЖНО: импорт sentence_transformers — ЛЕНИВЫЙ, происходит только здесь, в
    конструкторе конкретного класса, а не на уровне модуля ssr_core.py. Это
    сделано намеренно: test_ssr.py и agent-режим генерации (generate.py) не
    должны требовать установленного sentence-transformers/torch — они его не
    используют. Только score-стадия run_study.py реально инстанцирует этот класс.
    """

    def __init__(self, model_name: str, device: str = "cpu", prefix: str = ""):
        try:
            from sentence_transformers import SentenceTransformer  # ленивый импорт
        except ImportError as exc:  # pragma: no cover - обвязка для человекочитаемой ошибки
            raise ImportError(
                "Пакет 'sentence-transformers' не установлен, а provider embedding "
                "требует его для score-стадии. Установите: pip install sentence-transformers torch "
                "(см. scripts/setup.sh) или запустите `--stage generate`/`--stage report` отдельно, "
                "которые эту модель не используют."
            ) from exc

        self._model = SentenceTransformer(model_name, device=device)
        self.model_name = model_name
        self.device = device
        self.prefix = prefix or ""

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        prefixed = [f"{self.prefix}{t}" for t in texts] if self.prefix else list(texts)
        embeddings = self._model.encode(
            prefixed, normalize_embeddings=False, show_progress_bar=False, convert_to_numpy=True
        )
        return np.asarray(embeddings, dtype=np.float64)


# ============================================================================
# Ядро SSR-математики (чистый numpy, без внешних зависимостей)
# ============================================================================


def cosine_similarity_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    Косинусное сходство между каждой строкой a и каждой строкой b.

    a: (n, dim), b: (k, dim) -> результат (n, k).
    Нулевые векторы (edge case, напр. эмбеддинг пустой строки) не приводят к делению
    на ноль — их норма подменяется на 1.0, что даёт сходство 0 с чем угодно (нейтрально).
    """
    a = np.atleast_2d(np.asarray(a, dtype=np.float64))
    b = np.atleast_2d(np.asarray(b, dtype=np.float64))
    a_norm = np.linalg.norm(a, axis=1, keepdims=True)
    b_norm = np.linalg.norm(b, axis=1, keepdims=True)
    a_norm = np.where(a_norm == 0, 1.0, a_norm)
    b_norm = np.where(b_norm == 0, 1.0, b_norm)
    a_unit = a / a_norm
    b_unit = b / b_norm
    return a_unit @ b_unit.T


def pmf_from_similarities(sim: np.ndarray, epsilon: float) -> np.ndarray:
    """
    Ядро SSR: s_i -> p_i для ОДНОГО набора якорей.

    sim: (n_responses, n_points) — косинусные сходства ответов с точками 1..n_points
    одного якорного набора. Возвращает PMF той же формы, каждая строка суммируется в 1.

    Вырожденный случай (все сходства в строке идентичны И epsilon == 0): числитель и
    знаменатель нулевые — вместо деления на ноль/NaN возвращается равномерное
    распределение 1/n_points (нейтральный, документированный fallback, а не крах).
    """
    sim = np.atleast_2d(np.asarray(sim, dtype=np.float64))
    n_points = sim.shape[1]
    s_min = sim.min(axis=1, keepdims=True)
    numerator = (sim - s_min) + epsilon
    denom = numerator.sum(axis=1, keepdims=True)

    degenerate = (denom.flatten() <= 0)
    safe_denom = np.where(denom <= 0, 1.0, denom)
    pmf = numerator / safe_denom
    if np.any(degenerate):
        pmf[degenerate, :] = 1.0 / n_points
    return pmf


def apply_temperature(pmf: np.ndarray, temperature: float) -> np.ndarray:
    """
    Опциональное температурное сглаживание PMF: softmax(log(p) / T).

    T == 1.0 математически тождественно исходному PMF (softmax(log p) == p, т.к.
    p уже суммируется в 1) — при T == 1.0 функция возвращает pmf без изменений,
    не гоняя лишний раз log/exp (числовой шум был бы порядка 1e-16, но зачем).
    T < 1 заостряет распределение, T > 1 сглаживает.
    """
    if temperature == 1.0:
        return pmf
    if temperature <= 0:
        raise ValueError(f"pmf_temperature должен быть > 0, получено: {temperature}")
    pmf = np.atleast_2d(np.asarray(pmf, dtype=np.float64))
    log_p = np.log(np.clip(pmf, 1e-12, None))
    scaled = log_p / temperature
    scaled = scaled - scaled.max(axis=1, keepdims=True)  # численная устойчивость
    exp_scaled = np.exp(scaled)
    return exp_scaled / exp_scaled.sum(axis=1, keepdims=True)


def pmf_single_anchor_set(
    response_embs: np.ndarray,
    anchor_embs: np.ndarray,
    epsilon: float,
    temperature: float = 1.0,
) -> np.ndarray:
    """cosine -> pmf_from_similarities -> apply_temperature, для одного набора якорей."""
    sim = cosine_similarity_matrix(response_embs, anchor_embs)
    pmf = pmf_from_similarities(sim, epsilon)
    return apply_temperature(pmf, temperature)


def average_pmfs(pmfs: Iterable[np.ndarray]) -> np.ndarray:
    """
    Простое усреднение набора PMF-матриц одинаковой формы. Инвариантно к порядку
    элементов в pmfs (среднее арифметическое не зависит от порядка слагаемых) —
    используется и для усреднения по наборам якорей, и по сэмплам/респондентам.
    """
    stacked = np.stack([np.atleast_2d(p) for p in pmfs], axis=0)
    return stacked.mean(axis=0)


def expected_value(pmf: np.ndarray, scale: Optional[Sequence[float]] = None) -> np.ndarray:
    """E = sum_i i * p_i. pmf может быть (n_points,) или (n, n_points); scale по умолчанию 1..n_points."""
    pmf = np.atleast_2d(np.asarray(pmf, dtype=np.float64))
    n_points = pmf.shape[1]
    if scale is None:
        scale_arr = np.arange(1, n_points + 1, dtype=np.float64)
    else:
        scale_arr = np.asarray(scale, dtype=np.float64)
    return pmf @ scale_arr


def aggregate_pmfs_by_key(pmfs: np.ndarray, keys: Sequence[str]) -> dict[str, np.ndarray]:
    """
    Группирует строки pmfs (n, n_points) по keys (длины n) и усредняет PMF внутри
    каждой группы. Обычное среднее — инвариантно к порядку строк внутри группы.
    Используется дважды в score-стадии run_study.py: сэмплы -> респондент,
    респонденты -> сегмент.
    """
    groups: dict[str, list[int]] = {}
    for i, k in enumerate(keys):
        groups.setdefault(k, []).append(i)
    return {k: pmfs[idx].mean(axis=0) for k, idx in groups.items()}


# ============================================================================
# Бутстреп-CI (по респондентам)
# ============================================================================


@dataclass
class BootstrapResult:
    point_estimate: float
    ci_low: float
    ci_high: float
    n_iters: int
    seed: int
    ci: float


def bootstrap_ci(
    values: np.ndarray,
    n_iters: int = 1000,
    seed: int = 42,
    ci: float = 0.95,
) -> BootstrapResult:
    """
    Бутстреп-CI по респондентам для скалярной статистики (обычно E на респондента).

    Ресэмплинг с возвращением N раз (N = len(values)) повторяется n_iters раз;
    точечная оценка — среднее исходных values; границы CI — перцентили распределения
    ресэмплированных средних. При фиксированном seed результат бит-в-бит воспроизводим
    (np.random.default_rng(seed) детерминирован) — см. test_ssr.py::test_bootstrap_*.

    Бутстрепить можно как E-значения на респондента (обычный путь в report.py), так и
    точечные PMF при необходимости — в терминах ожидания это эквивалентно, т.к.
    E(mean(pmf_i)) == mean(E(pmf_i)) (E — линейный функционал), поэтому отдельной
    функции для бутстрепа PMF не заведено, чтобы не дублировать логику.
    """
    values = np.asarray(values, dtype=np.float64).flatten()
    n = values.shape[0]
    if n == 0:
        raise ValueError("bootstrap_ci: пустой массив значений — бутстреп невозможен")
    point_estimate = float(values.mean())
    if n == 1:
        # Вырожденный случай: бутстреп по одному наблюдению не даёт информации о
        # разбросе — возвращаем нулевой интервал вокруг единственного значения,
        # а не бросаем ошибку (одного респондента иногда достаточно для smoke-теста).
        return BootstrapResult(point_estimate, point_estimate, point_estimate, n_iters, seed, ci)

    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_iters, n))
    resampled_means = values[idx].mean(axis=1)
    alpha = (1.0 - ci) / 2.0
    lower = float(np.quantile(resampled_means, alpha))
    upper = float(np.quantile(resampled_means, 1.0 - alpha))
    return BootstrapResult(point_estimate, lower, upper, n_iters, seed, ci)


# ============================================================================
# Парный бутстреп разностей (spec_synthetic-panel_v1.3.md §1.3, фикс дефекта Д3)
# ============================================================================
#
# Д3 (верифицирован оркестратором): старый метод report.py считал "разделимость"
# как непересечение НЕЗАВИСИМЫХ бутстреп-ДИ двух стимулов (bootstrap_ci выше,
# вызванный ОТДЕЛЬНО для каждого стимула) — это статистически некорректно, когда
# оба стимула оценены ОДНИМИ И ТЕМИ ЖЕ респондентами (см. build_tasks в
# generate.py: каждый респондент сегмента отвечает на ВСЕ стимулы study.yaml).
# Правильный метод для "тот же респондент, стимул A против стимула B" —
# ПАРНЫЙ (paired) бутстреп разности: ресэмплируется ОДИН И ТОТ ЖЕ набор индексов
# респондентов, и ЭТОТ ЖЕ набор применяется одинаково ко ВСЕМ стимулам сразу —
# так сохраняется исходная парность (respondent i участвует в паре "A_i vs B_i"
# в КАЖДОЙ бутстреп-итерации, а не сравнивается со случайным другим респондентом
# из независимой выборки B). Из результата джойнт-бутстрепа ниже считаются и
# P(A>B) для любой пары, и вероятность 1-го места для каждого стимула — один
# проход бутстрепа на все производные метрики раздела 1.3.


def joint_paired_bootstrap_means(
    e_matrix: np.ndarray,
    n_iters: int = 1000,
    seed: int = 42,
) -> np.ndarray:
    """
    e_matrix: (n_respondents, n_stimuli) — E[шкала] по РЕСПОНДЕНТУ (строка) и
    СТИМУЛУ (столбец); респонденты — ОДНИ И ТЕ ЖЕ для всех стимулов-столбцов (см.
    докстринг раздела выше). Возвращает (n_iters, n_stimuli): для каждой итерации
    резэмплируется ОДИН набор индексов респондентов (with replacement, N =
    n_respondents) и применяется ОДИНАКОВО ко всем столбцам — так сохраняется
    парность, в отличие от независимого bootstrap_ci на каждый стимул отдельно.

    Из возвращаемой матрицы: pairwise_win_probability(boot_means, i, j) даёт
    P(стимул i > стимул j); place_probabilities(boot_means) даёт вероятность
    1-го места для каждого стимула. Оба — постобработка ОДНОГО прохода
    резэмплирования ниже (не отдельные циклы бутстрепа).
    """
    e_matrix = np.atleast_2d(np.asarray(e_matrix, dtype=np.float64))
    n_resp = e_matrix.shape[0]
    if n_resp == 0:
        raise ValueError("joint_paired_bootstrap_means: нет респондентов (пустая матрица).")
    if n_resp == 1:
        # Вырожденный случай — как и bootstrap_ci: бутстреп по одному респонденту не
        # даёт информации о разбросе; возвращаем n_iters копий единственной строки,
        # чтобы pairwise_win_probability/place_probabilities не падали, а честно
        # показали "точка совпадает с оценкой" (P(A>B) станет 0 или 1 детерминированно
        # по знаку разницы, без ложной иллюзии точности от нулевой дисперсии).
        return np.repeat(e_matrix, n_iters, axis=0)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n_resp, size=(n_iters, n_resp))
    resampled = e_matrix[idx]  # (n_iters, n_resp, n_stimuli) - фенси-индексация по строкам
    return resampled.mean(axis=1)  # (n_iters, n_stimuli)


def pairwise_win_probability(boot_means: np.ndarray, i: int, j: int) -> float:
    """
    P(стимул i > стимул j) по результатам joint_paired_bootstrap_means — доля
    бутстреп-итераций, где резэмплированное среднее E стимула i строго выше, чем
    у стимула j. Строгое ">" (не ">="): точное совпадение float-средних в реальных
    SSR-данных астрономически маловероятно (E — непрерывная величина по PMF), так
    что доля "равных" итераций пренебрежимо мала и не искажает оценку.
    """
    boot_means = np.atleast_2d(boot_means)
    return float(np.mean(boot_means[:, i] > boot_means[:, j]))


def place_probabilities(boot_means: np.ndarray) -> np.ndarray:
    """
    Вероятность 1-го места для каждого из n_stimuli стимулов — доля бутстреп-итераций
    (строк boot_means), где данный стимул строго максимален среди всех столбцов.

    Tie-handling: при точном совпадении резэмплированных средних np.argmax берёт
    ПЕРВЫЙ по порядку столбец (документированное поведение numpy) — для реальных
    SSR-PMF точные совпадения практически не встречаются (см. докстринг
    pairwise_win_probability); упоминается здесь, чтобы поведение при вырожденных
    входах (например, все ответы идентичны) было явно документировано, а не
    случайно обнаружено в тесте.
    """
    boot_means = np.atleast_2d(boot_means)
    n_iters, n_stim = boot_means.shape
    winners = np.argmax(boot_means, axis=1)
    counts = np.bincount(winners, minlength=n_stim)
    return counts / n_iters


def kendall_tau(order_a: Sequence[str], order_b: Sequence[str]) -> float:
    """
    Kendall tau-a между двумя ранжированиями ОДНОГО и того же множества id (порядок
    от лучшего к худшему) — используется для межпрогонной устойчивости рангов
    (spec_synthetic-panel_v1.3.md §1.3.2: "Kendall-устойчивость рангов между
    прогонами"). order_a/order_b обязаны быть перестановками ОДНОГО и того же
    множества id (иначе сравнение бессмысленно — ValueError).

    tau-a = (согласные пары − несогласные пары) / (n*(n-1)/2), диапазон [-1, 1];
    1 = идентичный порядок, -1 = полностью обратный. Реализовано БЕЗ scipy (лишняя
    тяжёлая зависимость ради одной формулы на малое n — типичное число стимулов
    исследования, единицы) — прямой подсчёт согласных/несогласных пар, O(n^2),
    тривиально быстро для n порядка 10.
    """
    set_a, set_b = set(order_a), set(order_b)
    if set_a != set_b:
        raise ValueError(
            f"kendall_tau: ранжирования по разным множествам id: {sorted(set_a)} vs {sorted(set_b)}"
        )
    n = len(order_a)
    if n < 2:
        return 1.0  # 0 или 1 элемент - порядок тривиально "совпадает"
    rank_a = {v: i for i, v in enumerate(order_a)}
    rank_b = {v: i for i, v in enumerate(order_b)}
    items = list(order_a)
    concordant = 0
    discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            sign_a = rank_a[items[i]] - rank_a[items[j]]
            sign_b = rank_b[items[i]] - rank_b[items[j]]
            product = sign_a * sign_b
            if product > 0:
                concordant += 1
            elif product < 0:
                discordant += 1
            # product == 0 невозможно: ранги внутри ОДНОГО ранжирования уникальны,
            # так что sign_a и sign_b по отдельности никогда не равны 0.
    total_pairs = n * (n - 1) / 2
    return (concordant - discordant) / total_pairs


# ============================================================================
# Загрузка references/anchors_ru.yaml
# ============================================================================


def load_anchor_sets(anchors_path: Path | str, scale_id: str) -> tuple[str, list[dict[int, str]]]:
    """
    Читает references/anchors_ru.yaml (владелец — сборщик методологии [B2]) и
    возвращает (question, anchor_sets) для шкалы scale_id.

    question — формулировка вопроса респонденту (идёт в промпт генерации).
    anchor_sets — список словарей {1: фраза, 2: фраза, ..., 5: фраза}, ПОРЯДОК
    наборов в списке не имеет значения (усредняются, см. average_pmfs) — эти фразы
    НИКОГДА не должны попадать в промпт генерации (см. generate.py).

    Ожидаемая схема (см. модульный docstring выше):
        scales:
          <scale_id>:
            question: "..."
            anchor_sets:
              - phrases: {1: "...", 2: "...", 3: "...", 4: "...", 5: "..."}
              - phrases: {...}
              ...

    Толерантности парсера (чтобы не падать на разумных вариациях авторства файла):
      - верхнеуровневый ключ `scales` необязателен — при его отсутствии весь корневой
        словарь трактуется как словарь шкал;
      - элемент anchor_sets может быть либо {"phrases": {...}, "label": ...}, либо
        "голым" {1: .., 5: ..} без обёртки phrases — служебные нечисловые ключи
        (label/set_id/name/id/comment/...) в этом случае просто игнорируются;
      - ключи фраз внутри phrases могут быть int или str "1".."5".

    Бросает FileNotFoundError / KeyError / ValueError с человекочитаемым сообщением
    при несоответствии схемы — вызывающий код (run_study.py) должен ловить их и
    печатать понятную ошибку, а не падать трейсбеком.
    """
    path = Path(anchors_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл якорей не найден: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    scales = data.get("scales", data) if isinstance(data, Mapping) else {}
    if not isinstance(scales, Mapping) or scale_id not in scales:
        available = ", ".join(sorted(k for k in scales if isinstance(k, str))) or "(пусто)"
        raise KeyError(f"Шкала '{scale_id}' не найдена в {path}. Доступные шкалы: {available}")

    scale_def = scales[scale_id]
    question = (scale_def or {}).get("question")
    if not question or not isinstance(question, str):
        raise ValueError(f"У шкалы '{scale_id}' отсутствует непустое поле 'question' в {path}")

    raw_sets = (scale_def or {}).get("anchor_sets") or (scale_def or {}).get("sets")
    if not raw_sets:
        raise ValueError(f"У шкалы '{scale_id}' отсутствуют anchor_sets в {path}")

    anchor_sets: list[dict[int, str]] = []
    for i, raw in enumerate(raw_sets):
        phrases_raw = raw.get("phrases", raw) if isinstance(raw, Mapping) else None
        if phrases_raw is None:
            raise ValueError(f"anchor_sets[{i}] шкалы '{scale_id}' имеет неверный формат: {raw!r}")
        phrases: dict[int, str] = {}
        for k, v in phrases_raw.items():
            try:
                k_int = int(k)
            except (TypeError, ValueError):
                continue  # служебный ключ вроде label/set_id/name/comment — пропускаем
            phrases[k_int] = v
        if sorted(phrases.keys()) != [1, 2, 3, 4, 5]:
            raise ValueError(
                f"anchor_sets[{i}] шкалы '{scale_id}' должен содержать ровно 5 фраз с ключами "
                f"1..5, получено: {sorted(phrases.keys())} (файл {path})"
            )
        anchor_sets.append(phrases)

    return question, anchor_sets


# ============================================================================
# Высокоуровневый оркестратор
# ============================================================================


class SSREngine:
    """
    Оркестратор SSR для одной шкалы: инкапсулирует embedding-бэкенд, якорные наборы
    и параметры (epsilon, pmf_temperature). Используется score-стадией run_study.py.
    """

    def __init__(
        self,
        backend: EmbeddingBackend,
        anchor_sets: Sequence[Mapping[int, str]],
        epsilon: float,
        pmf_temperature: float = 1.0,
        min_anchor_sets: int = 4,
    ):
        if len(anchor_sets) < min_anchor_sets:
            raise ValueError(
                f"Нужно минимум {min_anchor_sets} набор(ов) якорей (ssr.min_anchor_sets), "
                f"получено {len(anchor_sets)}."
            )
        self.backend = backend
        self.anchor_sets = list(anchor_sets)
        self.epsilon = epsilon
        self.pmf_temperature = pmf_temperature
        self.min_anchor_sets = min_anchor_sets
        self._anchor_embs_cache: Optional[list[np.ndarray]] = None

    def _get_anchor_embeddings(self) -> list[np.ndarray]:
        if self._anchor_embs_cache is None:
            cache = []
            for anchor_set in self.anchor_sets:
                ordered_phrases = [anchor_set[i] for i in sorted(anchor_set)]
                cache.append(self.backend.encode(ordered_phrases))
            self._anchor_embs_cache = cache
        return self._anchor_embs_cache

    def score_texts(self, texts: Sequence[str]) -> np.ndarray:
        """
        Возвращает PMF-матрицу (len(texts), 5) — по одной строке на текст,
        усреднённую по всем наборам якорей (уровень "PMF ответа" из §4).
        """
        if not texts:
            return np.zeros((0, 5), dtype=np.float64)
        response_embs = self.backend.encode(texts)
        anchor_embs_list = self._get_anchor_embeddings()
        per_set_pmfs = [
            pmf_single_anchor_set(response_embs, anchor_embs, self.epsilon, self.pmf_temperature)
            for anchor_embs in anchor_embs_list
        ]
        return average_pmfs(per_set_pmfs)


# ============================================================================
# Опциональный кросс-чек с пакетом semantic-similarity-rating (PyMC Labs)
# ============================================================================


def cross_check_with_ssr_package(
    texts: Sequence[str],
    anchor_sets: Sequence[Mapping[int, str]],
    epsilon: float,
    pmf_temperature: float,
    our_mean_pmf: np.ndarray,
    warn_threshold: float = 0.02,
) -> Optional[np.ndarray]:
    """
    Опциональная сверка с независимым пакетом `semantic-similarity-rating`
    (https://github.com/pymc-labs/semantic-similarity-rating, MIT, PyMC Labs).

    Если пакет (и его зависимость polars) не установлены — тихо возвращает None:
    это ОПЦИОНАЛЬНАЯ сверка, а не обязательная зависимость пайплайна (см. §4
    спецификации). Если установлен — считает PMF пакетом на тех же текстах и
    якорях и логирует WARNING, если |среднее PMF пакета − среднее нашего PMF|
    > warn_threshold (по умолчанию 0.02) хотя бы по одной точке шкалы.

    Методологическая оговорка (важно для интерпретации возможного предупреждения):
    референсная реализация пакета в некоторых версиях использует эмбеддер
    all-MiniLM-L6-v2 по умолчанию (см. research/2026-07-08/06_tech_implementation.md,
    часть А.3) — не тот же, что настроен в config.yaml для этой панели (обычно
    мультиязычная/русская модель), и в некоторых версиях приводит cosine к [0,1]
    через (1+cos)/2 перед вычитанием минимума, тогда как здесь s_i — «сырой» cosine
    по формуле §4 спецификации. Расхождение выше порога, если оно есть, вероятнее
    всего объясняется этими двумя факторами, а не ошибкой одной из реализаций —
    порог 0.02 это эвристика для привлечения внимания, а не тест на корректность.
    """
    try:
        import polars as pl  # лениво: опциональная тяжёлая зависимость
        from semantic_similarity_rating import ResponseRater  # лениво
    except ImportError:
        logger.debug(
            "semantic-similarity-rating (и/или polars) не установлен — кросс-чек SSR "
            "пропущен. Это нормально: пакет опционален (см. docstring)."
        )
        return None

    ids: list[str] = []
    int_responses: list[int] = []
    sentences: list[str] = []
    for set_idx, phrases in enumerate(anchor_sets):
        for point in sorted(phrases):
            ids.append(f"set{set_idx}")
            int_responses.append(point)
            sentences.append(phrases[point])
    df = pl.DataFrame({"id": ids, "int_response": int_responses, "sentence": sentences})

    rater = ResponseRater(df)
    pkg_pmfs = np.asarray(
        rater.get_response_pmfs(
            reference_set_id="mean",
            llm_responses=list(texts),
            temperature=pmf_temperature,
            epsilon=epsilon,
        )
    )
    pkg_mean = pkg_pmfs.mean(axis=0)
    our_mean_pmf = np.asarray(our_mean_pmf)
    our_mean = our_mean_pmf.mean(axis=0) if our_mean_pmf.ndim > 1 else our_mean_pmf

    diff = np.abs(pkg_mean - our_mean)
    if np.any(diff > warn_threshold):
        logger.warning(
            "Кросс-чек SSR: расхождение среднего PMF с пакетом semantic-similarity-rating "
            "превышает порог %.3f по точке(ам) шкалы %s (наше=%s, пакет=%s). Вероятные "
            "причины — разная embedding-модель и/или разное приведение диапазона cosine "
            "(см. docstring cross_check_with_ssr_package).",
            warn_threshold,
            [i + 1 for i in np.where(diff > warn_threshold)[0].tolist()],
            np.round(our_mean, 4).tolist(),
            np.round(pkg_mean, 4).tolist(),
        )
    return pkg_mean

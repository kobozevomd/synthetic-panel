#!/usr/bin/env python3
"""
test_render_client.py — юнит- и интеграционные тесты scripts/render_client.py
(spec_synthetic-panel_v1.3.md §2.2, задание [B4]).

Запуск:
    python scripts/test_render_client.py
    (или: python -m unittest scripts.test_render_client -v из корня скилла)

Покрытие:
    - парсинг-примитивы (заголовки/таблицы/инлайн-разметка/классификация разделов);
    - светофор разделимости (старый двухуровневый словарь И новый трёхуровневый,
      spec §1.3.3) и декодирование ASCII-спарклайна обратно в относительные высоты;
    - разрешение manifest.json (mode/controls_failed/метаданные) — терпимо к схеме
      до и после spec §1.5 (поле может отсутствовать);
    - проверка целостности блока «Границы этого отчёта» (жёсткое правило б) —
      позитивный случай и два вида порчи (пункт пропал / раздела нет вовсе);
    - happy-path на СКОНСТРУИРОВАННЫХ чистых фикстурах в стиле report.md и
      cjm_report.md (детерминированно проходят cjm_lint — не зависят от состояния
      реального пайплайна на момент прогона теста);
    - жёсткие правила (а): controls_failed и провал cjm_lint -> отказ с понятным
      сообщением и корректным кодом возврата, файл НЕ записывается;
    - CLI end-to-end через subprocess (тот же стиль, что test_cjm_lint.py);
    - реальные пилотные отчёты (runs/biotinal_claims_20260717-1109/report.md,
      runs/cjm_hairloss_demo_20260710-0017/cjm_report.md): проверка ПО ФАКТИЧЕСКОМУ
      состоянию cjm_lint на момент прогона теста (адаптивно — см.
      TestRealPilotReports докстринг за обоснованием) + прямой вызов build_html()
      в обход гейта, чтобы подтвердить, что сама логика рендера (не политика
      отказа) корректно работает на обеих реальных формах отчёта уже сейчас.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_SKILL_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import cjm_lint  # noqa: E402
import render_client as rc  # noqa: E402

RENDER_CLIENT_PATH = _SCRIPTS_DIR / "render_client.py"


# ----------------------------------------------------------------------------
# Вспомогательное: структурная валидация HTML (стандартная библиотека, без
# внешних зависимостей — html.parser есть везде, где есть Python).
# ----------------------------------------------------------------------------


class _StructureChecker(HTMLParser):
    """Проверяет, что каждый открывающий тег закрыт корректно (без внешних либ)."""

    _VOID_OR_SELFCLOSING_HANDLED_SEPARATELY = True

    def __init__(self):
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass  # самозакрывающиеся (<meta/>, <rect/>, ...) — не пушим и не ждём закрытия

    def handle_endtag(self, tag):
        if not self.stack:
            self.errors.append(f"лишний закрывающий тег: {tag}")
            return
        if self.stack[-1] == tag:
            self.stack.pop()
            return
        self.errors.append(f"несовпадение: закрывается {tag}, а на вершине стека {self.stack[-1]}")
        while self.stack and self.stack[-1] != tag:
            self.stack.pop()
        if self.stack:
            self.stack.pop()


def assert_valid_html_structure(test: unittest.TestCase, html_text: str) -> None:
    test.assertTrue(html_text.strip().lower().startswith("<!doctype"), "нет <!doctype html>")
    test.assertIn("</html>", html_text)
    test.assertIn("<title>", html_text)
    checker = _StructureChecker()
    checker.feed(html_text)
    test.assertEqual(checker.stack, [], f"незакрытые теги на конце документа: {checker.stack}")
    test.assertEqual(checker.errors, [], f"ошибки структуры HTML: {checker.errors}")


# ----------------------------------------------------------------------------
# Чистые фикстуры (детерминированно проходят cjm_lint — самопроверка ниже)
# ----------------------------------------------------------------------------

CLEAN_REPORT_MD = """\
# Отчёт: demo_test_study

- **Тип исследования:** claims_ranking
- **Дата прогона:** 2026-07-18T10:00:00+00:00
- **Режим генерации:** agent
- **Модель:** claude-sonnet-5
- **Эмбеддинг-модель (SSR):** paraphrase-multilingual-MiniLM-L12-v2
- **N:** 20 респондентов (1 сегмент(а/ов) × 10/сегмент) × 2 стимул(а/ов) ×
  1 сэмпла = 20 ответов
- **Manifest:** [manifest.json](manifest.json)

> Легенда карты доверия 🟢🟡🔴: качественная симуляция синтетической панели —
> ниже каждый раздел несёт один из трёх статусов.

---

## Главное

- Стимул A устойчиво впереди стимула B в этом сегменте — разрыв воспроизводится
  повторно. 🟢
- Рекомендация — тестировать A на следующем этапе, B доработать. 🟢

---

## 1. Рейтинг стимулов по сегментам 🟢

### Сегмент: Демо-сегмент

Шкала: Готовность купить (purchase_intent)

| Место | Стимул | E[шкала] | ДИ | PMF (1→5) | Отделимость от следующего |
|---:|---|---:|---:|---|---|
| 1 | A: Тестовый стимул один | 3.80 | [3.60, 4.00] | ▁▂▄▇█ | уверенный разрыв |
| 2 | B: Тестовый стимул два | 2.50 | [2.30, 2.70] | ▄█▅▂▁ | — |

---

## 2. Таблица PMF по стимулам и сегментам 🟢

| Стимул | Сегмент | P(1) | P(2) | P(3) | P(4) | P(5) | E[шкала] |
|---|---|---:|---:|---:|---:|---:|---:|
| A: Тестовый стимул один | Демо-сегмент | 0.05 | 0.10 | 0.15 | 0.30 | 0.40 | 3.80 |
| B: Тестовый стимул два | Демо-сегмент | 0.15 | 0.35 | 0.25 | 0.15 | 0.10 | 2.50 |

---

## 3. Качественный разбор 🟢

Смоделированные реакции (тестовая фикстура) расходятся по стимулам.

> «Стимул A звучит убедительно, я бы попробовала.» *(синтетический респондент, демо-сегмент)*

> «Стимул B не вызывает доверия.» *(синтетический респондент, демо-сегмент)*

---

## Что с этим делать 🟢

1. Взять стимул A в дальнейшую разработку.
2. Стимул B — доработать или отклонить.

---

## Границы этого отчёта 🟢

- **Режим:** данные получены в persona-режиме синтетической ИИ-панели — это
  качественная симуляция, а не наблюдение за реальными людьми.
- **Что надёжно:** относительный порядок и сравнение стимулов.
- **Что не надёжно:** абсолютные значения, доли, объёмы потребления.
- **Точность метода:** R=0,72 (~90% теоретического потолка).
- **Маркировка:** это вывод синтетической ИИ-панели, не данные реальных людей.
"""

CLEAN_REPORT_MANIFEST = {
    "mode": "exploratory",
    "model": "claude-sonnet-5",
    "self_reported": True,
    "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
    "anchors_version": 2,
}

CLEAN_CJM_REPORT_MD = """\
# Отчёт: demo_cjm_study — Карта сегментов и AI CJM

- **Категория:** тестовая категория
- **Линтер честности:** пройден (exit 0)

> Легенда карты доверия 🟢🟡🔴 — см. раздел 1 ниже.

---

## 1. Легенда карты доверия 🟢

| Статус | Значение |
|---|---|
| 🟢 | модельное качественное |
| 🟡 | гипотеза для проверки |
| 🔴 | требует данных |

---

## 6. Тест RTB (кандидаты 🟡 → результат SSR-теста)

### 6.2. Результат SSR-теста 🟢

#### Сегмент: Демо-сегмент CJM

Шкала: Готовность купить (purchase_intent)

| Место | RTB-кандидат | E[шкала] | ДИ | PMF (1→5) | Отделимость от следующего |
|---:|---|---:|---:|---|---|
| 1 | rtb1: Первый кандидат | 3.9 | [3.7, 4.1] | ▁▃▅▇█ | на грани |
| 2 | rtb2: Второй кандидат | 2.6 | [2.4, 2.8] | ▄█▅▂▁ | — |

---

## Границы этого отчёта 🟢

### Границы этого отчёта (общие) 🟢

- **Режим:** качественная симуляция, не наблюдение за реальными людьми.
- **Точность метода:** R=0,72 (~90% теоретического потолка).

### Границы этого отчёта (карта сегментов) 🟢

- **Устойчивость сегментов.** Каждый сегмент получен минимум в 3 прогонах
  сегментации.
- **Маркировка.** Это вывод синтетической панели, не данные реальных людей.
"""

CLEAN_CJM_MANIFEST = {"mode": "exploratory"}


# v1.3-форма report_template.md (наблюдалась в references/report_template.md на
# момент написания этого файла — см. issues в итоговом summary сборки): секция 1
# лишилась колонки PMF (только ярлык "Устойчивость разрыва от следующего"),
# E[шкала]/95% CI/PMF переехали в "## Приложение" -> "### Полная статистика по
# сегментам" — ОДНА таблица-рейтинг НА СЕГМЕНТ (сегмент из заголовка, не колонки).
CLEAN_REPORT_MD_V13_SHAPE = """\
# Отчёт: demo_v13_study

**Режим прогона:** 🟡 РАЗВЕДОЧНЫЙ

> Легенда карты доверия 🟢🟡🔴: качественная симуляция синтетической панели.

---

## Главное 🟢

- Стимул A устойчиво впереди стимула B — разрыв воспроизводится повторно (см. раздел 1). 🟢

---

## Паспорт методологии 🟢

Мнение смоделировано для 1 сегмента аудитории (Демо-сегмент) — по 10
смоделированных профилей на сегмент. Самоконтроль прогона: плацебо и ловушка на
своих местах — самоконтроль пройден.

---

## 1. Рейтинг стимулов по сегментам 🟢

### Сегмент: Демо-сегмент

Шкала: Готовность купить (purchase_intent)

| Место | Стимул | Устойчивость разрыва от следующего |
|---:|---|---|
| 1 | A: Тестовый стимул один | уверенный разрыв (Топ-2 воспроизведён в 2 из 2 проверок устойчивости.) |
| 2 | B: Тестовый стимул два | — (Топ-2 воспроизведён в 2 из 2 проверок устойчивости.) |

---

## 2. Качественный разбор 🟢

Смоделированные реакции по стимулам расходятся.

> «Стимул A убедителен.» *(смоделированная реакция, демо-сегмент)*

---

## Что с этим делать 🟢

1. Взять стимул A в дальнейшую разработку.

---

## Границы этого отчёта 🟢

- **Режим:** данные получены в persona-режиме синтетической ИИ-панели.
- **Точность метода:** R=0,72 (~90% теоретического потолка).

---

## Приложение 🟢

### Технический паспорт прогона

- **Модель:** claude-sonnet-5
- **N:** 20 смоделированных профилей

### Полная статистика по сегментам (E[шкала], 95% CI, PMF)

#### Сегмент: Демо-сегмент

| Место | Стимул | E[шкала] | 95% CI | PMF (1→5) | P(A>B) от следующего | Ярлык |
|---:|---|---:|---:|---|---:|---|
| 1 | A: Тестовый стимул один | 3.80 | [3.60, 4.00] | ▁▂▄▇█ | 0.95 | уверенный разрыв |
| 2 | B: Тестовый стимул два | 2.50 | [2.30, 2.70] | ▄█▅▂▁ | — | — |
"""

CLEAN_REPORT_MD_V13_MANIFEST = {"mode": "exploratory"}


def _write(tmp_dir: Path, name: str, content: str) -> Path:
    path = tmp_dir / name
    path.write_text(content, encoding="utf-8")
    return path


def _write_manifest(tmp_dir: Path, data: dict) -> Path:
    path = tmp_dir / "manifest.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ----------------------------------------------------------------------------
# Фикстуры действительно чистые (самопроверка — если кто-то отредактирует
# константы выше и случайно внесёт нарушение, этот тест поймает раньше, чем
# запутает остальные тесты непонятным падением)
# ----------------------------------------------------------------------------


class TestFixturesAreLintClean(unittest.TestCase):
    def test_clean_report_md_passes_cjm_lint(self):
        violations = cjm_lint.lint_text(CLEAN_REPORT_MD)
        self.assertEqual(violations, [], f"фикстура CLEAN_REPORT_MD должна быть чистой: {violations}")

    def test_clean_cjm_report_md_passes_cjm_lint(self):
        violations = cjm_lint.lint_text(CLEAN_CJM_REPORT_MD)
        self.assertEqual(violations, [], f"фикстура CLEAN_CJM_REPORT_MD должна быть чистой: {violations}")


# ----------------------------------------------------------------------------
# Markdown: примитивы
# ----------------------------------------------------------------------------


class TestMarkdownPrimitives(unittest.TestCase):
    def test_strip_html_comments_removes_multiline(self):
        text = "A\n<!-- скрытая\nмногострочная -->\nB\n"
        out = rc.strip_html_comments(text)
        self.assertNotIn("скрытая", out)
        self.assertIn("A", out)
        self.assertIn("B", out)

    def test_find_headings_levels_and_text(self):
        text = "# Заголовок 1\n\n## Раздел 2\n\n### Под 3\n"
        heads = rc.find_headings(text)
        self.assertEqual([h.level for h in heads], [1, 2, 3])
        self.assertEqual(heads[1].text, "Раздел 2")

    def test_top_level_sections_split_on_h2_boundaries(self):
        text = "# T\n\nintro\n\n## A\nbody a\n\n## B\nbody b\n"
        heads = rc.find_headings(text)
        sections = rc.top_level_sections(text, heads)
        self.assertEqual([h.text for h, _ in sections], ["A", "B"])
        self.assertIn("body a", sections[0][1])
        self.assertNotIn("body b", sections[0][1])

    def test_classify_heading_variants(self):
        cases = {
            "Главное": "highlights",
            "Что с этим делать": "recommendations",
            "1. Что делать дальше": "recommendations",
            "Границы этого отчёта": "disclaimer",
            "9. Границы этого отчёта (карта сегментов / AI CJM)": "disclaimer",
            "Легенда карты доверия": "trust_legend",
            "Паспорт методологии": "method_passport",
            "3. Сегменты: сводная таблица": "generic",
        }
        for heading_text, expected in cases.items():
            with self.subTest(heading=heading_text):
                self.assertEqual(rc.classify_heading(heading_text), expected)

    def test_classify_heading_tolerates_yo_ye_variation(self):
        # "отчёта" и "отчета" — должны нормализоваться одинаково
        self.assertEqual(rc.classify_heading("Границы этого отчета"), "disclaimer")

    def test_classify_heading_appendix_section(self):
        self.assertEqual(rc.classify_heading("Приложение"), "appendix_section")
        self.assertEqual(rc.classify_heading("10. Приложение"), "appendix_section")

    def test_inline_bold_italic_code(self):
        html_out = rc.inline_md_to_html("**жир** и *курсив* и `код`")
        self.assertIn("<strong>жир</strong>", html_out)
        self.assertIn("<em>курсив</em>", html_out)
        self.assertIn("<code>код</code>", html_out)

    def test_inline_escapes_html_special_chars(self):
        html_out = rc.inline_md_to_html("A < B & C > D")
        self.assertIn("&lt;", html_out)
        self.assertIn("&amp;", html_out)
        self.assertIn("&gt;", html_out)

    def test_inline_link_same_label_and_href_collapses(self):
        html_out = rc.inline_md_to_html("[manifest.json](manifest.json)")
        self.assertIn('<code class="ref-path">manifest.json</code>', html_out)
        self.assertNotIn("ref-link", html_out)

    def test_inline_link_different_label_and_href_keeps_both(self):
        html_out = rc.inline_md_to_html("[тут](manifest.json)")
        self.assertIn("ref-link", html_out)
        self.assertIn("<code>manifest.json</code>", html_out)

    def test_find_tables_in_lines_requires_separator_row(self):
        lines = ["| a | b |", "not a separator", "| 1 | 2 |"]
        tables = rc.find_tables_in_lines(lines)
        self.assertEqual(tables, [], "без строки-разделителя |---|---| это не таблица")

    def test_find_tables_in_lines_parses_header_and_rows(self):
        lines = ["| a | b |", "|---|---|", "| 1 | 2 |", "| 3 | 4 |"]
        tables = rc.find_tables_in_lines(lines)
        self.assertEqual(len(tables), 1)
        t = tables[0]
        self.assertEqual(t.header, ["a", "b"])
        self.assertEqual(t.rows, [["1", "2"], ["3", "4"]])
        self.assertEqual(t.end, 4)

    def test_find_col_word_boundary_avoids_false_positive_inside_kandidat(self):
        # "кандидат" содержит подстроку "ди" — не должно матчиться как колонка "ДИ"
        header = ["Место", "RTB-кандидат", "E[шкала]", "ДИ", "PMF (1→5)", "Отделимость"]
        idx = rc._find_col(header, substrings=("95%",), word_tokens=("ci", "ди"))
        self.assertEqual(idx, 3, "ДИ должен матчиться как отдельный столбец, не 'RTB-кандидат'")

    def test_is_ranking_table_true_for_report_style_header(self):
        header = ["Место", "Стимул", "E[шкала]", "95% CI", "PMF (1→5)", "Отделимость от следующего"]
        self.assertTrue(rc.is_ranking_table(header))

    def test_is_ranking_table_false_for_rtb_candidates_table(self):
        header = ["id", "Текст", "Целевой барьер", "Целевой benefit", "Тип RTB"]
        self.assertFalse(rc.is_ranking_table(header))

    def test_is_pmf_table_true_only_with_all_five_columns(self):
        full = ["Стимул", "Сегмент", "P(1)", "P(2)", "P(3)", "P(4)", "P(5)", "E[шкала]"]
        partial = ["Стимул", "Сегмент", "P(1)", "P(2)"]
        self.assertTrue(rc.is_pmf_table(full))
        self.assertFalse(rc.is_pmf_table(partial))

    def test_parse_header_meta_joins_wrapped_lines(self):
        preamble = (
            "\n- **Устойчивость:** 3 прогона, режим —\n"
            "  approximated (демо-приближение: важное\n"
            "  продолжение фразы, которое не должно потеряться).\n"
        )
        meta = rc.parse_header_meta(preamble)
        self.assertEqual(len(meta), 1)
        label, value = meta[0]
        self.assertEqual(label, "Устойчивость")
        self.assertIn("approximated", value)
        self.assertIn("не должно потеряться", value, "word-wrap продолжение значения не должно обрезаться")


# ----------------------------------------------------------------------------
# Светофор разделимости + PMF (точный join и ASCII-декодирование)
# ----------------------------------------------------------------------------


class TestSeparabilityAndPmf(unittest.TestCase):
    def test_classify_separability_new_three_tier_vocabulary(self):
        self.assertEqual(rc.classify_separability("уверенный разрыв")[0], "confident")
        self.assertEqual(rc.classify_separability("на грани")[0], "borderline")
        self.assertEqual(rc.classify_separability("в пределах шума")[0], "noise")

    def test_classify_separability_old_two_tier_vocabulary(self):
        self.assertEqual(rc.classify_separability("разделимо (CI не пересекаются)")[0], "confident")
        self.assertEqual(rc.classify_separability("не разделимо (CI пересекаются)")[0], "noise")

    def test_classify_separability_dash_is_none_tier(self):
        tier, display = rc.classify_separability("—")
        self.assertEqual(tier, "none")
        self.assertEqual(display, "—")

    def test_classify_separability_dash_with_stability_note_is_none_tier(self):
        # Реальная форма report.py v1.3 для последней строки ранжирования: не
        # голый "—", а "— (Топ-N воспроизведён в X из Y проверок…)" — см.
        # test_report.py::TestRenderSmoke (найдено при регрессионном прогоне).
        text = "— (Топ-2 воспроизведён в 2 из 2 проверок устойчивости.)"
        tier, display = rc.classify_separability(text)
        self.assertEqual(tier, "none")
        self.assertEqual(display, text, "пояснение в скобках должно сохраниться, не заменяться на голое тире")

    def test_classify_separability_unknown_text_preserved_verbatim(self):
        tier, display = rc.classify_separability("что-то совсем новое")
        self.assertEqual(tier, "unknown")
        self.assertEqual(display, "что-то совсем новое", "неизвестная формулировка не должна переписываться")

    def test_decode_ascii_bar_matches_report_py_encoding_roundtrip(self):
        # report.py::ascii_bar кодирует уровень round(p/max_p * 7); проверяем обратное
        # декодирование на известном спарклайне (монотонно возрастающий PMF).
        bar = "▁▂▄▆█"
        levels = rc.decode_ascii_bar(bar)
        self.assertIsNotNone(levels)
        self.assertEqual(len(levels), 5)
        self.assertEqual(levels, sorted(levels), "монотонный спарклайн должен декодироваться в монотонные высоты")
        self.assertAlmostEqual(levels[0], 0.0)
        self.assertAlmostEqual(levels[-1], 1.0)

    def test_decode_ascii_bar_rejects_wrong_length(self):
        self.assertIsNone(rc.decode_ascii_bar("▁▂▄"))

    def test_build_pmf_lookup_and_exact_join_overrides_ascii(self):
        table = rc.ParsedTable(
            header=["Стимул", "Сегмент", "P(1)", "P(2)", "P(3)", "P(4)", "P(5)", "E[шкала]"],
            rows=[["A: текст стимула", "Демо", "0.1", "0.1", "0.1", "0.1", "0.6", "3.9"]],
            start=0,
            end=2,
        )
        lookup = rc.build_pmf_lookup([table])
        self.assertIn(("демо", "a"), lookup)
        self.assertEqual(lookup[("демо", "a")], [0.1, 0.1, 0.1, 0.1, 0.6])

        rank_table = rc.ParsedTable(
            header=["Место", "Стимул", "E[шкала]", "ДИ", "PMF (1→5)", "Отделимость от следующего"],
            rows=[["1", "A: текст стимула", "3.9", "[3.7, 4.1]", "▁▁▁▁█", "—"]],
            start=0,
            end=2,
        )
        rows = rc.extract_rank_rows(rank_table, lookup, segment_key="демо")
        self.assertEqual(rows[0].pmf_source, "exact")
        # точный источник (0.6 макс) должен победить над декодированием ASCII-бара
        self.assertAlmostEqual(max(rows[0].pmf), 1.0)

    def test_extract_rank_rows_falls_back_to_ascii_when_no_exact_pmf(self):
        rank_table = rc.ParsedTable(
            header=["Место", "RTB-кандидат", "E[шкала]", "ДИ", "PMF (1→5)", "Отделимость от следующего"],
            rows=[["1", "rtb1: текст", "3.9", "[3.7, 4.1]", "▁▂▄▆█", "на грани"]],
            start=0,
            end=2,
        )
        rows = rc.extract_rank_rows(rank_table, pmf_lookup={}, segment_key="сегмент, которого нет в lookup")
        self.assertEqual(rows[0].pmf_source, "ascii")
        self.assertIsNotNone(rows[0].pmf)

    def test_extract_rank_rows_handles_missing_pmf_gracefully(self):
        rank_table = rc.ParsedTable(
            header=["Место", "Стимул", "E[шкала]", "Отделимость от следующего"],
            rows=[["1", "A: текст", "3.9", "уверенный разрыв"]],
            start=0,
            end=2,
        )
        rows = rc.extract_rank_rows(rank_table, pmf_lookup={}, segment_key=None)
        self.assertEqual(rows[0].pmf_source, "none")
        self.assertIsNone(rows[0].pmf)

    def test_render_pmf_bars_svg_has_five_fill_rects(self):
        svg = rc.render_pmf_bars_svg([0.2, 0.4, 0.6, 0.8, 1.0])
        self.assertTrue(svg.startswith("<svg"))
        self.assertEqual(svg.count('class="bar-fill"'), 5)
        self.assertEqual(svg.count('class="bar-track"'), 5)

    def test_build_pmf_lookup_from_document_bridges_ascii_pmf_across_sections(self):
        # v1.3-форма: секция 1 без PMF, "Приложение" содержит ASCII PMF под тем же
        # заголовком "Сегмент: X" (не колонкой) — см. build_pmf_lookup_from_document.
        text = (
            "### Сегмент: Демо\n\n"
            "| Место | Стимул | Устойчивость разрыва от следующего |\n"
            "|---:|---|---|\n"
            "| 1 | A: текст | уверенный разрыв |\n\n"
            "#### Сегмент: Демо\n\n"
            "| Место | Стимул | E[шкала] | PMF (1→5) | Ярлык |\n"
            "|---:|---|---:|---|---|\n"
            "| 1 | A: текст | 3.9 | ▁▂▄▆█ | уверенный разрыв |\n"
        )
        lookup = rc.build_pmf_lookup_from_document(text)
        self.assertIn(("демо", "a"), lookup)
        self.assertEqual(lookup[("демо", "a")], rc.decode_ascii_bar("▁▂▄▆█"))

    def test_build_pmf_lookup_from_document_prefers_exact_over_ascii(self):
        text = (
            "| Стимул | Сегмент | P(1) | P(2) | P(3) | P(4) | P(5) | E[шкала] |\n"
            "|---|---|---:|---:|---:|---:|---:|---:|\n"
            "| A: текст | Демо | 0.1 | 0.1 | 0.1 | 0.1 | 0.6 | 3.9 |\n\n"
            "### Сегмент: Демо\n\n"
            "| Место | Стимул | E[шкала] | PMF (1→5) |\n"
            "|---:|---|---:|---|\n"
            "| 1 | A: текст | 3.9 | ▁▁▁▁█ |\n"
        )
        lookup = rc.build_pmf_lookup_from_document(text)
        self.assertEqual(lookup[("демо", "a")], [0.1, 0.1, 0.1, 0.1, 0.6], "точные P(1)..P(5) не должны затираться ASCII-декодом")

    def test_render_body_cardify_rankings_false_keeps_plain_table(self):
        text = (
            "| Место | Стимул | E[шкала] | Ярлык |\n"
            "|---:|---|---:|---|\n"
            "| 1 | A: текст | 3.9 | уверенный разрыв |\n"
        )
        html_out, n_cards = rc.render_body(text, pmf_lookup={}, appendix_tables=[], cardify_rankings=False)
        self.assertEqual(n_cards, 0)
        self.assertNotIn("segment-card", html_out)
        self.assertIn("<table", html_out)
        self.assertIn("<th>Ярлык</th>", html_out)


# ----------------------------------------------------------------------------
# manifest.json: режим/контроли/метаданные
# ----------------------------------------------------------------------------


class TestManifestResolution(unittest.TestCase):
    def test_resolve_mode_explicit_validated(self):
        info = rc.resolve_mode({"mode": "validated"})
        self.assertEqual(info.mode, "validated")
        self.assertFalse(info.inferred)

    def test_resolve_mode_missing_field_defaults_exploratory_and_marks_inferred(self):
        info = rc.resolve_mode({"study_name": "x"})
        self.assertEqual(info.mode, "exploratory")
        self.assertTrue(info.inferred)

    def test_resolve_mode_missing_manifest_defaults_exploratory(self):
        info = rc.resolve_mode(None)
        self.assertEqual(info.mode, "exploratory")
        self.assertTrue(info.inferred)

    def test_resolve_mode_invalid_value_falls_back_safely(self):
        info = rc.resolve_mode({"mode": "не пойми что"})
        self.assertEqual(info.mode, "exploratory")
        self.assertTrue(info.inferred)

    def test_resolve_controls_failed_top_level_true(self):
        self.assertTrue(rc.resolve_controls_failed({"controls_failed": True}))

    def test_resolve_controls_failed_nested_in_stage(self):
        manifest = {"stages": {"controls": {"controls_failed": True}}}
        self.assertTrue(rc.resolve_controls_failed(manifest))

    def test_resolve_controls_failed_absent_is_false(self):
        self.assertFalse(rc.resolve_controls_failed({"study_name": "x"}))
        self.assertFalse(rc.resolve_controls_failed(None))

    def test_resolve_controls_failed_explicit_false_is_false(self):
        self.assertFalse(rc.resolve_controls_failed({"controls_failed": False}))

    def test_manifest_meta_reports_unfixed_model_when_absent(self):
        meta = dict(rc.manifest_meta({"stages": {"generate": {}}}))
        self.assertIn("не зафиксирована", meta.get("Модель", ""))

    def test_manifest_meta_none_returns_empty(self):
        self.assertEqual(rc.manifest_meta(None), [])


class TestModeBadgeRendering(unittest.TestCase):
    """render_mode_badge() напрямую — все три варианта бейджа режима (spec §1.5)."""

    def test_validated_badge(self):
        badge = rc.render_mode_badge(rc.ModeInfo(mode="validated", inferred=False))
        self.assertIn("badge-validated", badge)
        self.assertIn("Валидированный режим", badge)
        self.assertNotIn("не размечен", badge)

    def test_exploratory_explicit_badge(self):
        badge = rc.render_mode_badge(rc.ModeInfo(mode="exploratory", inferred=False))
        self.assertIn("badge-exploratory", badge)
        self.assertIn("Разведочный режим", badge)
        self.assertNotIn("не размечен", badge)

    def test_exploratory_inferred_badge_carries_note(self):
        badge = rc.render_mode_badge(rc.ModeInfo(mode="exploratory", inferred=True))
        self.assertIn("badge-exploratory", badge)
        self.assertIn("не размечен в manifest", badge)


class TestStimulusKindBadge(unittest.TestCase):
    """
    resolve_stimulus_kind()/render_stimulus_kind_badge() (spec_synthetic-panel_v1.4.md
    §1.1/§1.4) — ИНТЕГРАЦИОННЫЙ ФИКС [F2, v1.4 DoD п.6]: до этого фикса build_html
    молча теряла строку «Стимулы: 🖼️ ВИЗУАЛЬНЫЕ (проба зрения: ...)» из шапки
    report.md — она физический simple-абзац преамбулы (не bullet-list, не
    blockquote), а ни parse_header_meta, ни parse_header_note его не подхватывают
    (см. докстринг resolve_stimulus_kind). Найдено на реальном end-to-end визуальном
    пилоте (studies/visual_smoke.yaml), не в этих тестах — тесты ниже фиксируют фикс.
    """

    def test_resolve_none_manifest_is_text_kind(self):
        info = rc.resolve_stimulus_kind(None)
        self.assertEqual(info.kind, "text")
        self.assertFalse(info.vision_failed)
        self.assertFalse(info.has_vision_check)

    def test_resolve_manifest_without_stimulus_kind_field_falls_back_to_text(self):
        info = rc.resolve_stimulus_kind({"mode": "exploratory"})
        self.assertEqual(info.kind, "text")

    def test_resolve_image_kind_with_passed_vision_check(self):
        info = rc.resolve_stimulus_kind({"stimulus_kind": "image", "vision_check": {"vision_failed": False}})
        self.assertEqual(info.kind, "image")
        self.assertFalse(info.vision_failed)
        self.assertTrue(info.has_vision_check)

    def test_resolve_mixed_kind_with_failed_vision_check(self):
        info = rc.resolve_stimulus_kind({"stimulus_kind": "mixed", "vision_check": {"vision_failed": True}})
        self.assertEqual(info.kind, "mixed")
        self.assertTrue(info.vision_failed)

    def test_resolve_unknown_kind_value_falls_back_to_text(self):
        # схема защищена run_study.py, но рендер не должен упасть на мусорном значении
        info = rc.resolve_stimulus_kind({"stimulus_kind": "garbage"})
        self.assertEqual(info.kind, "text")

    def test_badge_empty_for_text_kind(self):
        self.assertEqual(rc.render_stimulus_kind_badge(rc.StimulusKindInfo("text", False, False)), "")

    def test_badge_image_kind_passed(self):
        badge = rc.render_stimulus_kind_badge(rc.StimulusKindInfo("image", False, True))
        self.assertIn("badge-visual", badge)
        self.assertNotIn("badge-visual-warn", badge)
        self.assertIn("Визуальные стимулы", badge)
        self.assertIn("проба зрения: пройдена", badge)

    def test_badge_mixed_kind_label(self):
        badge = rc.render_stimulus_kind_badge(rc.StimulusKindInfo("mixed", False, True))
        self.assertIn("Смешанные стимулы", badge)

    def test_badge_vision_failed_uses_warn_style(self):
        badge = rc.render_stimulus_kind_badge(rc.StimulusKindInfo("image", True, True))
        self.assertIn("badge-visual-warn", badge)
        self.assertIn("проба зрения: НЕ пройдена", badge)

    def test_build_html_includes_badge_for_image_study_and_omits_for_text_study(self):
        """
        Сквозная проверка через build_html() целиком (не только напрямую вызванные
        resolve/render) — именно build_html молча теряла строку до фикса (см.
        докстринг класса), поэтому регрессия должна ловиться на этом уровне, а не
        только на уровне чистых функций.
        """
        body = (
            "**Режим прогона:** 🟡 РАЗВЕДОЧНЫЙ\n\n"
            "**Стимулы:** 🖼️ ВИЗУАЛЬНЫЕ (проба зрения: пройдена)\n\n"
            "## Главное\n\nПункт.\n\n---\n\n"
            "## Границы этого отчёта\n\n"
            "- **Режим:** качественная симуляция, не наблюдение за реальными людьми.\n"
            "- **Точность метода:** R=0,72 (~90% теоретического потолка).\n"
        )
        # Проверяем ИСПОЛЬЗОВАНИЕ класса в разметке (`class="mode-badge badge-visual"`),
        # а не голую подстроку "badge-visual" — та ВСЕГДА присутствует в статичном
        # <style>-блоке (правило .badge-visual {...} печатается на каждый рендер
        # независимо от контента), поэтому bare-подстрока ничего не различает.
        badge_usage = 'class="mode-badge badge-visual"'
        with tempfile.TemporaryDirectory() as td:
            report_path = Path(td) / "report.md"
            report_path.write_text(f"# Отчёт: demo\n\n{body}", encoding="utf-8")
            html_image = rc.build_html(report_path, {"mode": "exploratory", "stimulus_kind": "image"})
            self.assertIn(badge_usage, html_image)
            self.assertIn("Визуальные стимулы", html_image)

            html_text = rc.build_html(report_path, {"mode": "exploratory", "stimulus_kind": "text"})
            self.assertNotIn(badge_usage, html_text)
            self.assertNotIn("Визуальные стимулы", html_text)

            html_no_manifest = rc.build_html(report_path, None)
            self.assertNotIn(badge_usage, html_no_manifest)
            self.assertNotIn("Визуальные стимулы", html_no_manifest)


# ----------------------------------------------------------------------------
# Жёсткое правило (б): целостность дисклеймеров
# ----------------------------------------------------------------------------

_DISCLAIMER_BODY_OK = """
- **Режим:** качественная симуляция, не наблюдение за реальными людьми.
- **Точность метода:** R=0,72 (~90% теоретического потолка).
"""


class TestDisclaimerIntegrity(unittest.TestCase):
    def test_passes_when_all_bullets_present_in_rendered_text(self):
        rendered, _ = rc.render_body(_DISCLAIMER_BODY_OK, pmf_lookup={}, appendix_tables=[])
        # не должно бросать исключение
        rc.assert_disclaimers_not_shortened([("Границы этого отчёта", _DISCLAIMER_BODY_OK)], [rendered])

    def test_raises_when_a_bullet_is_missing_from_rendered_text(self):
        rendered_missing_second_bullet = "<p>Режим: качественная симуляция, не наблюдение за реальными людьми.</p>"
        with self.assertRaises(rc.RenderRefused) as ctx:
            rc.assert_disclaimers_not_shortened(
                [("Границы этого отчёта", _DISCLAIMER_BODY_OK)], [rendered_missing_second_bullet]
            )
        self.assertEqual(ctx.exception.exit_code, rc.EXIT_DISCLAIMER_INTEGRITY)

    def test_raises_when_no_bullets_found_at_all(self):
        body_without_bullets = "Просто абзац прозы без единого пункта-маркера."
        with self.assertRaises(rc.RenderRefused) as ctx:
            rc.assert_disclaimers_not_shortened([("Границы этого отчёта", body_without_bullets)], [""])
        self.assertEqual(ctx.exception.exit_code, rc.EXIT_DISCLAIMER_INTEGRITY)

    def test_tolerates_code_span_adjacent_to_punctuation(self):
        # Регрессионный тест: "(`vertical: pharma_rx`)" ранее ломался наивным
        # снятием тегов (см. историю правки _plain_text — блочные vs инлайн-теги).
        body = "\n- **Фарма-гейт.** Ограничение для (`vertical: pharma_rx`) категорий.\n"
        rendered, _ = rc.render_body(body, pmf_lookup={}, appendix_tables=[])
        rc.assert_disclaimers_not_shortened([("Границы этого отчёта", body)], [rendered])

    def test_tolerates_underscore_identifiers(self):
        # Регрессионный тест: "temperature_control=false" не должен ложно рваться
        # на "temperaturecontrol" из-за стрипа "_" как разметки курсива.
        body = "\n- **Прогон:** модель не зафиксирована (agent-режим, temperature_control=false).\n"
        rendered, _ = rc.render_body(body, pmf_lookup={}, appendix_tables=[])
        rc.assert_disclaimers_not_shortened([("Границы этого отчёта", body)], [rendered])


# ----------------------------------------------------------------------------
# Happy path: build_html_or_refuse на чистых фикстурах (детерминированно)
# ----------------------------------------------------------------------------


class TestBuildHtmlHappyPathReport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.report_path = _write(self.tmp_dir, "report.md", CLEAN_REPORT_MD)
        _write_manifest(self.tmp_dir, CLEAN_REPORT_MANIFEST)
        self.html = rc.build_html_or_refuse(self.report_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_produces_valid_html_structure(self):
        assert_valid_html_structure(self, self.html)

    def test_has_mode_badge_exploratory_explicit_not_inferred(self):
        self.assertIn("badge-exploratory", self.html)
        self.assertIn("Разведочный режим", self.html)
        self.assertNotIn("не размечен в manifest", self.html, "mode задан явно — пометка 'не размечен' не должна появляться")

    def test_has_how_to_read_box_with_three_lines(self):
        self.assertIn("Как читать этот отчёт", self.html)
        for line in rc.HOW_TO_READ_LINES:
            self.assertIn(line[:40], self.html)

    def test_has_highlights_section(self):
        self.assertIn("highlights-box", self.html)
        self.assertIn("Главное", self.html)

    def test_has_recommendations_section(self):
        self.assertIn("recommendations-box", self.html)
        self.assertIn("Что с этим делать", self.html)

    def test_has_segment_card_with_traffic_light_and_svg_bar(self):
        self.assertIn("segment-card", self.html)
        self.assertIn("pmf-svg", self.html)
        self.assertIn("dot-confident", self.html, "уверенный разрыв должен дать зелёный светофор")
        self.assertIn("dot-none", self.html, "последняя строка ('—') должна дать нейтральную точку")

    def test_qualitative_quotes_rendered_as_quote_cards(self):
        self.assertIn("quote-card", self.html)
        self.assertIn("Стимул A звучит убедительно", self.html)

    def test_disclaimer_section_present_with_canonical_accuracy_phrase(self):
        self.assertIn("disclaimer-block", self.html)
        self.assertIn("R=0,72 (~90% теоретического потолка)", self.html)

    def test_appendix_contains_pmf_table_moved_out_of_main_flow(self):
        self.assertIn('details class="appendix"', self.html)
        self.assertIn("P(1)", self.html)

    def test_cli_writes_file_and_returns_success(self):
        result = subprocess.run(
            [sys.executable, str(RENDER_CLIENT_PATH), "--report", str(self.report_path)],
            cwd=str(self.tmp_dir),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, rc.EXIT_OK, result.stderr)
        out_path = self.report_path.with_suffix(".html")
        self.assertTrue(out_path.exists())
        assert_valid_html_structure(self, out_path.read_text(encoding="utf-8"))


class TestForwardCompatibleV13ReportShape(unittest.TestCase):
    """
    Фикстура списана с ФАКТИЧЕСКОГО references/report_template.md, найденного в
    дереве на момент написания этого файла (см. issues итогового summary) — секция
    1 лишилась колонки PMF (только ярлык "Устойчивость разрыва от следующего"),
    числа переехали в "## Приложение". Использует build_html() НАПРЯМУЮ (минуя
    cjm_lint-гейт) по той же причине, что и TestRealPilotReports: заголовок
    "95% CI" в таблице приложения — то же самое ложное срабатывание правила 1
    cjm_lint, что и у обоих реальных пилотных отчётов (не про рендер — про
    рассинхрон линтера с форматом таблиц report.py, см. issues); тест здесь
    целенаправленно проверяет ДВИЖОК рендера на новой форме, а не политику отказа.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp_dir = Path(cls._tmp.name)
        report_path = _write(tmp_dir, "report.md", CLEAN_REPORT_MD_V13_SHAPE)
        _write_manifest(tmp_dir, CLEAN_REPORT_MD_V13_MANIFEST)
        cls.html = rc.build_html(report_path, CLEAN_REPORT_MD_V13_MANIFEST)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_produces_valid_html_structure(self):
        assert_valid_html_structure(self, self.html)

    def test_simplified_section1_table_recognised_as_ranking_card(self):
        self.assertIn("segment-card", self.html)
        self.assertIn("dot-confident", self.html)
        self.assertIn("dot-none", self.html)

    def test_section1_card_bar_bridged_from_appendix_pmf_not_missing(self):
        # Ключевая регрессия: секция 1 v1.3 не содержит своей колонки PMF —
        # бар обязан подтянуться из таблицы "Приложение" по сегменту+id стимула.
        self.assertIn("pmf-svg", self.html)
        self.assertNotIn("нет данных PMF в отчёте", self.html)

    def test_appendix_section_collapsed_not_in_main_flow(self):
        self.assertIn('details class="appendix"', self.html)
        self.assertIn("Технический паспорт прогона", self.html)
        self.assertIn("Полная статистика по сегментам", self.html)
        # Столбец "Ярлык" существует только в детальной таблице приложения — должен
        # встречаться ВНУТРИ details, а не как отдельный видимый раздел основного потока.
        appendix_start = self.html.index('details class="appendix"')
        yarlyk_pos = self.html.index("<th>Ярлык</th>")
        self.assertGreater(yarlyk_pos, appendix_start)

    def test_appendix_table_kept_as_plain_table_not_re_cardified(self):
        # Регрессия: таблица приложения технически совпадает с сигнатурой
        # ranking-таблицы (есть "Место"+"E[") — но карточка ПРЯЧЕТ CI/P(A>B),
        # ради которых читатель разворачивает приложение (см. docstring
        # render_body::cardify_rankings). Должна остаться обычной HTML-таблицей
        # с этими колонками видимыми, а не второй карточкой того же сегмента.
        self.assertIn("<th>95% CI</th>", self.html)
        self.assertIn("<th>P(A&gt;B) от следующего</th>", self.html)
        # ровно одна карточка на сегмент (из секции 1) — приложение не должно
        # добавить вторую карточку для того же "Демо-сегмент"
        self.assertEqual(self.html.count('class="segment-card"'), 1)

    def test_highlights_passport_recommendations_and_disclaimers_present(self):
        self.assertIn("highlights-box", self.html)
        self.assertIn("method-passport", self.html)
        self.assertIn("recommendations-box", self.html)
        self.assertIn("disclaimer-block", self.html)
        self.assertIn("R=0,72 (~90% теоретического потолка)", self.html)


class TestBuildHtmlHappyPathCjmReport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.report_path = _write(self.tmp_dir, "cjm_report.md", CLEAN_CJM_REPORT_MD)
        _write_manifest(self.tmp_dir, CLEAN_CJM_MANIFEST)
        self.html = rc.build_html_or_refuse(self.report_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_produces_valid_html_structure(self):
        assert_valid_html_structure(self, self.html)

    def test_has_trust_legend_section(self):
        self.assertIn("trust-legend", self.html)
        self.assertIn("Легенда карты доверия", self.html)

    def test_ranking_card_uses_ascii_fallback_when_no_exact_pmf_table(self):
        self.assertIn("segment-card", self.html)
        self.assertIn("dot-borderline", self.html, "'на грани' должен дать жёлтый светофор")

    def test_merged_disclaimer_subsections_both_present(self):
        self.assertIn("Границы этого отчёта (общие)", self.html)
        self.assertIn("Границы этого отчёта (карта сегментов)", self.html)
        self.assertIn("R=0,72 (~90% теоретического потолка)", self.html)


# ----------------------------------------------------------------------------
# Жёсткое правило (а): controls_failed / провал cjm_lint -> отказ
# ----------------------------------------------------------------------------


class TestHardRuleARefusals(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_controls_failed_manifest_refuses_before_lint(self):
        report_path = _write(self.tmp_dir, "report.md", CLEAN_REPORT_MD)
        _write_manifest(self.tmp_dir, {"mode": "exploratory", "controls_failed": True})
        with self.assertRaises(rc.RenderRefused) as ctx:
            rc.build_html_or_refuse(report_path)
        self.assertEqual(ctx.exception.exit_code, rc.EXIT_CONTROLS_FAILED)
        self.assertIn("controls_failed", str(ctx.exception))
        self.assertIn("самоконтроль", str(ctx.exception))

    def test_lint_failing_report_refuses_with_lint_exit_code(self):
        dirty_report = CLEAN_REPORT_MD.replace(
            "- **Что не надёжно:** абсолютные значения, доли, объёмы потребления.",
            "- **Что не надёжно:** абсолютные значения — например, доля 42% в опросах без источника.",
        )
        # убеждаемся, что фикстура действительно грязная (иначе тест ничего не проверяет)
        self.assertTrue(cjm_lint.lint_text(dirty_report), "фикстура должна была стать нарушающей правило 1")

        report_path = _write(self.tmp_dir, "report.md", dirty_report)
        _write_manifest(self.tmp_dir, CLEAN_REPORT_MANIFEST)
        with self.assertRaises(rc.RenderRefused) as ctx:
            rc.build_html_or_refuse(report_path)
        self.assertEqual(ctx.exception.exit_code, rc.EXIT_LINT_FAILED)
        self.assertIn("cjm_lint", str(ctx.exception))

    def test_cli_refusal_does_not_write_output_file_and_returns_nonzero(self):
        report_path = _write(self.tmp_dir, "report.md", CLEAN_REPORT_MD)
        _write_manifest(self.tmp_dir, {"mode": "exploratory", "controls_failed": True})
        result = subprocess.run(
            [sys.executable, str(RENDER_CLIENT_PATH), "--report", str(report_path)],
            cwd=str(self.tmp_dir),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, rc.EXIT_CONTROLS_FAILED)
        self.assertIn("ОТКАЗ", result.stderr)
        self.assertFalse(report_path.with_suffix(".html").exists())


# ----------------------------------------------------------------------------
# Жёсткое правило (б) на уровне полного пайплайна: раздела границ нет вовсе
# ----------------------------------------------------------------------------


class TestHardRuleBNoDisclaimerSectionAtAll(unittest.TestCase):
    def test_report_without_any_disclaimer_heading_refuses(self):
        no_disclaimer_report = CLEAN_REPORT_MD.split("## Границы этого отчёта")[0]
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            report_path = _write(tmp_dir, "report.md", no_disclaimer_report)
            _write_manifest(tmp_dir, CLEAN_REPORT_MANIFEST)
            with self.assertRaises(rc.RenderRefused) as ctx:
                rc.build_html_or_refuse(report_path)
            self.assertEqual(ctx.exception.exit_code, rc.EXIT_NO_DISCLAIMERS)


# ----------------------------------------------------------------------------
# Реальные пилотные отчёты
# ----------------------------------------------------------------------------

BIOTINAL_REPORT = _SKILL_ROOT / "runs" / "biotinal_claims_20260717-1109" / "report.md"
CJM_HAIRLOSS_REPORT = _SKILL_ROOT / "runs" / "cjm_hairloss_demo_20260710-0017" / "cjm_report.md"


class TestRealPilotReports(unittest.TestCase):
    """
    ВАЖНО: этот проект собирается несколькими параллельными сборщиками ([B1]
    generate.py/report.py, [B3] шаблоны/cjm_lint.py). Итог cjm_lint для
    ФАКТИЧЕСКОГО report.md/cjm_report.md на диске может измениться между
    моментом написания этого файла и моментом запуска теста (например, если
    отчёт перескорят по итогам v1.3 или поправят cjm_lint.py). Поэтому эти
    тесты проверяют ИНВАРИАНТ ("рендер отказывает ТОГДА И ТОЛЬКО ТОГДА, когда
    cjm_lint находит нарушения"), а не жёстко зашитый сегодняшний результат —
    иначе тест сломался бы от чужой, законной правки в другой зоне сборки.

    На момент написания этого файла: cjm_report.md ПРОХОДИТ cjm_lint (exit 0,
    как и зафиксировано в его собственном manifest.json), report.md — НЕ
    проходит (11 нарушений: правило 3 требует карту доверия 🟢🟡🔴, которой в
    report.md никогда не было по спецификации v1/v1.2, плюс правила 1/4 ложно
    матчят "95% CI"/"Brand Lift"/"прогноз продаж" внутри дословно скопированного
    disclaimers.md — блок теряет маркеры DISCLAIMER_BLOCK_START/END при вставке
    в report.py::render_report, поэтому маскировка cjm_lint по ним не
    срабатывает на готовом report.md, хотя срабатывает на cjm_report.md, где
    агент копирует маркеры дословно). Это НЕ дефект render_client.py — см.
    итоговый summary сборки (issues) за отдельным разбором.
    """

    def test_render_matches_cjm_lint_ground_truth_for_both_pilot_reports(self):
        for report_path in (BIOTINAL_REPORT, CJM_HAIRLOSS_REPORT):
            with self.subTest(report=report_path.name):
                if not report_path.exists():
                    self.skipTest(f"{report_path} отсутствует в этом дереве (не моя зона — пропускаю)")
                lint_ok = not cjm_lint.lint_file(report_path)
                if lint_ok:
                    html_text = rc.build_html_or_refuse(report_path)
                    assert_valid_html_structure(self, html_text)
                    self.assertIn("disclaimer-block", html_text)
                else:
                    with self.assertRaises(rc.RenderRefused) as ctx:
                        rc.build_html_or_refuse(report_path)
                    self.assertEqual(ctx.exception.exit_code, rc.EXIT_LINT_FAILED)

    def test_build_html_core_logic_works_on_both_real_reports_bypassing_lint_gate(self):
        """
        Проверяет ДВИЖОК рендера (парсинг/карточки/бары/дисклеймеры), а не политику
        отказа — вызывает build_html() напрямую (минуя run_lint_gate), чтобы
        подтвердить, что сама логика извлечения секций/таблиц уже сейчас корректно
        работает на ОБЕИХ реальных формах отчёта (report.md и cjm_report.md), даже
        если CLI сегодня отказывает одному из них по правилу (а) — см. докстринг
        класса выше и issues в итоговом summary сборки.
        """
        for report_path in (BIOTINAL_REPORT, CJM_HAIRLOSS_REPORT):
            with self.subTest(report=report_path.name):
                if not report_path.exists():
                    self.skipTest(f"{report_path} отсутствует в этом дереве (не моя зона — пропускаю)")
                manifest = rc.load_manifest(report_path)
                html_text = rc.build_html(report_path, manifest)
                assert_valid_html_structure(self, html_text)
                self.assertIn("segment-card", html_text, "должна найтись хотя бы одна карточка сегмента/рейтинга")
                self.assertIn("pmf-svg", html_text, "должен найтись хотя бы один SVG-бар распределения")
                self.assertIn(
                    "R=0,72 (~90% теоретического потолка)",
                    html_text,
                    "каноническая формулировка точности метода обязана перенестись дословно",
                )


# ----------------------------------------------------------------------------
# CLI: аргументы/файл не найден
# ----------------------------------------------------------------------------


class TestCliArgs(unittest.TestCase):
    def test_missing_report_file_returns_args_exit_code(self):
        result = subprocess.run(
            [sys.executable, str(RENDER_CLIENT_PATH), "--report", "/nonexistent/report.md"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, rc.EXIT_ARGS)
        self.assertIn("не найден", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""
test_cjm_lint.py — юнит-тесты линтера честности (scripts/cjm_lint.py), spec_synthetic-panel_v1.1_segment_map.md §4.

Запуск:
    python scripts/test_cjm_lint.py
    (или: python -m unittest scripts.test_cjm_lint -v из корня скилла)

Покрытие (см. spec §4 и §5.1 "Юнит: cjm_lint.py ловит подсаженные нарушения..."):
    - чистый образец (все 4 правила выполнены) -> lint_text даёт [];
    - по фикстуре на каждый ТИП нарушения (правила 1-4, включая обе под-проверки
      правила 3: отсутствие легенды и отсутствие маркера в разделе) -> нарушение
      подсаженного типа ловится, позитивные соседние строки не ловятся ложно;
    - многофайловая оркестрация (lint_files): легенда в одном файле "закрывает"
      требование для всего набора, посекционные маркеры проверяются на КАЖДЫЙ
      файл отдельно;
    - CLI end-to-end (subprocess): exit 0 на чистом образце, exit 1 на грязном.
    - самотест на references/cjm_report_template.md, если он уже существует
      (self-skip, если сборщик [B1] ещё не создал файл — не блокирует прогон).

Добавлено v1.2 (spec_synthetic-panel_v1.2.md, задание [B2] п.1, п.5):
    - warn-слой ИИ-измов (TestStyleWarningsAiIsms): каждый паттерн-минимум по
      отдельной фикстуре (-ориентированн/-фокусированн/осознанн.. потребител/
      длинное дефисное слово >18 симв.), дедупликация по спану, маскировка
      несъёмного блока дисклеймеров, отсутствие влияния на lint_text()/exit-код
      (CLI-тесты в TestCliExitCodes: warn-only отчёт всё равно exit 0/1 строго
      по нарушениям правил 1-4, но блок "Стилистические предупреждения" печатается).
    - конкурентная красная зона в правиле 4 (TestRule4CompetitiveRedZone): «доля
      рынка»/«switch rate»/«отнимем ... %»/«переключим ... %» без тега источника,
      включая контрольный позитивный тест на РЕКОМЕНДОВАННУЮ спецификацией
      формулировку («какими сообщениями и от кого отстраиваться», не «сколько
      отнимем») — она обязана проходить линтер начисто.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import cjm_lint  # noqa: E402

CJM_LINT_PATH = _SCRIPTS_DIR / "cjm_lint.py"


# ----------------------------------------------------------------------------
# Фикстуры
# ----------------------------------------------------------------------------

CLEAN_REPORT = """\
# Отчёт AI CJM: демо (cjm_hairloss_demo)

## Легенда карты доверия 🟢🟡🔴

- 🟢 модельное качественное — мотивации, барьеры, язык, JTBD.
- 🟡 гипотеза для проверки — RTB-кандидаты, неосознанные потребности, карта тачпоинтов.
- 🔴 требует данных — доли и проценты без независимого измерения.

## Сегмент 1: Молодые мамы после родов 🟢 (3/3)

Главная мотивация — вернуть волосам густоту до рождения ребёнка.

Доля упоминаний темы в обсуждениях: 34% [BA].

Оценка: значительная часть сегмента впервые сталкивается с выпадением через
2-3 месяца после родов (оценка, отдельного измерения по срокам нет).

Синтетическая иллюстрация (сгенерировано моделью, не отзыв из источника):
«кажется, что волосы высыпаются клочьями».

Реальный отзыв [отзывы: data/social_listening_2026.csv]: «уже полгода мажу всё
подряд, толку ноль».

## RTB-кандидаты для сегмента 1 🟡

Гипотеза для проверки: клиническая доказанность молекулы поддержана данными
исследования (18% участников [клиент]).

## Раздел данных 🔴

Доля пациентов, дошедших до постановки диагноза: нет данных — оценка.

## Точность метода 🟢

Метод SSR: R=0,72 (~90% теоретического потолка).
"""


def _write(tmp_dir: Path, name: str, content: str) -> Path:
    path = tmp_dir / name
    path.write_text(content, encoding="utf-8")
    return path


# ----------------------------------------------------------------------------
# Чистый образец
# ----------------------------------------------------------------------------


class TestCleanSample(unittest.TestCase):
    def test_clean_report_has_no_violations(self):
        violations = cjm_lint.lint_text(CLEAN_REPORT)
        self.assertEqual(violations, [], f"Чистый образец не должен давать нарушений, получено: {violations}")


# ----------------------------------------------------------------------------
# Правило 1: проценты без источника
# ----------------------------------------------------------------------------


class TestRule1PercentSourcing(unittest.TestCase):
    def test_percent_without_source_is_flagged(self):
        text = "# Отчёт\n## Раздел 🟢\nДоля сегмента: 42% в обсуждениях категории.\n"
        violations = cjm_lint.lint_text(text)
        rule1 = [v for v in violations if v.rule == 1]
        self.assertEqual(len(rule1), 1)
        self.assertEqual(rule1[0].line, 3)

    def test_percent_with_source_tag_passes(self):
        text = "# Отчёт\n## Раздел 🟢\nДоля сегмента: 42% [BA].\n"
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 1], [])

    def test_percent_with_estimate_word_passes(self):
        text = "# Отчёт\n## Раздел 🟢\nОценка: примерно 42% сегмента сталкивается с этим впервые.\n"
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 1], [])

    def test_percent_with_hypothesis_word_passes(self):
        text = "# Отчёт\n## Раздел 🟡\nГипотеза: около 20% готовы попробовать новинку.\n"
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 1], [])

    def test_all_six_source_tags_are_accepted(self):
        for tag in ("[BA]", "[Mediascope]", "[DSM]", "[Росстат]", "[опрос]", "[клиент]"):
            text = f"# Отчёт\n## Раздел 🟢\nПоказатель: 10% {tag}.\n"
            violations = cjm_lint.lint_text(text)
            self.assertEqual([v for v in violations if v.rule == 1], [], f"тег {tag} должен приниматься")

    def test_percent_without_space_is_also_matched(self):
        text = "# Отчёт\n## Раздел 🟢\nПоказатель: 42% без пробела перед процентом.\n"
        violations = cjm_lint.lint_text(text)
        self.assertEqual(len([v for v in violations if v.rule == 1]), 1)


# ----------------------------------------------------------------------------
# Находка №4, MAJOR (review_v1.1.md §3.4) — "процент" словом/цифрой без "%"
# ----------------------------------------------------------------------------


class TestRule1PercentWordForm(unittest.TestCase):
    """PERCENT_RE ловит только цифровую форму со знаком "%"; количественное
    утверждение без единого символа "%" (числительное словом, голая цифра рядом
    со словом "процент", разговорная инверсия) должно ловиться так же — см.
    докстринг cjm_lint.py, PERCENT_WORD_RE."""

    def test_spelled_out_number_word_without_percent_sign_is_flagged(self):
        text = (
            "# Отчёт\n## Раздел 🟢\n"
            "Сорок процентов сегмента предпочитают этот вариант, без каких-либо оговорок.\n"
        )
        violations = cjm_lint.lint_text(text)
        rule1 = [v for v in violations if v.rule == 1]
        self.assertEqual(len(rule1), 1, f"ожидалось 1 нарушение, получено: {rule1}")

    def test_digit_without_percent_sign_is_flagged(self):
        text = "# Отчёт\n## Раздел 🟢\nПоказатель: 40 процентов сегмента, без источника и без пометки.\n"
        violations = cjm_lint.lint_text(text)
        self.assertEqual(len([v for v in violations if v.rule == 1]), 1)

    def test_reversed_word_order_colloquial_inversion_is_flagged(self):
        text = "# Отчёт\n## Раздел 🟢\nПроцентов пять сегмента реагируют так же, без указания источника.\n"
        violations = cjm_lint.lint_text(text)
        self.assertEqual(len([v for v in violations if v.rule == 1]), 1)

    def test_percent_word_form_with_source_tag_passes(self):
        text = "# Отчёт\n## Раздел 🟢\nСорок процентов сегмента [BA] предпочитают этот вариант.\n"
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 1], [])

    def test_percent_word_form_with_estimate_word_passes(self):
        text = "# Отчёт\n## Раздел 🟡\nОценка: сорок процентов сегмента предпочитают этот вариант.\n"
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 1], [])

    def test_bare_percent_word_without_nearby_numeral_does_not_trigger(self):
        """Ложное срабатывание, которого явно нужно избежать (спецификация задачи):
        методологическая проза про "правило процентов" без числительного рядом —
        не количественное утверждение, не нарушение."""
        text = (
            "# Отчёт\n## Раздел 🟢\n"
            "Напомним правило процентов: любая цифра с «%» требует тега источника "
            "или пометки «оценка»/«гипотеза».\n"
        )
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 1], [])

    def test_ni_odnogo_procenta_idiom_does_not_trigger(self):
        """"Ни одного процента" — утверждение ОТСУТСТВИЯ процента (дословно из
        чек-листа runs/cjm_hairloss_demo_20260710-0017/01_segmentation_run{1,2,3}.md),
        не количественная доля — "одного" технически числительное, но идиома
        самопроверки не должна ловиться как нарушение."""
        text = (
            "# Отчёт\n## Раздел 🟢\n"
            "Ни одного процента, ни одного коэффициента значимости в тексте выше.\n"
        )
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 1], [])


# ----------------------------------------------------------------------------
# Находка №1, CRITICAL (review_v1.1.md §3.2) — табличное "отмывание" непомеченных
# значений: split_into_blocks склеивал ВСЕ строки markdown-таблицы в один блок,
# так что тег в одном ряду прикрывал непомеченные проценты/цитаты в соседних.
# ----------------------------------------------------------------------------


class TestTableRowLaundering(unittest.TestCase):
    def test_rule1_table_row_without_tag_is_flagged_even_if_sibling_row_has_tag(self):
        text = (
            "# Отчёт\n## Раздел 🟢\n"
            "| Сегмент | Доля | Источник |\n"
            "|---|---|---|\n"
            "| Альфа | 42% | [BA] |\n"
            "| Бета | 55% | без источника здесь вообще |\n"
        )
        violations = cjm_lint.lint_text(text)
        rule1 = [v for v in violations if v.rule == 1]
        self.assertEqual(len(rule1), 1, f"ожидалось 1 нарушение (ряд «Бета»), получено: {rule1}")
        self.assertIn("Бета", rule1[0].excerpt)
        self.assertNotIn("Альфа", rule1[0].excerpt)

    def test_rule2_table_row_without_tag_is_flagged_even_if_sibling_row_has_tag(self):
        text = (
            '# Отчёт\n## Раздел 🟢\n'
            '| Сегмент | Цитата |\n'
            '|---|---|\n'
            '| Альфа | Реальный отзыв [BA]: "работает" |\n'
            '| Бета | Реальный отзыв: "не работает вообще, зря деньги" |\n'
        )
        violations = cjm_lint.lint_text(text)
        rule2 = [v for v in violations if v.rule == 2]
        self.assertEqual(len(rule2), 1, f"ожидалось 1 нарушение (ряд «Бета»), получено: {rule2}")
        self.assertIn("Бета", rule2[0].excerpt)

    def test_rule4_table_row_with_bad_wording_is_flagged_even_if_sibling_row_is_canonical(self):
        text = (
            "# Отчёт\n## Раздел 🟢\n"
            "| Сегмент | Точность |\n"
            "|---|---|\n"
            "| Альфа | Точность метода 90% для этого сегмента отдельно |\n"
            "| Бета | Метод SSR: R=0,72 (~90% теоретического потолка) |\n"
        )
        violations = cjm_lint.lint_text(text)
        rule4 = [v for v in violations if v.rule == 4]
        self.assertEqual(len(rule4), 1, f"ожидалось 1 нарушение (ряд «Альфа»), получено: {rule4}")
        self.assertIn("Альфа", rule4[0].excerpt)

    def test_clean_table_with_correct_tag_in_every_row_passes(self):
        """Регресс: если у КАЖДОГО ряда свой корректный тег — построчная проверка
        не должна давать ложных срабатываний (не путать с прежним поведением по
        блоку целиком)."""
        text = (
            "# Отчёт\n## Раздел 🟢\n"
            "| Сегмент | Доля | Источник |\n"
            "|---|---|---|\n"
            "| Альфа | 42% | [BA] |\n"
            "| Бета | 55% | [Mediascope] |\n"
        )
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 1], [])

    def test_blank_line_separated_rows_still_flagged_as_before(self):
        """Контрольный тест ревью: та же пара строк, что и в первом фикстуре
        выше, но разделённая пустой строкой (значит — заведомо два блока) —
        должна была и раньше ловиться корректно; фиксирует, что фикс не завязан
        на этот конкретный случай, а именно на отсутствие пустой строки между
        рядами таблицы."""
        text = (
            "# Отчёт\n## Раздел 🟢\n"
            "| Сегмент | Доля | Источник |\n"
            "|---|---|---|\n"
            "| Альфа | 42% | [BA] |\n"
            "\n"
            "| Бета | 55% | без источника здесь вообще |\n"
        )
        violations = cjm_lint.lint_text(text)
        rule1 = [v for v in violations if v.rule == 1]
        self.assertEqual(len(rule1), 1)


class TestProseWordWrapStillWorksAfterTableFix(unittest.TestCase):
    """Регресс осознанного фикса B2 (docstring cjm_lint.py) — word-wrap прозы БЕЗ
    пустой строки между физическими строками по-прежнему должен считаться ОДНИМ
    блоком; TABLE_ROW_RE — отдельная новая ветка только для строк, начинающихся
    с "|", не должна была затронуть эту логику."""

    def test_percent_and_estimate_word_wrapped_onto_next_line_still_pass(self):
        text = (
            "# Отчёт\n## Раздел 🟢\n"
            "Доля сегмента составляет примерно 42% — это предварительная\n"
            "оценка, не измерение, окончательных данных пока нет.\n"
        )
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 1], [])


# ----------------------------------------------------------------------------
# Правило 2: "реальный отзыв"/"реальная цитата" без источника
# ----------------------------------------------------------------------------


class TestRule2RealQuoteSourcing(unittest.TestCase):
    def test_real_quote_without_source_is_flagged(self):
        text = '# Отчёт\n## Раздел 🟢\nРеальный отзыв: "это работает у меня отлично".\n'
        violations = cjm_lint.lint_text(text)
        rule2 = [v for v in violations if v.rule == 2]
        self.assertEqual(len(rule2), 1)
        self.assertEqual(rule2[0].line, 3)

    def test_real_review_plural_without_source_is_flagged(self):
        text = "# Отчёт\n## Раздел 🟢\nВ основе анализа — реальные отзывы пользователей.\n"
        violations = cjm_lint.lint_text(text)
        self.assertEqual(len([v for v in violations if v.rule == 2]), 1)

    def test_real_quote_with_ba_tag_passes(self):
        text = '# Отчёт\n## Раздел 🟢\nРеальный отзыв [BA]: "это работает у меня отлично".\n'
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 2], [])

    def test_real_quote_with_otzyvy_tag_passes(self):
        text = (
            '# Отчёт\n## Раздел 🟢\nРеальный отзыв [отзывы: data/social_listening.csv]: '
            '"это работает у меня отлично".\n'
        )
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 2], [])

    def test_synthetic_illustration_wording_does_not_trigger(self):
        text = "# Отчёт\n## Раздел 🟢\nСинтетическая иллюстрация: «кажется, что волосы как пакля».\n"
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 2], [])


# ----------------------------------------------------------------------------
# Правило 3: легенда + маркеры разделов
# ----------------------------------------------------------------------------


class TestRule3TrustMap(unittest.TestCase):
    def test_missing_legend_word_is_flagged(self):
        text = "# Отчёт\n\n## Раздел данных 🔴\n\nЗдесь про данные, без явной легенды в документе.\n"
        violations = cjm_lint.lint_text(text)
        rule3 = [v for v in violations if v.rule == 3]
        self.assertTrue(any("Легенда" in v.message or "легенда" in v.message.lower() for v in rule3))

    def test_missing_markers_in_document_is_flagged_even_with_legend_word(self):
        text = "# Отчёт\n\n## Легенда карты доверия\n\nЕсть только 🔴 маркер здесь, остальных нет.\n"
        violations = cjm_lint.lint_text(text)
        rule3 = [v for v in violations if v.rule == 3]
        self.assertTrue(any("не хватает маркеров" in v.message for v in rule3))

    def test_section_without_any_marker_is_flagged(self):
        text = (
            "# Отчёт\n\n"
            "## Легенда карты доверия\n\n"
            "- 🟢 модельное качественное\n- 🟡 гипотеза для проверки\n- 🔴 требует данных\n\n"
            "## Раздел без маркера\n\n"
            "Здесь просто текст без эмодзи вообще.\n"
        )
        violations = cjm_lint.lint_text(text)
        rule3 = [v for v in violations if v.rule == 3]
        self.assertEqual(len(rule3), 1, f"ожидалось ровно одно нарушение (раздел без маркера), получено: {rule3}")
        self.assertIn("Раздел без маркера", rule3[0].message)

    def test_marker_in_section_body_not_heading_still_passes(self):
        """Маркер может быть в теле раздела, не обязательно в самом заголовке."""
        text = (
            "# Отчёт\n\n"
            "## Легенда карты доверия\n\n"
            "- 🟢 модельное качественное\n- 🟡 гипотеза для проверки\n- 🔴 требует данных\n\n"
            "## Сегмент 1\n\n"
            "Статус: 🟢 модельное качественное.\n"
        )
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 3], [])

    def test_nested_h3_content_counts_toward_parent_h2_section(self):
        text = (
            "# Отчёт\n\n"
            "## Легенда карты доверия\n\n"
            "- 🟢 модельное качественное\n- 🟡 гипотеза для проверки\n- 🔴 требует данных\n\n"
            "## Сегмент 1\n\n"
            "### Мотивация\n\n"
            "Текст без маркера тут.\n\n"
            "### Барьер 🟡\n\n"
            "Текст про барьер.\n"
        )
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 3], [])

    def test_h1_preamble_before_first_h2_is_not_checked(self):
        text = (
            "# Отчёт без раздела вообще (только титул)\n\n"
            "Просто текст сразу под титулом, без эмодзи.\n"
        )
        violations = cjm_lint.lint_text(text)
        # Заголовков уровня 2 нет вовсе -> check_section_markers не находит ни одного
        # раздела для проверки; но легенда всё равно отсутствует (правило 3а).
        self.assertEqual([v for v in violations if "не содержит маркера" in v.message], [])


# ----------------------------------------------------------------------------
# Правило 4: запрещённые обещания / формулировка точности
# ----------------------------------------------------------------------------


class TestRule4ForbiddenPromises(unittest.TestCase):
    def test_percent_will_buy_phrase_is_flagged(self):
        text = "# Отчёт\n## Раздел 🟢\n70% купят этот продукт после просмотра ролика.\n"
        violations = cjm_lint.lint_text(text)
        rule4 = [v for v in violations if v.rule == 4]
        self.assertTrue(any("% купят" in v.message for v in rule4))

    def test_sales_forecast_phrase_is_flagged(self):
        text = "# Отчёт\n## Раздел 🟢\nПрогноз продаж на следующий квартал — рост в 2 раза.\n"
        violations = cjm_lint.lint_text(text)
        rule4 = [v for v in violations if v.rule == 4]
        self.assertTrue(any("прогноз продаж" in v.message for v in rule4))

    def test_brand_lift_phrase_is_flagged_case_insensitively(self):
        text = "# Отчёт\n## Раздел 🟢\nОжидаем сильный Brand Lift после кампании.\n"
        violations = cjm_lint.lint_text(text)
        rule4 = [v for v in violations if v.rule == 4]
        self.assertTrue(any("brand lift" in v.message.lower() for v in rule4))

    def test_bad_accuracy_wording_is_flagged(self):
        text = "# Отчёт\n## Раздел 🟢\nТочность метода составляет 90% для всех сегментов.\n"
        violations = cjm_lint.lint_text(text)
        rule4 = [v for v in violations if v.rule == 4]
        self.assertTrue(any("R=0,72" in v.message for v in rule4))

    def test_canonical_accuracy_wording_passes(self):
        text = "# Отчёт\n## Раздел 🟢\nМетод SSR: R=0,72 (~90% теоретического потолка).\n"
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 4], [])

    def test_accuracy_word_without_percent_does_not_trigger(self):
        text = "# Отчёт\n## Раздел 🟢\nТочность метода на этом сегменте не определена.\n"
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 4], [])


# ----------------------------------------------------------------------------
# Правило 4 (расширение v1.2, spec_synthetic-panel_v1.2.md §Модуль 3 п.4):
# конкурентная красная зона — «доля рынка»/«switch rate»/«отнимем ... %»/
# «переключим ... %» без тега источника.
# ----------------------------------------------------------------------------


class TestRule4CompetitiveRedZone(unittest.TestCase):
    def test_market_share_phrase_without_tag_is_flagged(self):
        text = "# Отчёт\n## Раздел 🟢\nНаша доля рынка вырастет за счёт конкурента в этой категории.\n"
        violations = cjm_lint.lint_text(text)
        rule4 = [v for v in violations if v.rule == 4]
        self.assertTrue(any("доля рынка" in v.message for v in rule4), rule4)

    def test_market_share_declension_doli_rynka_is_also_flagged(self):
        text = "# Отчёт\n## Раздел 🟢\nОжидаем рост доли рынка нашего бренда в этом сегменте.\n"
        violations = cjm_lint.lint_text(text)
        self.assertTrue(any(v.rule == 4 for v in violations))

    def test_market_share_phrase_with_source_tag_passes(self):
        text = "# Отчёт\n## Раздел 🟢\nТекущая доля рынка бренда — 12% [BA], по данным клиента.\n"
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 4], [])

    def test_switch_rate_without_tag_is_flagged_even_with_hypothesis_word(self):
        """Ключевая семантика: в отличие от правила 1, слово «гипотеза» НЕ
        освобождает от тега источника для конкурентных стоп-паттернов (см.
        докстринг cjm_lint.py — пометка "гипотеза" не чинит структурную
        неспособность метода измерить switch rate)."""
        text = "# Отчёт\n## Раздел 🟡\nГипотеза: switch rate для этого сегмента около 20%.\n"
        violations = cjm_lint.lint_text(text)
        rule4 = [v for v in violations if v.rule == 4]
        self.assertTrue(any("switch rate" in v.message.lower() for v in rule4), rule4)

    def test_switch_rate_case_insensitive_with_tag_passes(self):
        text = "# Отчёт\n## Раздел 🟢\nSwitch Rate категории по историческим данным — 20% [Mediascope].\n"
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 4], [])

    def test_otnimem_with_percent_without_tag_is_flagged(self):
        text = "# Отчёт\n## Раздел 🟡\nМы отнимем 15% пользователей у конкурента этим сообщением.\n"
        violations = cjm_lint.lint_text(text)
        rule4 = [v for v in violations if v.rule == 4]
        self.assertTrue(any("отнимем" in v.message for v in rule4), rule4)

    def test_otnimem_without_percent_is_not_flagged(self):
        """«отнимем ... %» из спецификации — процент ОБЯЗАТЕЛЕН для срабатывания
        этого под-паттерна (см. докстринг: «...» между глаголом и «%»)."""
        text = "# Отчёт\n## Раздел 🟢\nОтнимем внимание аудитории у конкурента ярким сообщением.\n"
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 4], [])

    def test_pereklyuchim_with_percent_without_tag_is_flagged(self):
        text = "# Отчёт\n## Раздел 🟡\nЭто сообщение переключит 20% пользователей конкурента, гипотеза.\n"
        violations = cjm_lint.lint_text(text)
        rule4 = [v for v in violations if v.rule == 4]
        self.assertTrue(any("переключим" in v.message for v in rule4), rule4)

    def test_pereklyuchim_without_percent_is_not_flagged(self):
        text = "# Отчёт\n## Раздел 🟢\nПопробуем переключить их на наш бренд этим сообщением.\n"
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 4], [])

    def test_pereklyuchim_with_word_form_percent_without_sign_is_flagged(self):
        """Процент словом, без символа «%» (находка №4 review_v1.1.md,
        PERCENT_WORD_RE) тоже должен удовлетворять требованию "есть процент
        в блоке" для «переключим ... %»."""
        text = "# Отчёт\n## Раздел 🟡\nПереключим двадцать процентов аудитории конкурента, гипотеза.\n"
        violations = cjm_lint.lint_text(text)
        self.assertTrue(any(v.rule == 4 for v in violations))

    def test_otnimem_with_tag_passes(self):
        text = "# Отчёт\n## Раздел 🟢\nПо факту прошлых кампаний отняли 15% [BA] у основного конкурента.\n"
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 4], [])

    def test_spec_recommended_formula_sentence_passes_clean(self):
        """Контрольный позитивный тест: формулировка, прямо рекомендованная
        спецификацией («какими сообщениями и от кого отстраиваться», не
        «сколько отнимем») обязана проходить линтер начисто — ни один стоп-
        паттерн не должен сработать на собственных словах спецификации."""
        text = (
            "# Отчёт\n## Раздел 🟢\n"
            "Результат отвечает на вопрос, какими сообщениями и от кого отстраиваться, "
            "а не сколько отнимем — это качественная карта территорий, а не прогноз доли.\n"
        )
        violations = cjm_lint.lint_text(text)
        self.assertEqual([v for v in violations if v.rule == 4], [])

    def test_competitive_red_zone_respects_table_row_atomicity(self):
        """Регресс той же природы, что находка №1 review_v1.1.md — тег в одном
        ряду таблицы не должен «отмывать» непомеченный стоп-паттерн в соседнем ряду."""
        text = (
            "# Отчёт\n## Раздел 🟢\n"
            "| Конкурент | Комментарий |\n"
            "|---|---|\n"
            "| Альфа | доля рынка стабильна [BA] |\n"
            "| Бета | отнимем у них 10% без указания источника |\n"
        )
        violations = cjm_lint.lint_text(text)
        rule4 = [v for v in violations if v.rule == 4]
        self.assertEqual(len(rule4), 1, f"ожидалось 1 нарушение (ряд «Бета»), получено: {rule4}")
        self.assertIn("Бета", rule4[0].excerpt)

    def test_competitive_disclaimer_block_is_masked_from_rule4(self):
        """references/competitive_report_template.md фиксирует статичный раздел
        «Границы этого отчёта (конкурентная отстройка)», который ДОСЛОВНО
        упоминает «доля рынка»/«switch rate», ОБЪЯСНЯЯ, что отчёт их не
        оценивает (см. spec_synthetic-panel_v1.2.md §Модуль 3 п.4,
        docstring этого модуля, DISCLAIMER_BLOCK_MARKERS). Без маскировки
        такой дословно скопированный дисклеймер ложно ловится правилом 4 —
        та же природа находки, что уже чинилась для DISCLAIMER_BLOCK_CJM."""
        text = (
            "# Отчёт\n## Раздел 🟢\nВсё в порядке здесь.\n\n"
            "<!-- DISCLAIMER_BLOCK_COMPETITIVE_START -->\n"
            "## Границы этого отчёта (конкурентная отстройка)\n\n"
            "Реальная доля рынка и switch rate этим отчётом не оцениваются и не "
            "прогнозируются — ни в процентах, ни любым другим количественным способом.\n"
            "<!-- DISCLAIMER_BLOCK_COMPETITIVE_END -->\n"
        )
        violations = cjm_lint.lint_text(text)
        rule4 = [v for v in violations if v.rule == 4]
        self.assertEqual(rule4, [], f"дисклеймер должен быть замаскирован, получено: {rule4}")


# ----------------------------------------------------------------------------
# Многофайловая оркестрация (lint_files)
# ----------------------------------------------------------------------------


class TestLintFilesMultiFile(unittest.TestCase):
    def test_legend_in_report_satisfies_extra_files_without_own_legend(self):
        report = (
            "# Отчёт\n\n## Легенда карты доверия\n\n"
            "- 🟢 модельное качественное\n- 🟡 гипотеза для проверки\n- 🔴 требует данных\n\n"
            "## Сегмент 1 🟢\n\nТекст.\n"
        )
        extra = "## Черновик сегмента 2 🟡\n\nТекст без собственной легенды.\n"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            report_path = _write(tmp, "cjm_report.md", report)
            extra_path = _write(tmp, "02_cjm_seg2.md", extra)
            results = cjm_lint.lint_files([report_path, extra_path])
            legend_violations = [v for _, v in results if "Легенда" in v.message or "маркеров карты" in v.message]
            self.assertEqual(legend_violations, [])

    def test_section_marker_check_is_per_file(self):
        report = (
            "# Отчёт\n\n## Легенда карты доверия\n\n"
            "- 🟢 модельное качественное\n- 🟡 гипотеза для проверки\n- 🔴 требует данных\n\n"
            "## Сегмент 1 🟢\n\nТекст.\n"
        )
        extra_bad = "## Раздел без маркера\n\nТекст совсем без эмодзи.\n"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            report_path = _write(tmp, "cjm_report.md", report)
            extra_path = _write(tmp, "02_cjm_bad.md", extra_bad)
            results = cjm_lint.lint_files([report_path, extra_path])
            section_violations = [
                (fname, v) for fname, v in results if v.rule == 3 and "не содержит маркера" in v.message
            ]
            self.assertEqual(len(section_violations), 1)
            self.assertEqual(section_violations[0][0], "02_cjm_bad.md")


# ----------------------------------------------------------------------------
# CLI end-to-end (subprocess) — exit-коды
# ----------------------------------------------------------------------------


class TestCliExitCodes(unittest.TestCase):
    def test_clean_report_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            report_path = _write(Path(td), "cjm_report.md", CLEAN_REPORT)
            proc = subprocess.run(
                [sys.executable, str(CJM_LINT_PATH), "--report", str(report_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("OK", proc.stdout)

    def test_dirty_report_exits_one_and_lists_violations(self):
        dirty = "# Отчёт\n## Раздел 🟢\nДоля сегмента: 42% без источника.\n"
        with tempfile.TemporaryDirectory() as td:
            report_path = _write(Path(td), "cjm_report.md", dirty)
            proc = subprocess.run(
                [sys.executable, str(CJM_LINT_PATH), "--report", str(report_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("Правило 1", proc.stdout)
            self.assertIn("ИТОГО", proc.stdout)

    def test_missing_file_exits_one(self):
        proc = subprocess.run(
            [sys.executable, str(CJM_LINT_PATH), "--report", "/nonexistent/cjm_report.md"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)

    def test_no_arguments_exits_one(self):
        proc = subprocess.run(
            [sys.executable, str(CJM_LINT_PATH)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)

    def test_extra_only_without_report_works(self):
        with tempfile.TemporaryDirectory() as td:
            extra_path = _write(Path(td), "02_cjm_seg1.md", CLEAN_REPORT)
            proc = subprocess.run(
                [sys.executable, str(CJM_LINT_PATH), "--extra", str(extra_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")

    def test_style_warnings_do_not_affect_exit_code_on_otherwise_clean_report(self):
        """v1.2 §Модуль 1 п.3: warn-слой НЕ влияет на exit-код — отчёт, чистый по
        правилам 1-4, но с ИИ-измом (сегмент «Ингредиент-ориентированные
        рутинщики», буквально мотивирующий пример из spec §Модуль 1 п.1),
        обязан по-прежнему выйти с exit 0, но напечатать блок предупреждений."""
        report_with_ai_ism = CLEAN_REPORT.replace(
            "## Сегмент 1: Молодые мамы после родов 🟢 (3/3)",
            "## Сегмент 1: Ингредиент-ориентированные рутинщики 🟢 (3/3)",
        )
        with tempfile.TemporaryDirectory() as td:
            report_path = _write(Path(td), "cjm_report.md", report_with_ai_ism)
            proc = subprocess.run(
                [sys.executable, str(CJM_LINT_PATH), "--report", str(report_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("OK", proc.stdout)
            self.assertIn("Стилистические предупреждения (не блокируют):", proc.stdout)
            self.assertIn("ориентированн", proc.stdout)

    def test_style_warnings_are_also_printed_alongside_real_violations(self):
        dirty_with_ai_ism = (
            "# Отчёт\n## Раздел 🟢\n"
            "Доля сегмента: 42% без источника, для осознанных потребителей рынка.\n"
        )
        with tempfile.TemporaryDirectory() as td:
            report_path = _write(Path(td), "cjm_report.md", dirty_with_ai_ism)
            proc = subprocess.run(
                [sys.executable, str(CJM_LINT_PATH), "--report", str(report_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("ИТОГО", proc.stdout)
            self.assertIn("Стилистические предупреждения (не блокируют):", proc.stdout)


# ----------------------------------------------------------------------------
# v1.2, warn-слой ИИ-измов (StyleWarning) — spec_synthetic-panel_v1.2.md §Модуль 1 п.3
# ----------------------------------------------------------------------------


class TestStyleWarningsAiIsms(unittest.TestCase):
    def test_clean_report_has_no_style_warnings(self):
        self.assertEqual(cjm_lint.collect_style_warnings(CLEAN_REPORT), [])

    def test_orientirovannost_suffix_is_warned(self):
        text = "## Раздел 🟢\nИнгредиент-ориентированные рутинщики читают составы.\n"
        warnings = cjm_lint.collect_style_warnings(text)
        self.assertTrue(any("ориентированн" in w.message for w in warnings), warnings)

    def test_fokusirovannost_suffix_is_warned(self):
        text = "## Раздел 🟢\nКлиент-фокусированные покупатели ищут выгоду.\n"
        warnings = cjm_lint.collect_style_warnings(text)
        self.assertTrue(any("фокусированн" in w.message for w in warnings), warnings)

    def test_osoznannye_potrebiteli_stamp_is_warned(self):
        text = "## Раздел 🟢\nЭто сегмент осознанных потребителей категории.\n"
        warnings = cjm_lint.collect_style_warnings(text)
        self.assertTrue(any("осознанн" in w.message for w in warnings), warnings)

    def test_osoznannye_potrebiteli_plural_nominative_is_warned(self):
        text = "## Раздел 🟢\nОсознанные потребители читают состав перед покупкой.\n"
        warnings = cjm_lint.collect_style_warnings(text)
        self.assertTrue(any("осознанн" in w.message for w in warnings), warnings)

    def test_long_hyphenated_word_without_named_suffix_is_warned(self):
        text = "## Раздел 🟢\nИнформационно-перегруженные покупатели теряются в ассортименте.\n"
        warnings = cjm_lint.collect_style_warnings(text)
        self.assertTrue(any("дефисное составное" in w.message for w in warnings), warnings)
        self.assertTrue(any("Информационно-перегруженные" in w.excerpt for w in warnings), warnings)

    def test_short_hyphenated_word_is_not_warned(self):
        text = "## Раздел 🟢\nЭто бизнес-класс обслуживания, какой-то стандартный уровень.\n"
        warnings = cjm_lint.collect_style_warnings(text)
        self.assertEqual(warnings, [])

    def test_no_duplicate_warning_for_word_matching_both_named_pattern_and_length(self):
        """«Ингредиент-ориентированные» (26 символов) одновременно матчит именной
        паттерн -ориентированн И порог длины >18 — должно быть ОДНО предупреждение
        на этот спан, не два (см. докстринг check_ai_isms — дедупликация по span)."""
        text = "## Раздел 🟢\nИнгредиент-ориентированные рутинщики.\n"
        warnings = cjm_lint.collect_style_warnings(text)
        overlapping = [w for w in warnings if "ориентированн" in w.excerpt.lower()]
        self.assertEqual(len(overlapping), 1, f"ожидалось 1 предупреждение на это слово, получено: {overlapping}")

    def test_disclaimer_block_is_masked_from_style_warnings(self):
        text = (
            "## Раздел 🟢\nТекст в порядке.\n\n"
            "<!-- DISCLAIMER_BLOCK_START -->\n"
            "Здесь упомянуты ингредиент-ориентированные предположения дословно из шаблона.\n"
            "<!-- DISCLAIMER_BLOCK_END -->\n"
        )
        warnings = cjm_lint.collect_style_warnings(text)
        self.assertEqual(warnings, [])

    def test_style_warnings_are_not_included_in_lint_text_violations(self):
        """Warn-слой НЕ должен просочиться в список Violation, возвращаемый
        lint_text() — контракт для 45 существующих тестов не меняется. Берём
        структурно ПОЛНОСТЬЮ чистый образец (легенда + все маркеры уже есть в
        CLEAN_REPORT), чтобы единственная переменная — вставленный ИИ-изм."""
        text_with_ai_ism = CLEAN_REPORT.replace(
            "## Сегмент 1: Молодые мамы после родов 🟢 (3/3)",
            "## Сегмент 1: Ингредиент-ориентированные рутинщики 🟢 (3/3)",
        )
        violations = cjm_lint.lint_text(text_with_ai_ism)
        self.assertEqual(violations, [])  # ни одно из правил 1-4 не нарушено этим текстом
        self.assertTrue(cjm_lint.collect_style_warnings(text_with_ai_ism))  # но warn-слой это ловит

    def test_lint_files_style_warnings_multi_file(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            p1 = _write(tmp, "a.md", "## Раздел 🟢\nИнгредиент-ориентированные рутинщики.\n")
            p2 = _write(tmp, "b.md", "## Раздел 🟢\nВсё чисто здесь, никаких канцеляризмов.\n")
            results = cjm_lint.lint_files_style_warnings([p1, p2])
            filenames = {fname for fname, _ in results}
            self.assertIn("a.md", filenames)
            self.assertNotIn("b.md", filenames)


# ----------------------------------------------------------------------------
# Самотест на реальном references/cjm_report_template.md (self-skip если ещё нет)
# ----------------------------------------------------------------------------


class TestRealReportTemplate(unittest.TestCase):
    def test_real_template_lints_clean_if_present(self):
        """
        Спецификация §4 требует "юнит-тест на самом шаблоне отчёта" — как только
        сборщик [B1] создаст references/cjm_report_template.md, этот тест проверит
        его напрямую. Самопропуск, если файла ещё нет (не моя зона сборки) —
        не блокирует общий прогон test_cjm_lint.py.
        """
        real_path = _SCRIPTS_DIR.parent / "references" / "cjm_report_template.md"
        if not real_path.exists():
            self.skipTest("references/cjm_report_template.md ещё не создан (не моя зона сборки)")
        violations = cjm_lint.lint_file(real_path)
        self.assertEqual(
            violations,
            [],
            f"references/cjm_report_template.md должен проходить линтер начисто, найдено: {violations}",
        )



class TestHtmlCommentMasking(unittest.TestCase):
    """v1.2, находка №1 (CRITICAL) review_v1.2.md: HTML-комментарии — служебные
    инструкции сборщику, читатель отчёта их не видит; правила 1/2/4 и warn-слой
    к ним не применяются. Порядок препроцессинга: mask_reference_blocks →
    mask_html_comments (маркеры дисклеймеров — сами комментарии)."""

    LEGEND = "Легенда карты доверия: 🟢 🟡 🔴\n"

    def test_violation_inside_single_line_comment_not_flagged(self):
        text = self.LEGEND + "## Раздел 🟢\n<!-- пример: доля рынка 40% без тега -->\nЧистая строка.\n"
        self.assertEqual(cjm_lint.lint_text(text), [])

    def test_violation_inside_multiline_comment_not_flagged(self):
        text = (self.LEGEND + "## Раздел 🟢\n<!--\n  запрещено: switch rate, отнимем 5%\n"
                "  и доля рынка 40 процентов\n-->\nЧистая строка.\n")
        self.assertEqual(cjm_lint.lint_text(text), [])

    def test_same_violation_outside_comment_still_flagged(self):
        text = self.LEGEND + "## Раздел 🟢\nдоля рынка 40% без тега\n"
        self.assertNotEqual(cjm_lint.lint_text(text), [])

    def test_visible_text_around_inline_comment_still_checked(self):
        text = self.LEGEND + "## Раздел 🟢\nдоля рынка 40% <!-- пометка --> без тега\n"
        self.assertNotEqual(cjm_lint.lint_text(text), [])

    def test_style_warning_inside_comment_not_flagged(self):
        text = "<!-- ингредиент-ориентированные рутинщики -->\nОбычный текст.\n"
        self.assertEqual(cjm_lint.collect_style_warnings(text), [])

    def test_disclaimer_masking_survives_comment_stripping(self):
        text = (self.LEGEND + "## Раздел 🟢\n<!-- DISCLAIMER_BLOCK_START -->\n"
                "Здесь упоминается Brand Lift и 40% без тега — это несъёмный блок.\n"
                "<!-- DISCLAIMER_BLOCK_END -->\nЧистая строка.\n")
        self.assertEqual(cjm_lint.lint_text(text), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

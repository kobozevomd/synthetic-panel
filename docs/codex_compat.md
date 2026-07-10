# Совместимость с OpenAI Codex CLI

Источник: `research/2026-07-08/06_tech_implementation.md`, части Б.5-Б.6 (проверено
по официальной документации Codex, июль 2026). Этот файл — заметка о переносе
`synthetic-panel` на Codex, не инструкция по установке (установка под Claude Code —
`SKILL.md` + симлинк `~/.claude/skills/synthetic-panel`, см. корень спецификации §1).

## Что переносится 1:1, без изменений

Codex и Claude Code читают один и тот же открытый стандарт Agent Skills
(agentskills.io): директория со SKILL.md + опциональные `scripts/` и `references/`.
Для этого скилла это означает:

- `SKILL.md` — frontmatter `name` + `description` + тело читаются одинаково на
  обеих платформах; описание триггеров (клеймы/концепты/сегменты/JDE) и раздел
  «Когда применять и когда отказаться» работают без изменений.
- `scripts/*.py` — исполняемый код, вызывается через shell (Bash), не подгружается
  в контекст модели целиком. Скрипты не используют никаких Claude-Code-специфичных
  API — только stdlib, numpy, PyYAML, sentence-transformers/torch, опционально
  anthropic/openai. Это значит `run_study.py --stage generate/score/report`
  работает под Codex буквально тем же кодом.
- `references/*.md`, `references/anchors_ru.yaml` — документация, подгружается по
  явной ссылке, а не всегда; тот же принцип progressive disclosure на обеих
  платформах.
- `panel/segments/*.yaml`, `studies/*.yaml` — простые YAML-данные, платформенно
  нейтральны.
- **Agent-режим генерации** (provider: agent) не завязан на Claude Code конкретно:
  это просто файл `AGENT_TASK.md` с инструкцией + `responses_todo.jsonl`, которые
  заполняет ЛЮБАЯ модель/агент, ведущий CLI-сессию (Codex agent читает тот же
  Markdown-файл и заполняет тот же JSONL той же логикой) — контракт файлового
  обмена, не Claude-Code API.

## Что НЕ переносится / работает по-другому

- **Frontmatter-поля Claude Code** (`allowed-tools`, `model`, `effort`, `context`,
  `disable-model-invocation` и т.п. в `SKILL.md` этого скилла) — Codex их не
  документирует как читаемые; по общему принципу спецификации незнакомые ключи
  YAML игнорируются (no-op, не ошибка). В частности `allowed-tools: [AskUserQuestion,
  Read, Write, Edit, Bash, Glob, Grep]` в шапке `SKILL.md` — специфично для Claude
  Code, под Codex просто не действует (Codex использует свою модель разрешений).
- **Codex-специфичные расширения** живут в ОТДЕЛЬНОМ файле `agents/openai.yaml`
  рядом со скиллом (не в frontmatter SKILL.md!) — там задаются `interface`
  (display_name, short_description, иконки), `policy.allow_implicit_invocation`,
  `dependencies.tools`. Этот файл в v1 НЕ создан (не требуется спецификацией) —
  добавить его можно аддитивно в любой момент, без изменений в остальном дереве.
- **Пути обнаружения скиллов Codex**: по официальной документации —
  `$CWD/.agents/skills`, `$REPO_ROOT/.agents/skills`, `$HOME/.agents/skills`,
  `/etc/codex/skills` (символические ссылки поддерживаются). Независимый
  вторичный источник называет более старые пути `~/.codex/skills/` и
  `.codex/skills/` — вероятно, конвенция до перехода на общий `.agents/`-стандарт;
  точная дата смены не подтверждена (см. дословно `06_tech_implementation.md`, Б.5).
  **Практическое следствие:** при установке под Codex проверить, какой путь
  реально ищет установленная версия CLI, и предусмотреть оба варианта (симлинк
  можно создать в обоих местах одновременно — это дёшево).
- **Always-on контекст проекта**: Claude Code использует `CLAUDE.md`, Codex —
  `AGENTS.md` (у этого репозитория такой файл на уровне PROJECT, не внутри
  `synthetic-panel/`). Если потребуется общий always-on файл для Codex-сессии
  именно в контуре скилла — стандартный приём (уже есть прецедент в стороннем
  скилле `synthetic-market-research`, аудированном в `06_tech_implementation.md`,
  А.1: `CLAUDE.md -> SKILL.md`) — симлинк `AGENTS.md -> CLAUDE.md` или обратно,
  не дублирование текста.
- **Temperature-контроль генерации через Claude Agent SDK** (см.
  `06_tech_implementation.md`, Б.2-Б.3) и хуки Claude Code для автоматического
  логирования прогонов — платформенно-специфичные механизмы Claude Code без
  документированного эквивалента на стороне Codex на момент подготовки досье;
  не влияет на сам скилл (`generate.py` в режимах `anthropic`/`openai` уже
  делает temperature явным параметром прямого API-вызова, а не полагается на
  механизмы конкретного CLI — см. `scripts/generate.py`).

## Рекомендация для установки под Codex

1. Скопировать/симлинковать `<PROJECT>/synthetic-panel/` в один (или оба, на
   всякий случай) из путей обнаружения скиллов Codex, например:
   `ln -s "<PROJECT>/synthetic-panel" ~/.agents/skills/synthetic-panel`.
2. Ядро (SKILL.md-тело, scripts/, references/, panel/, studies/) не требует
   правок. При желании — добавить `agents/openai.yaml` с `display_name`/
   `short_description`/иконкой для более аккуратного отображения в Codex UI
   (аддитивно, необязательно для функциональности).
3. Проверить `bash scripts/setup.sh` и `python scripts/test_ssr.py` внутри
   Codex-сессии тем же способом, что и в Claude Code — оба шага платформенно
   нейтральны (обычный venv + pytest/unittest).

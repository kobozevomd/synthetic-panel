#!/usr/bin/env bash
# setup.sh — создаёт .venv и ставит зависимости synthetic-panel (spec_synthetic-panel_v1.md §11.1).
#
# DoD: на чистой машине с сетью создаёт .venv и ставит numpy, PyYAML,
# sentence-transformers+torch (CPU-only wheel), anthropic, openai. Последние два —
# best-effort (не обязательны для provider: agent, дефолт config.yaml, который не
# делает прямых вызовов LLM API — см. scripts/generate.py).
#
# Идемпотентен: повторный запуск на уже существующем .venv просто доустанавливает
# недостающее — не пересоздаёт venv и не падает, если что-то уже стоит.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$SKILL_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "== synthetic-panel: setup.sh =="
echo "Директория скилла: $SKILL_DIR"

if [ ! -d "$VENV_DIR" ]; then
  echo "-- Создаю venv: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
else
  echo "-- venv уже существует: $VENV_DIR (переиспользую, доустанавливаю недостающее)"
fi

PIP="$VENV_DIR/bin/pip"
PY="$VENV_DIR/bin/python"

echo "-- Обновляю pip"
"$PY" -m pip install --upgrade pip --quiet

echo "-- Обязательные зависимости: numpy, PyYAML"
"$PIP" install --quiet "numpy>=1.24" "PyYAML>=6.0"

echo "-- torch (CPU-only wheel) — установка может занять несколько минут"
"$PIP" install --quiet torch --index-url https://download.pytorch.org/whl/cpu

echo "-- sentence-transformers (embedding-слой стадии --stage score)"
"$PIP" install --quiet sentence-transformers

echo "-- anthropic/openai (опционально, best-effort — не нужны provider: agent)"
if ! "$PIP" install --quiet anthropic openai; then
  echo "!! anthropic/openai не установились — не критично для provider: agent (дефолт config.yaml)." >&2
fi

echo ""
echo "== Зависимости установлены =="
echo "-- Смоук-тест SSR-математики (без сети, ссылка на DoD §11.2):"
"$PY" "$SCRIPT_DIR/test_ssr.py" 2>&1 | tail -6 || true

echo ""
echo "Готово. Активация окружения вручную: source $VENV_DIR/bin/activate"
echo "Первый прогон score-стадии скачает embedding-модель с HuggingFace (см. config.yaml: embedding.model)."

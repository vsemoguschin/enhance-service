#!/usr/bin/env bash
# Запуск под pm2 с секретами из .env (файл в .gitignore, права 600).
#
# Зачем не `pm2 restart --update-env`: тот берёт окружение из шелла, где запущен pm2 CLI,
# и любая переменная, отсутствующая в этом шелле, молча теряется. Для ENHANCE_API_KEY это
# особенно опасно: пустой ключ в config.py = auth отключён, эндпоинты открыты.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

exec .venv/bin/python -m uvicorn app.main:app \
  --host "${ENHANCE_HOST:-0.0.0.0}" \
  --port "${ENHANCE_PORT:-8011}"

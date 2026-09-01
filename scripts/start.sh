#!/usr/bin/env bash
# Запуск под pm2 с секретами из .env (файл в .gitignore, права 600).
#
# Зачем не `pm2 restart --update-env`: тот берёт окружение из шелла, где запущен pm2 CLI,
# и любая переменная, отсутствующая в этом шелле, молча теряется. Для ENHANCE_API_KEY это
# особенно опасно: пустой ключ в config.py = auth отключён, эндпоинты открыты.
set -euo pipefail

cd "$(dirname "$0")/.."

# PM2 blue/green slots pass only non-secret overrides. Preserve them across `.env`,
# whose values remain authoritative for secrets and common provider configuration.
slot_override="${ENHANCE_SLOT_OVERRIDE:-}"
port_override="${ENHANCE_PORT_OVERRIDE:-}"
host_override="${ENHANCE_HOST_OVERRIDE:-}"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

if [ -n "$slot_override" ]; then
  export ENHANCE_SLOT="$slot_override"
fi
if [ -n "$port_override" ]; then
  export ENHANCE_PORT="$port_override"
fi
if [ -n "$host_override" ]; then
  export ENHANCE_HOST="$host_override"
fi

exec .venv/bin/python -m uvicorn app.main:app \
  --host "${ENHANCE_HOST:-0.0.0.0}" \
  --port "${ENHANCE_PORT:-8011}"

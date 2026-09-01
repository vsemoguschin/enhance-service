#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^[ab]$ ]]; then
  echo "usage: sudo bash ./scripts/switch-active-slot.sh <a|b>" >&2
  exit 2
fi

slot="$1"
snippet_dir="${ENHANCE_NGINX_SNIPPET_DIR:-/etc/nginx/snippets}"
target="${snippet_dir}/enhance-active-${slot}.conf"
active="${snippet_dir}/enhance-active-proxy.conf"

if [[ ! -f "$target" ]]; then
  echo "missing slot snippet: $target" >&2
  exit 1
fi

ln -sfn "$target" "${active}.next"
mv -Tf "${active}.next" "$active"
nginx -t
systemctl reload nginx
echo "enhance active slot: $slot"

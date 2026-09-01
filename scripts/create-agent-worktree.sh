#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "usage: bash ./scripts/create-agent-worktree.sh <task-slug>" >&2
  exit 2
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dated_slug="$(date +%F)-$1"
branch="agent/${dated_slug}"
worktree="${repo_dir}/.codex-workspaces/agents/${dated_slug}"

if [[ "$(git -C "$repo_dir" branch --show-current)" != "main" ]]; then
  echo "create worktree from main" >&2
  exit 1
fi
if [[ -n "$(git -C "$repo_dir" status --short)" ]]; then
  echo "main worktree is not clean" >&2
  git -C "$repo_dir" status --short >&2
  exit 1
fi

mkdir -p "${repo_dir}/.codex-workspaces/agents"
git -C "$repo_dir" worktree add -b "$branch" "$worktree" main

mkdir -p "$worktree/docs/audits"
audit="$worktree/docs/audits/${dated_slug}.md"
if [[ ! -e "$audit" ]]; then
  printf '# %s\n\n## Задача\n\n## Аудит\n\n## План\n\n## Результат\n' "$dated_slug" > "$audit"
fi

printf 'Branch: %s\nWorktree: %s\nAudit: %s\n' "$branch" "$worktree" "$audit"

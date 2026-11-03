#!/usr/bin/env bash
set -euo pipefail
repo="$HOME/electionsarchive"
tree="$HOME/.cache/ea-i18n"
exec 9> "$repo/data/logs/i18n-sync.lock"
flock -n 9 || exit 0
if [ ! -d "$tree" ]; then
  git -C "$repo" fetch -q origin main
  git -C "$repo" worktree add -q --detach "$tree" origin/main
  ln -s "$repo/data" "$tree/data"
  ln -s "$repo/.env" "$tree/.env"
fi
cd "$tree"
git fetch -q origin main
git checkout -q --detach origin/main
node apps/web/scripts/i18n-check.js > /dev/null 2>&1 && exit 0
(cd pipelines && uv sync -q && anthropic-batch uv run ea i18n run)
node apps/web/scripts/i18n-check.js
git add apps/web/messages
git diff --cached --quiet && exit 0
git commit -q -m "web: translate the English strings edited since the last sync; checked with i18n:check"
git push -q origin HEAD:main

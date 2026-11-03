#!/usr/bin/env bash
set -euo pipefail
repo="$HOME/electionsarchive"
tree="$HOME/.cache/ea-i18n"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
exec 9> "$repo/data/logs/i18n-sync.lock"
flock -n 9 || { log "another sync holds the lock"; exit 0; }
if [ ! -d "$tree" ]; then
  git -C "$repo" fetch -q origin main
  git -C "$repo" worktree add -q --detach "$tree" origin/main
  ln -s "$repo/data" "$tree/data"
  ln -s "$repo/.env" "$tree/.env"
fi
cd "$tree"
git rebase --abort > /dev/null 2>&1 || true
git fetch -q origin main
git checkout -q -f --detach origin/main
git clean -q -fd apps/web/messages
base=$(git rev-parse --short HEAD)
if node apps/web/scripts/i18n-check.js > /dev/null 2>&1; then
  exit 0
fi
log "main at $base needs translations"
cd pipelines
uv sync -q
set +e
timeout 15m anthropic-batch uv run ea i18n run --once
code=$?
set -e
cd "$tree"
if [ "$code" -eq 124 ]; then
  log "the step ran past 15 minutes; the next tick resumes it"
  exit 0
elif [ "$code" -ne 0 ]; then
  log "ea i18n run failed with exit $code"
  exit "$code"
fi
if ! node apps/web/scripts/i18n-check.js > /dev/null 2>&1; then
  log "batches still running; the next tick resumes"
  exit 0
fi
"$repo/node_modules/.bin/oxfmt" apps/web/messages > /dev/null
git add apps/web/messages
if git diff --cached --quiet; then
  log "nothing to commit"
  exit 0
fi
git commit -q -m "web: translate the English strings edited since the last sync (main at $base); checked with i18n:check"
for attempt in 1 2 3; do
  git fetch -q origin main
  if ! git merge-base --is-ancestor origin/main HEAD; then
    if ! git rebase -q origin/main; then
      git rebase --abort
      log "main moved and the locales conflict; dropping this commit, the next tick starts over"
      exit 0
    fi
    if ! node apps/web/scripts/i18n-check.js > /dev/null 2>&1; then
      log "main moved and changed the English again; the next tick starts over"
      exit 0
    fi
  fi
  if git push -q origin HEAD:main; then
    log "pushed $(git rev-parse --short HEAD)"
    exit 0
  fi
  log "push $attempt rejected; retrying"
done
log "push failed three times; the next tick starts over"
exit 1

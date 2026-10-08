#!/usr/bin/env bash
# Saves the results after each cloud run:
#   1. app data (docs/data) and, on first install, the app code -> main branch
#   2. the journal database -> 'radar-data' branch (one commit, force-pushed, so the repository stays small)
#   3. after the first install, starts the first real scan
set -uo pipefail
cd "$(dirname "$0")/.."
MODE="$(cat .radar_mode 2>/dev/null || echo scan)"
BOT_NAME="stock-radar-bot"
BOT_MAIL="41898282+github-actions[bot]@users.noreply.github.com"
git config user.name "$BOT_NAME"
git config user.email "$BOT_MAIL"

git add -A
if ! git diff --cached --quiet; then
  git commit -q -m "radar: $MODE $(date -u +%F)"
  for _ in 1 2 3; do
    git push -q && break
    # another run saved first: replay this run on top of it (this run's files win on conflicts)
    git pull -q --rebase -X theirs origin main || { git rebase --abort 2>/dev/null; sleep 5; }
  done
fi

if [ -f data/journal.sqlite ]; then
  blob="$(git hash-object -w data/journal.sqlite)"
  tree="$(printf '100644 blob %s\tjournal.sqlite\n' "$blob" | git mktree)"
  commit="$(git commit-tree "$tree" -m "journal $(date -u +%F)")"
  git push -q -f origin "$commit:refs/heads/radar-data" || echo "warning: could not save the journal"
fi

if [ "$MODE" = setup ] && [ ! -f docs/data/latest.json ]; then
  gh workflow run radar.yml -f mode=scan || echo "warning: could not start the first scan - run it from the app or the Actions tab"
fi
exit 0

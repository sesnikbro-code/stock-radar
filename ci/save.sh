#!/usr/bin/env bash
# Saves the results after each cloud run:
#   1. app data (docs/data) and, on first install, the app code -> main branch
#   2. the journal database -> 'radar-data' branch (one commit, force-pushed, so the repository stays small)
#   3. starts the next run when needed (first scan, model training)
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

# chain the next run (only one per run, so runs never cancel each other):
#   first install -> first scan;  daily scan or update -> train the probability model when it is missing or old;
#   update with a model that is still fresh -> a new scan with the new version;
#   successful training -> a fresh scan, so the app shows the new probabilities right away
if [ "$MODE" = setup ] && [ ! -f docs/data/latest.json ]; then
  gh workflow run radar.yml -f mode=scan || echo "warning: could not start the first scan - run it from the app or the Actions tab"
elif [ "$MODE" = train ]; then
  if [ "$(cat .radar_rc 2>/dev/null)" = 0 ]; then
    gh workflow run radar.yml -f mode=scan || echo "warning: could not start the scan after training"
  fi
elif { [ "$MODE" = scan ] || [ "$MODE" = setup ]; } && [ "$(python3 ci/need_train.py 2>/dev/null)" = yes ]; then
  gh workflow run radar.yml -f mode=train || echo "warning: could not start model training"
elif [ "$MODE" = setup ]; then
  # an update: scan again right away, so the app shows what the new version checks
  gh workflow run radar.yml -f mode=scan || echo "warning: could not start a scan after the update"
fi
exit 0

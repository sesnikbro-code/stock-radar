#!/usr/bin/env bash
# Runs inside GitHub Actions (called from .github/workflows/radar.yml).
# Modes: setup (first install or update: starts the first real scan), scan, ticker, backtest, train, test-alert.
set -uo pipefail
cd "$(dirname "$0")/.."

MODE="${MODE:-}"
MODE="${MODE:-scan}"
[ -f .radar_setup ] && MODE=setup
RAW="$(printf '%s' "${SYMBOLS:-}" | tr '[:lower:]' '[:upper:]' | tr ',;' '  ')"
SYMBOLS=""
for t in $RAW; do
  if [[ "$t" =~ ^[A-Z][A-Z0-9.-]{0,9}$ ]]; then SYMBOLS="$SYMBOLS $t"; fi
done
SYMBOLS="${SYMBOLS# }"
echo "$MODE" > .radar_mode
echo "mode=$MODE symbols=$SYMBOLS"

fail_status() {
  mkdir -p docs/data
  printf '{"mode":"%s","ok":false,"message":"%s","finished":"%s"}\n' "$MODE" "$1" "$(date -u +%FT%TZ)" > docs/data/status.json
}

# A queued run may have been checked out before an earlier run saved its results: refresh the app data.
if git fetch -q origin main 2>/dev/null; then
  git checkout -q FETCH_HEAD -- docs/data 2>/dev/null || true
fi

# GitHub sometimes skips scheduled runs, so the workflow has backup times later in the morning.
# A backup run stops here when today's scan already exists.
if [ "${GITHUB_EVENT_NAME:-}" = schedule ] && [ "$MODE" = scan ] && python3 - <<'PY'
import json, sys
from datetime import datetime, timezone
try:
    day = json.load(open("docs/data/latest.json", encoding="utf-8"))["date"]
except Exception:
    sys.exit(1)
sys.exit(0 if day == datetime.now(timezone.utc).date().isoformat() else 1)
PY
then
  echo "today's scan already exists - nothing to do in this backup run"
  echo skip > .radar_mode
  echo 0 > .radar_rc
  exit 0
fi

if [ -z "${RADAR_SKIP_INSTALL:-}" ] && ! { python -m pip install -q --upgrade pip && python -m pip install -q -r requirements.txt; }; then
  fail_status "התקנת החבילות נכשלה בענן. נסה להריץ שוב מאוחר יותר."
  exit 1
fi

# The journal (track record, paper trades, learned weights) lives on the 'radar-data' branch.
mkdir -p data
if git fetch -q --depth=1 origin radar-data 2>/dev/null; then
  git show FETCH_HEAD:journal.sqlite > data/journal.sqlite 2>/dev/null || rm -f data/journal.sqlite
fi

# keep the cloud cache small: old daily price files and stale downloads are not needed
if [ -d data/cache ]; then
  find data/cache -name 'prices_*.pkl' -mtime +1 -delete 2>/dev/null || true
  find data/cache -type f -mtime +60 -delete 2>/dev/null || true
fi

rc=0
case "$MODE" in
  setup)
    # first install or update: no demo data in the cloud, only real scans
    rm -rf docs/data/demo
    mkdir -p docs/data
    if [ -f docs/data/latest.json ] && [ "$(python3 ci/need_train.py 2>/dev/null)" = yes ]; then
      msg="העדכון הותקן בהצלחה. עכשיו מתחיל אימון של מודל הסיכוי על 5 שנות היסטוריה אמיתית (כ-15 עד 40 דקות), ואחריו סריקה חדשה."
    elif [ -f docs/data/latest.json ]; then
      msg="העדכון הותקן בהצלחה."
    else
      msg="ההתקנה הסתיימה. הסריקה הראשונה של מניות אמיתיות התחילה ותסתיים בעוד כ-30 עד 60 דקות."
    fi
    printf '{"mode":"setup","ok":true,"message":"%s","finished":"%s"}\n' "$msg" "$(date -u +%FT%TZ)" > docs/data/status.json
    ;;
  scan)
    python run.py --export docs/data scan || rc=$?
    ;;
  ticker)
    if [ -z "$SYMBOLS" ]; then fail_status "לא הוזן סימול מניה"; exit 1; fi
    # shellcheck disable=SC2086
    python run.py --export docs/data ticker $SYMBOLS || rc=$?
    ;;
  backtest)
    python run.py --export docs/data backtest || rc=$?
    ;;
  train)
    echo "$(date -u +%F) $(python3 ci/need_train.py --spec 2>/dev/null)" > docs/data/model_attempt.txt
    python run.py --export docs/data train || rc=$?
    ;;
  test-alert)
    python run.py --export docs/data test-alert || rc=$?
    ;;
  *)
    fail_status "סוג הרצה לא מוכר: $MODE"
    exit 1
    ;;
esac
echo "$rc" > .radar_rc
exit "$rc"

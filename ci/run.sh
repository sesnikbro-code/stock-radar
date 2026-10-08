#!/usr/bin/env bash
# Runs inside GitHub Actions (called from .github/workflows/radar.yml).
# Modes: setup (first install: demo data + starts the first real scan), scan, ticker, backtest, test-alert.
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

if [ -z "${RADAR_SKIP_INSTALL:-}" ] && ! { python -m pip install -q --upgrade pip && python -m pip install -q -r requirements.txt; }; then
  fail_status "התקנת החבילות נכשלה בענן. נסה להריץ שוב מאוחר יותר."
  exit 1
fi

# The journal (track record, paper trades, learned weights) lives on the 'radar-data' branch.
mkdir -p data
if git fetch -q --depth=1 origin radar-data 2>/dev/null; then
  git show FETCH_HEAD:journal.sqlite > data/journal.sqlite 2>/dev/null || rm -f data/journal.sqlite
fi

rc=0
case "$MODE" in
  setup)
    python run.py --export docs/data scan --demo --demo-history 120 --no-send || rc=$?
    python run.py --export docs/data ticker DMO005 --demo || true
    python run.py --export docs/data backtest --demo || true
    if [ "$rc" = 0 ]; then
      mkdir -p docs/data
      printf '{"mode":"setup","ok":true,"message":"%s","finished":"%s"}\n' \
        "ההתקנה הסתיימה. הסריקה האמיתית הראשונה התחילה ותסתיים בעוד כ-30 עד 60 דקות." \
        "$(date -u +%FT%TZ)" > docs/data/status.json
    fi
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
  test-alert)
    python run.py --export docs/data test-alert || rc=$?
    ;;
  *)
    fail_status "סוג הרצה לא מוכר: $MODE"
    exit 1
    ;;
esac
exit "$rc"

#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PY="${PY:-$ROOT/.venv/bin/python}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
SESSION="${SESSION:-rapidapi_mechanism_${RUN_ID}}"
LOG_DIR="$ROOT/logs/rapidapi_mechanism_completion"
LAUNCHER_STATUS="$LOG_DIR/launcher_${RUN_ID}.json"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-8}"
RESTART_DELAY_SECONDS="${RESTART_DELAY_SECONDS:-600}"

mkdir -p "$LOG_DIR"
EXISTING="$(screen -ls 2>/dev/null | awk '/rapidapi_mechanism_/ && /Detached|Attached/ {print $1}' | head -n 1 || true)"
if [[ -n "$EXISTING" ]]; then
  echo "already_running screen_session=$EXISTING"
  exit 0
fi

RUN_ID="$RUN_ID" MAX_ATTEMPTS="$MAX_ATTEMPTS" RESTART_DELAY_SECONDS="$RESTART_DELAY_SECONDS" \
  screen -dmS "$SESSION" /usr/bin/caffeinate -dimsu /bin/bash \
  rapidapi_crawl/scripts/supervise_mechanism_completion.sh
printf '%s\n' "$RUN_ID" > "$LOG_DIR/latest_run"
printf '%s\n' "$SESSION" > "$LOG_DIR/current_screen_session"

"$PY" - "$LAUNCHER_STATUS" "$RUN_ID" "$SESSION" <<'PY'
import json
import sys
from datetime import datetime, timezone

path, run_id, session = sys.argv[1:4]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "run_id": run_id,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "state": "started",
            "screen_session": session,
        },
        handle,
        ensure_ascii=False,
        indent=2,
    )
PY

echo "started screen_session=$SESSION run_id=$RUN_ID"
echo "status=$LOG_DIR/status_${RUN_ID}.json"
echo "run_log=$LOG_DIR/run_${RUN_ID}.log"
echo "supervisor_log=$LOG_DIR/supervisor_${RUN_ID}.log"

#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PY="${PY:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  echo "Python environment not found at $PY" >&2
  exit 2
fi

RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
SESSION="${SESSION:-rapidapi_weekly_${RUN_ID}}"
LOG_DIR="$ROOT/logs/rapidapi_weekly_incremental"
SUPERVISOR="$LOG_DIR/supervisor_${RUN_ID}.sh"
LAUNCHER_STATUS="$LOG_DIR/launcher_${RUN_ID}.json"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-4}"
RESTART_DELAY_SECONDS="${RESTART_DELAY_SECONDS:-600}"

mkdir -p "$LOG_DIR"

running_sessions() {
  screen -ls 2>/dev/null | awk '/rapidapi_weekly_/ && /Detached|Attached/ {print $1}' || true
}

EXISTING="$(running_sessions | head -n 1 || true)"
if [[ -n "$EXISTING" ]]; then
  "$PY" - "$LAUNCHER_STATUS" "$RUN_ID" "$EXISTING" <<'PY'
import json
import sys
from datetime import datetime, timezone

path, run_id, existing = sys.argv[1:4]
with open(path, "w", encoding="utf-8") as f:
    json.dump(
        {
            "run_id": run_id,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "state": "already_running",
            "screen_session": existing,
            "message": "A weekly RapidAPI incremental background job is already running; no duplicate was started.",
        },
        f,
        ensure_ascii=False,
        indent=2,
    )
PY
  echo "already_running screen_session=$EXISTING"
  exit 0
fi

cat > "$SUPERVISOR" <<EOF
#!/usr/bin/env bash
set -u
cd "$ROOT"

RUN_ID="$RUN_ID"
MAX_ATTEMPTS="$MAX_ATTEMPTS"
RESTART_DELAY_SECONDS="$RESTART_DELAY_SECONDS"
LOG_DIR="$LOG_DIR"
STATUS_FILE="\$LOG_DIR/status_\${RUN_ID}.json"
SUPERVISOR_LOG="\$LOG_DIR/supervisor_\${RUN_ID}.log"

write_status() {
  local step="\$1"
  local state="\$2"
  local message="\${3:-}"
  "$PY" - "\$STATUS_FILE" "\$RUN_ID" "\$step" "\$state" "\$message" <<'PY'
import json
import sys
from datetime import datetime, timezone

path, run_id, step, state, message = sys.argv[1:6]
with open(path, "w", encoding="utf-8") as f:
    json.dump(
        {
            "run_id": run_id,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "step": step,
            "state": state,
            "message": message,
        },
        f,
        ensure_ascii=False,
        indent=2,
    )
PY
}

attempt=1
while [[ "\$attempt" -le "\$MAX_ATTEMPTS" ]]; do
  echo "===== \$(date '+%Y-%m-%dT%H:%M:%S%z') supervisor attempt \$attempt/\$MAX_ATTEMPTS =====" >> "\$SUPERVISOR_LOG"
  write_status "background_supervisor" "running" "attempt=\$attempt/\$MAX_ATTEMPTS"
  RUN_ID="\$RUN_ID" bash rapidapi_crawl/scripts/run_weekly_incremental_update.sh
  code=\$?
  if [[ "\$code" -eq 0 ]]; then
    echo "===== \$(date '+%Y-%m-%dT%H:%M:%S%z') supervisor complete =====" >> "\$SUPERVISOR_LOG"
    write_status "background_supervisor" "complete" "attempt=\$attempt"
    exit 0
  fi
  echo "===== \$(date '+%Y-%m-%dT%H:%M:%S%z') worker failed exit=\$code =====" >> "\$SUPERVISOR_LOG"
  attempt=\$((attempt + 1))
  if [[ "\$attempt" -le "\$MAX_ATTEMPTS" ]]; then
    write_status "background_supervisor" "retry_wait" "last_exit=\$code; sleeping=\$RESTART_DELAY_SECONDS seconds; next_attempt=\$attempt"
    sleep "\$RESTART_DELAY_SECONDS"
  fi
done

write_status "background_supervisor" "failed" "exhausted attempts=\$MAX_ATTEMPTS"
exit 1
EOF

chmod +x "$SUPERVISOR"

screen -dmS "$SESSION" /usr/bin/caffeinate -dimsu /bin/bash "$SUPERVISOR"
printf '%s\n' "$RUN_ID" > "$LOG_DIR/latest_run"
printf '%s\n' "$SESSION" > "$LOG_DIR/current_screen_session"

"$PY" - "$LAUNCHER_STATUS" "$RUN_ID" "$SESSION" "$MAX_ATTEMPTS" "$RESTART_DELAY_SECONDS" <<'PY'
import json
import sys
from datetime import datetime, timezone

path, run_id, session, max_attempts, restart_delay = sys.argv[1:6]
with open(path, "w", encoding="utf-8") as f:
    json.dump(
        {
            "run_id": run_id,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "state": "started",
            "screen_session": session,
            "max_attempts": int(max_attempts),
            "restart_delay_seconds": int(restart_delay),
            "status_file": f"logs/rapidapi_weekly_incremental/status_{run_id}.json",
            "run_log": f"logs/rapidapi_weekly_incremental/run_{run_id}.log",
            "supervisor_log": f"logs/rapidapi_weekly_incremental/supervisor_{run_id}.log",
        },
        f,
        ensure_ascii=False,
        indent=2,
    )
PY

echo "started screen_session=$SESSION run_id=$RUN_ID"
echo "status=$LOG_DIR/status_${RUN_ID}.json"
echo "run_log=$LOG_DIR/run_${RUN_ID}.log"
echo "supervisor_log=$LOG_DIR/supervisor_${RUN_ID}.log"

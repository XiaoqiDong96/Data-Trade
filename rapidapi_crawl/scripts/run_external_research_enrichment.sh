#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PY="${PY:-$ROOT/.venv/bin/python}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
LOG_DIR="$ROOT/logs/external_research_enrichment"
LOG_FILE="$LOG_DIR/run_${RUN_ID}.log"
STATUS_FILE="$LOG_DIR/status_${RUN_ID}.json"
WORKERS="${WORKERS:-3}"
DELAY="${DELAY:-0.45}"

mkdir -p "$LOG_DIR" rapidapi_crawl/data_external rapidapi_crawl/external_raw
printf '%s\n' "$RUN_ID" > "$LOG_DIR/latest_run"
exec >> "$LOG_FILE" 2>&1

write_status() {
  local step="$1" state="$2" message="${3:-}"
  "$PY" - "$STATUS_FILE" "$RUN_ID" "$step" "$state" "$message" <<'PY'
import json, sys
from datetime import datetime, timezone
path, run_id, step, state, message = sys.argv[1:6]
with open(path, "w", encoding="utf-8") as f:
    json.dump({"run_id": run_id, "updated_at_utc": datetime.now(timezone.utc).isoformat(), "step": step, "state": state, "message": message}, f, ensure_ascii=False, indent=2)
PY
}

run_stage() {
  local stage="$1"
  write_status "$stage" "running" "workers=$WORKERS delay=$DELAY"
  "$PY" rapidapi_crawl/scripts/external_research_enrichment.py \
    --root rapidapi_crawl --stages "$stage" --workers "$WORKERS" --delay "$DELAY" --retry-errors
  write_status "$stage" "done" ""
}

trap 'code=$?; write_status "pipeline" "failed" "exit=$code line=${BASH_LINENO[0]:-unknown}"; exit "$code"' ERR

write_status "pipeline" "running" "external enrichment initialized"
run_stage adoption
run_stage open_substitutes
run_stage schema_overlap
run_stage response_samples
run_stage competitors
run_stage owners
run_stage macro
run_stage build
write_status "pipeline" "complete" "all external sources normalized and merged"

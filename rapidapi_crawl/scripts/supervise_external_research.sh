#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

RUN_ID="${RUN_ID:?RUN_ID is required}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-8}"
RESTART_DELAY_SECONDS="${RESTART_DELAY_SECONDS:-300}"
LOG_DIR="$ROOT/logs/external_research_enrichment"
SUPERVISOR_LOG="$LOG_DIR/supervisor_${RUN_ID}.log"

attempt=1
while [[ "$attempt" -le "$MAX_ATTEMPTS" ]]; do
  echo "===== $(date '+%Y-%m-%dT%H:%M:%S%z') attempt=$attempt/$MAX_ATTEMPTS =====" >> "$SUPERVISOR_LOG"
  RUN_ID="$RUN_ID" bash rapidapi_crawl/scripts/run_external_research_enrichment.sh
  code=$?
  if [[ "$code" -eq 0 ]]; then
    exit 0
  fi
  attempt=$((attempt + 1))
  if [[ "$attempt" -le "$MAX_ATTEMPTS" ]]; then
    sleep "$RESTART_DELAY_SECONDS"
  fi
done
exit 1

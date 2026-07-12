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
HISTORY_DIR="$ROOT/rapidapi_crawl/data_incremental"
WORK_ROOT="$HISTORY_DIR/work_${RUN_ID}"
OUT_DIR="$HISTORY_DIR/${RUN_ID}"
LOG_DIR="$ROOT/logs/rapidapi_weekly_incremental"
LOG_FILE="$LOG_DIR/run_${RUN_ID}.log"
STATUS_FILE="$LOG_DIR/status_${RUN_ID}.json"

DISCOVERY_DELAY="${DISCOVERY_DELAY:-0.45}"
DETAIL_WORKERS="${DETAIL_WORKERS:-3}"
DETAIL_DELAY="${DETAIL_DELAY:-0.70}"
STATIC_WORKERS="${STATIC_WORKERS:-2}"
STATIC_DELAY="${STATIC_DELAY:-1.00}"
ADDITIONAL_WORKERS="${ADDITIONAL_WORKERS:-2}"
ADDITIONAL_DELAY="${ADDITIONAL_DELAY:-1.00}"
EXPOSURE_WORKERS="${EXPOSURE_WORKERS:-2}"
EXPOSURE_DELAY="${EXPOSURE_DELAY:-1.00}"
EXPOSURE_TERMS_MODE="${EXPOSURE_TERMS_MODE:-broad}"
EXPOSURE_MAX_PAGES="${EXPOSURE_MAX_PAGES:-10}"

mkdir -p "$LOG_DIR" "$WORK_ROOT/data" "$OUT_DIR"
exec >> "$LOG_FILE" 2>&1

ts() {
  date '+%Y-%m-%dT%H:%M:%S%z'
}

status() {
  local step="$1"
  local state="$2"
  local message="${3:-}"
  "$PY" - "$STATUS_FILE" "$RUN_ID" "$step" "$state" "$message" <<'PY'
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

run() {
  local step="$1"
  shift
  echo
  echo "===== $(ts) START $step ====="
  status "$step" "running" "$*"
  "$@"
  echo "===== $(ts) DONE $step ====="
}

on_error() {
  local code=$?
  local line=${BASH_LINENO[0]:-unknown}
  echo "===== $(ts) ERROR line=$line exit=$code ====="
  status "error" "failed" "line=$line exit=$code"
  exit "$code"
}
trap on_error ERR

echo "RapidAPI weekly incremental update"
echo "root=$ROOT"
echo "run_id=$RUN_ID"
echo "work_root=$WORK_ROOT"
echo "out_dir=$OUT_DIR"
echo "log=$LOG_FILE"
status "started" "running" "weekly incremental update initialized"

run "search_window" \
  "$PY" rapidapi_crawl/scripts/rapidapi_crawler.py \
  --root "$WORK_ROOT" \
  --category Data \
  --first 100 \
  --max-pages 0 \
  --delay "$DISCOVERY_DELAY"

run "broad_discovery" \
  "$PY" rapidapi_crawl/scripts/rapidapi_discovery_crawler.py \
  --root "$WORK_ROOT" \
  --category Data \
  --terms-mode broad \
  --first 100 \
  --max-pages-per-combo 0 \
  --delay "$DISCOVERY_DELAY" \
  --seed-existing

run "prepare_new_api_list" \
  "$PY" rapidapi_crawl/scripts/build_weekly_incremental_delta.py prepare \
  --work-root "$WORK_ROOT" \
  --merged-dir rapidapi_crawl/data_merged \
  --history-dir rapidapi_crawl/data_incremental \
  --out-dir "$OUT_DIR" \
  --run-id "$RUN_ID"

NEW_COUNT=$("$PY" - "$OUT_DIR/rapidapi_weekly_prepare_summary.json" <<'PY'
import json
import sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    print(json.load(f).get("new_api_candidates", 0))
PY
)
echo "new_api_candidates=$NEW_COUNT"

if [[ "$NEW_COUNT" == "0" ]]; then
  rm -rf "$WORK_ROOT"
  status "complete" "complete" "no new API ids found"
  echo "===== $(ts) COMPLETE no new API ids ====="
  exit 0
fi

run "detail_new_apis" \
  "$PY" rapidapi_crawl/scripts/rapidapi_detail_parallel.py \
  --root "$WORK_ROOT" \
  --category Data \
  --source-csv "$WORK_ROOT/data/rapidapi_discovery_Data_apis.csv" \
  --workers "$DETAIL_WORKERS" \
  --delay "$DETAIL_DELAY" \
  --retry-errors

run "normalize_new_details" \
  "$PY" rapidapi_crawl/scripts/rapidapi_crawler.py \
  --root "$WORK_ROOT" \
  --category Data \
  --skip-search \
  --details \
  --details-source discovery \
  --details-limit 0 \
  --details-offline-only

run "base_new_plan_panel" \
  "$PY" rapidapi_crawl/scripts/build_rapidapi_panel.py \
  --root "$WORK_ROOT" \
  --category Data

run "static_new_enrichment" \
  "$PY" rapidapi_crawl/scripts/rapidapi_static_enrichment.py \
  --root "$WORK_ROOT" \
  --category Data \
  --kinds playground,billing_endpoints,owner \
  --workers "$STATIC_WORKERS" \
  --delay "$STATIC_DELAY" \
  --retry-errors

run "static_new_panel" \
  "$PY" rapidapi_crawl/scripts/build_static_enriched_panel.py \
  --root "$WORK_ROOT" \
  --category Data

run "additional_new_market_data" \
  "$PY" rapidapi_crawl/scripts/rapidapi_additional_market_data.py \
  --root "$WORK_ROOT" \
  --category Data \
  --kinds healthcheck,detail_extras \
  --workers "$ADDITIONAL_WORKERS" \
  --delay "$ADDITIONAL_DELAY" \
  --retry-errors

run "search_exposure_for_new_filter" \
  "$PY" rapidapi_crawl/scripts/rapidapi_search_exposure_crawler.py \
  --root "$WORK_ROOT" \
  --category Data \
  --terms-mode "$EXPOSURE_TERMS_MODE" \
  --first 100 \
  --max-pages-per-combo "$EXPOSURE_MAX_PAGES" \
  --workers "$EXPOSURE_WORKERS" \
  --delay "$EXPOSURE_DELAY" \
  --retry-errors \
  --save-every 25

run "additional_new_panel" \
  "$PY" rapidapi_crawl/scripts/build_additional_market_panel.py \
  --root "$WORK_ROOT" \
  --category Data

run "build_aligned_delta_tables" \
  "$PY" rapidapi_crawl/scripts/build_weekly_incremental_delta.py build \
  --work-root "$WORK_ROOT" \
  --merged-dir rapidapi_crawl/data_merged \
  --history-dir rapidapi_crawl/data_incremental \
  --out-dir "$OUT_DIR" \
  --run-id "$RUN_ID"

rm -rf "$WORK_ROOT"
status "complete" "complete" "weekly incremental delta tables written to $OUT_DIR"
echo "===== $(ts) COMPLETE weekly incremental update ====="

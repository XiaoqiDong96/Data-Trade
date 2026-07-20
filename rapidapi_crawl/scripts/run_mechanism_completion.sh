#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PY="${PY:-$ROOT/.venv/bin/python}"
RUN_ID="${RUN_ID:?RUN_ID is required}"
LOG_DIR="$ROOT/logs/rapidapi_mechanism_completion"
LOG_FILE="$LOG_DIR/run_${RUN_ID}.log"
STATUS_FILE="$LOG_DIR/status_${RUN_ID}.json"
MANIFEST="$ROOT/rapidapi_crawl/data/rapidapi_mechanism_collection_manifest.json"
WORKERS="${WORKERS:-3}"
DELAY="${DELAY:-0.80}"

mkdir -p "$LOG_DIR"
exec >> "$LOG_FILE" 2>&1

status() {
  local state="$1"
  local message="$2"
  "$PY" - "$STATUS_FILE" "$RUN_ID" "$state" "$message" <<'PY'
import json
import sys
from datetime import datetime, timezone

path, run_id, state, message = sys.argv[1:5]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "run_id": run_id,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "state": state,
            "message": message,
        },
        handle,
        ensure_ascii=False,
        indent=2,
    )
PY
}

on_error() {
  local code=$?
  status "failed" "exit=$code"
  exit "$code"
}
trap on_error ERR

status "running" "workers=$WORKERS delay=$DELAY"
"$PY" rapidapi_crawl/scripts/rapidapi_additional_market_data.py \
  --root rapidapi_crawl \
  --category Data \
  --kinds healthcheck,detail_extras \
  --workers "$WORKERS" \
  --delay "$DELAY" \
  --retry-errors

"$PY" - "$MANIFEST" <<'PY'
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

manifest_path = Path(sys.argv[1])
master = pd.read_csv("rapidapi_crawl/data_merged/rapidapi_merged_api_master.csv", usecols=["api_id"])
target_ids = set(master["api_id"].dropna().astype(str))
raw_dir = Path("rapidapi_crawl/raw/graphql/additional_Data/mechanisms")
counts = Counter()
error_types = Counter()
terminal_records = []
seen = set()
for path in raw_dir.glob("*.json"):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        counts["read_error"] += 1
        error_types[type(exc).__name__] += 1
        continue
    api_id = str(payload.get("__api_id") or path.stem)
    if api_id not in target_ids:
        counts["out_of_scope_cache"] += 1
        continue
    seen.add(api_id)
    if payload.get("__terminal_detail_error") and not payload.get("__error__"):
        counts["terminal_detail_not_found"] += 1
        terminal_records.append(
            {
                "api_id": api_id,
                "reason": str(payload["__terminal_detail_error"])[:500],
                "lookup_mode": payload.get("__lookup_mode"),
            }
        )
    elif payload.get("__error__"):
        counts["error"] += 1
        message = str(payload["__error__"])
        if "429" in message:
            error_types["http_429"] += 1
        elif "missing owner_slug or api_slug" in message:
            error_types["missing_slug"] += 1
        elif "timed out" in message.lower() or "timeout" in message.lower():
            error_types["timeout"] += 1
        else:
            error_types[message[:180]] += 1
        continue
    data = payload.get("data") or {}
    health = data.get("healthcheckAnalytics")
    detail = data.get("apiBySlugifiedNameAndOwnerName")
    counts["detail_response"] += int(bool(detail))
    counts["health_response"] += int(health is not None)
    counts["fetched_without_detail"] += int(not detail)
counts["target"] = len(target_ids)
counts["cached_target"] = len(seen)
counts["missing_cache"] = len(target_ids - seen)
manifest = {
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "endpoint": "public RapidAPI GraphQL getApiStaticMechanisms",
    "target_api_count": len(target_ids),
    "counts": dict(counts),
    "error_types": dict(error_types),
    "terminal_records": terminal_records,
    "reachable_detail_coverage_rate": (
        counts["detail_response"] / max(1, len(target_ids) - counts["terminal_detail_not_found"])
    ),
    "pending_errors": counts["error"] + counts["read_error"] + counts["missing_cache"],
}
manifest_path.parent.mkdir(parents=True, exist_ok=True)
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(manifest, ensure_ascii=False, indent=2))
if manifest["pending_errors"]:
    raise SystemExit(75)
PY

"$PY" rapidapi_crawl/scripts/refresh_mechanism_baseline.py --root rapidapi_crawl
"$PY" rapidapi_crawl/scripts/build_data_handoff_docs.py

status "complete" "all reachable mechanism requests completed; terminal detail records retained in manifest"

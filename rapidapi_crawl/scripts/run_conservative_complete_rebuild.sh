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
LOG_DIR="$ROOT/logs/rapidapi_full_rebuild"
LOG_FILE="$LOG_DIR/run_${RUN_ID}.log"
STATUS_FILE="$LOG_DIR/status_${RUN_ID}.json"
LATEST_FILE="$LOG_DIR/latest_run"
PID_FILE="$LOG_DIR/pipeline_${RUN_ID}.pid"

STATIC_WORKERS="${STATIC_WORKERS:-3}"
STATIC_DELAY="${STATIC_DELAY:-0.70}"
ADDITIONAL_WORKERS="${ADDITIONAL_WORKERS:-3}"
ADDITIONAL_DELAY="${ADDITIONAL_DELAY:-0.70}"
SEARCH_WORKERS="${SEARCH_WORKERS:-3}"
SEARCH_DELAY="${SEARCH_DELAY:-0.70}"
SEARCH_MAX_PAGES="${SEARCH_MAX_PAGES:-10}"

mkdir -p "$LOG_DIR" rapidapi_crawl/data rapidapi_crawl/data_merged
printf '%s\n' "$RUN_ID" > "$LATEST_FILE"
printf '%s\n' "$$" > "$PID_FILE"

exec >> "$LOG_FILE" 2>&1

ts() {
  date '+%Y-%m-%dT%H:%M:%S%z'
}

json_status() {
  local step="$1"
  local state="$2"
  local message="${3:-}"
  "$PY" - "$STATUS_FILE" "$RUN_ID" "$step" "$state" "$message" <<'PY'
import json
import sys
from datetime import datetime, timezone

path, run_id, step, state, message = sys.argv[1:6]
payload = {
    "run_id": run_id,
    "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    "step": step,
    "state": state,
    "message": message,
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
PY
}

run_required() {
  local step="$1"
  shift
  echo
  echo "===== $(ts) START required: $step ====="
  json_status "$step" "running" "$*"
  "$@"
  local code=$?
  if [[ "$code" -ne 0 ]]; then
    echo "===== $(ts) FAILED required: $step exit=$code ====="
    json_status "$step" "failed" "exit=$code"
    exit "$code"
  fi
  echo "===== $(ts) DONE required: $step ====="
  json_status "$step" "done" ""
}

run_optional() {
  local step="$1"
  shift
  echo
  echo "===== $(ts) START optional: $step ====="
  json_status "$step" "running_optional" "$*"
  if "$@"; then
    echo "===== $(ts) DONE optional: $step ====="
    json_status "$step" "done_optional" ""
  else
    local code=$?
    echo "===== $(ts) WARNING optional failed: $step exit=$code ====="
    json_status "$step" "warning_optional_failed" "exit=$code"
  fi
}

on_error() {
  local code=$?
  local line=${BASH_LINENO[0]:-unknown}
  echo "===== $(ts) ERROR line=$line exit=$code ====="
  json_status "error" "failed" "line=$line exit=$code"
  exit "$code"
}

trap on_error ERR

echo "RapidAPI conservative complete rebuild"
echo "root=$ROOT"
echo "run_id=$RUN_ID"
echo "log=$LOG_FILE"
echo "status=$STATUS_FILE"
echo "pid=$$"
echo "static workers/delay=$STATIC_WORKERS/$STATIC_DELAY"
echo "additional workers/delay=$ADDITIONAL_WORKERS/$ADDITIONAL_DELAY"
echo "search workers/delay/max_pages=$SEARCH_WORKERS/$SEARCH_DELAY/$SEARCH_MAX_PAGES"

json_status "started" "running" "pipeline initialized"

run_required "offline_detail_normalize" \
  "$PY" rapidapi_crawl/scripts/rapidapi_crawler.py \
  --root rapidapi_crawl \
  --category Data \
  --skip-search \
  --details \
  --details-source discovery \
  --details-limit 0 \
  --details-offline-only

run_required "base_plan_panel" \
  "$PY" rapidapi_crawl/scripts/build_rapidapi_panel.py \
  --root rapidapi_crawl \
  --category Data

run_required "static_enrichment_playground_billing_owner" \
  "$PY" rapidapi_crawl/scripts/rapidapi_static_enrichment.py \
  --root rapidapi_crawl \
  --category Data \
  --kinds playground,billing_endpoints,owner \
  --workers "$STATIC_WORKERS" \
  --delay "$STATIC_DELAY" \
  --retry-errors

run_required "static_enriched_panel" \
  "$PY" rapidapi_crawl/scripts/build_static_enriched_panel.py \
  --root rapidapi_crawl \
  --category Data

run_required "additional_health_restrictions_spotlights" \
  "$PY" rapidapi_crawl/scripts/rapidapi_additional_market_data.py \
  --root rapidapi_crawl \
  --category Data \
  --kinds healthcheck,detail_extras \
  --workers "$ADDITIONAL_WORKERS" \
  --delay "$ADDITIONAL_DELAY" \
  --retry-errors

run_required "build_search_terms" \
  "$PY" - <<'PY'
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pandas as pd

root = Path("rapidapi_crawl/data")
root.mkdir(parents=True, exist_ok=True)

frames = []
for name in [
    "rapidapi_discovery_Data_apis.csv",
    "rapidapi_details_Data_apis.csv",
    "rapidapi_static_Data_api_enriched.csv",
]:
    path = root / name
    if path.exists() and path.stat().st_size:
        frames.append(pd.read_csv(path, dtype=str, low_memory=False))

if not frames:
    raise SystemExit("No API text tables found for search term construction.")

df = pd.concat(frames, ignore_index=True, sort=False).fillna("")
text_cols = [
    c for c in [
        "name", "title", "slugifiedName", "description",
        "api_name", "api_title", "api_slug", "api_description",
        "owner_slugifiedName", "owner_slug", "categoryName", "category",
    ] if c in df.columns
]
texts = []
for col in text_cols:
    texts.extend(df[col].astype(str).str.lower().tolist())

stop = {
    "api", "apis", "data", "the", "and", "for", "with", "from", "your", "free",
    "best", "new", "test", "simple", "service", "public", "rapidapi", "default",
}
token_counts: Counter[str] = Counter()
phrase_counts: Counter[str] = Counter()
domain_seed = [
    "scraper", "search", "linkedin", "twitter", "youtube", "instagram", "tiktok",
    "google", "amazon", "email", "phone", "profile", "company", "people", "lead",
    "news", "finance", "stock", "crypto", "weather", "sports", "real estate",
    "location", "address", "ip", "domain", "web", "review", "product", "job",
    "vehicle", "music", "movie", "maps", "places", "price", "restaurant",
    "business", "contact", "enrichment", "whois", "ocr", "pdf", "document",
    "text", "sentiment", "property", "airbnb", "hotel", "flight", "country",
    "city", "zipcode", "postcode", "identity", "verification", "market data",
    "social", "ecommerce", "database", "extractor", "crawler",
]

for text in texts:
    words = [
        w for w in re.findall(r"[a-z][a-z0-9]{2,}", text.replace("-", " "))
        if w not in stop and not w.isdigit()
    ]
    token_counts.update(words)
    phrase_counts.update(
        " ".join(pair)
        for pair in zip(words, words[1:])
        if all(len(x) >= 3 and x not in stop for x in pair)
    )

def clean_terms(values):
    out, seen = [], set()
    for value in values:
        term = re.sub(r"\s+", " ", str(value).strip().lower())
        if not term or term in seen or len(term) > 45:
            continue
        seen.add(term)
        out.append(term)
    return out

common = clean_terms(domain_seed + [t for t, _ in token_counts.most_common(80)])[:90]
extra = clean_terms([t for t, _ in phrase_counts.most_common(120)])[:90]
tail_candidates = [
    t for t, n in token_counts.items()
    if 2 <= n <= 12 and t not in set(common) and len(t) >= 4
]
tail = clean_terms(sorted(tail_candidates))[:70]

(root / "rapidapi_common_terms.txt").write_text("\n".join(common) + "\n", encoding="utf-8")
(root / "rapidapi_extra_terms.txt").write_text("\n".join(extra) + "\n", encoding="utf-8")
(root / "rapidapi_tail_terms.txt").write_text("\n".join(tail) + "\n", encoding="utf-8")

print({
    "common_terms": len(common),
    "extra_terms": len(extra),
    "tail_terms": len(tail),
    "total_file_terms": len(set(common + extra + tail)),
})
PY

run_required "search_exposure_comprehensive" \
  "$PY" rapidapi_crawl/scripts/rapidapi_search_exposure_crawler.py \
  --root rapidapi_crawl \
  --category Data \
  --terms-mode comprehensive \
  --first 100 \
  --max-pages-per-combo "$SEARCH_MAX_PAGES" \
  --workers "$SEARCH_WORKERS" \
  --delay "$SEARCH_DELAY" \
  --retry-errors \
  --save-every 25

run_required "additional_market_panel" \
  "$PY" rapidapi_crawl/scripts/build_additional_market_panel.py \
  --root rapidapi_crawl \
  --category Data

run_required "raw_variable_dictionary" \
  "$PY" rapidapi_crawl/scripts/build_raw_variable_dictionary.py

run_optional "article_model_report" \
  "$PY" rapidapi_io_static/scripts/build_data_commodity_io_article.py

run_required "consolidated_tables" \
  "$PY" rapidapi_crawl/scripts/build_consolidated_tables.py \
  --root .

run_required "validate_merged_tables" \
  "$PY" - <<'PY'
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

out = Path("rapidapi_crawl/data_merged")
checks = {
    "rapidapi_merged_api_master.csv": ["api_id"],
    "rapidapi_merged_plan_contracts.csv": ["api_id", "plan_id", "version_id"],
    "rapidapi_merged_endpoint_schema.csv": ["api_id", "endpoint_id"],
    "rapidapi_merged_search_exposure.csv": ["query_id", "replica_index", "search_rank", "api_id"],
    "rapidapi_merged_marketplace_listings.csv": ["listing_source", "rank", "page", "api_id"],
}
result = {}
for name, key in checks.items():
    path = out / name
    if not path.exists():
        result[name] = {"exists": False}
        continue
    df = pd.read_csv(path, dtype=str, low_memory=False)
    available_key = [c for c in key if c in df.columns]
    duplicate_rows = None
    if available_key:
        duplicate_rows = int(df.duplicated(available_key, keep=False).sum())
    result[name] = {
        "exists": True,
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "key_checked": available_key,
        "duplicate_rows_on_key": duplicate_rows,
    }

manifest = pd.DataFrame([
    {
        "table": name,
        "exists": info.get("exists"),
        "rows": info.get("rows"),
        "columns": info.get("columns"),
        "key_checked": "|".join(info.get("key_checked") or []),
        "duplicate_rows_on_key": info.get("duplicate_rows_on_key"),
    }
    for name, info in result.items()
])
manifest.to_csv(out / "rapidapi_merged_validation.csv", index=False)
(out / "rapidapi_merged_validation.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

readme = out / "README.md"
if readme.exists():
    txt = readme.read_text(encoding="utf-8")
    txt = txt.replace(
        "需要逐条复核时回到 `rapidapi_crawl/raw/graphql/`。",
        "本交付版已经清理 raw/intermediate；需要逐条复核时应重新运行可续跑抓取脚本生成 raw。",
    )
    readme.write_text(txt, encoding="utf-8")

print(json.dumps(result, ensure_ascii=False, indent=2))
PY

run_required "cleanup_intermediate_data" \
  bash -lc 'rm -rf rapidapi_crawl/raw rapidapi_crawl/data rapidapi_io_static/data && find . -name "__pycache__" -type d -prune -exec rm -rf {} + && find . -name "*.tmp" -type f -delete'

json_status "complete" "complete" "conservative complete rebuild finished"
echo
echo "===== $(ts) COMPLETE ====="
echo "Final merged data: $ROOT/rapidapi_crawl/data_merged"
echo "Article outputs: $ROOT/rapidapi_io_static/report $ROOT/rapidapi_io_static/tables $ROOT/rapidapi_io_static/figures"
echo "Log: $LOG_FILE"
echo "Status: $STATUS_FILE"

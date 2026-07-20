#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PY="${PY:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  echo "Python environment not found at $PY" >&2
  exit 2
fi

RUN_ID="${RUN_ID:-$(cat "$ROOT/logs/rapidapi_weekly_incremental/latest_run")}"
HISTORY_DIR="$ROOT/rapidapi_crawl/data_incremental"
WORK_ROOT="$HISTORY_DIR/work_strict_${RUN_ID}"
OUT_DIR="$HISTORY_DIR/${RUN_ID}"
LOG_DIR="$ROOT/logs/rapidapi_weekly_incremental"
LOG_FILE="$LOG_DIR/strict_recrawl_${RUN_ID}.log"
STATUS_FILE="$LOG_DIR/strict_status_${RUN_ID}.json"

DETAIL_WORKERS="${DETAIL_WORKERS:-3}"
DETAIL_DELAY="${DETAIL_DELAY:-0.70}"
STATIC_WORKERS="${STATIC_WORKERS:-3}"
STATIC_DELAY="${STATIC_DELAY:-0.70}"
ADDITIONAL_WORKERS="${ADDITIONAL_WORKERS:-3}"
ADDITIONAL_DELAY="${ADDITIONAL_DELAY:-0.70}"
EXPOSURE_WORKERS="${EXPOSURE_WORKERS:-3}"
EXPOSURE_DELAY="${EXPOSURE_DELAY:-0.70}"
EXPOSURE_MAX_PAGES="${EXPOSURE_MAX_PAGES:-10}"
EXTERNAL_WORKERS="${EXTERNAL_WORKERS:-2}"
EXTERNAL_DELAY="${EXTERNAL_DELAY:-0.75}"

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
  echo "===== $(ts) START strict: $step ====="
  status "$step" "running" "$*"
  "$@"
  echo "===== $(ts) DONE strict: $step ====="
}

on_error() {
  local code=$?
  local line=${BASH_LINENO[0]:-unknown}
  echo "===== $(ts) ERROR strict line=$line exit=$code ====="
  status "error" "failed" "line=$line exit=$code"
  exit "$code"
}
trap on_error ERR

echo "RapidAPI strict incremental recrawl"
echo "root=$ROOT"
echo "run_id=$RUN_ID"
echo "work_root=$WORK_ROOT"
echo "out_dir=$OUT_DIR"
echo "log=$LOG_FILE"
status "started" "running" "strict recrawl initialized"

rm -rf "$WORK_ROOT"
mkdir -p "$WORK_ROOT/data"

run "seed_new_candidates" \
  "$PY" - "$OUT_DIR" "$WORK_ROOT" <<'PY'
import shutil
import sys
from pathlib import Path

out_dir = Path(sys.argv[1])
work_root = Path(sys.argv[2])
src = out_dir / "rapidapi_weekly_new_candidates.csv"
if not src.exists():
    raise SystemExit(f"missing candidates: {src}")
dst = work_root / "data" / "rapidapi_discovery_Data_apis.csv"
dst.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(src, dst)
shutil.copy2(src, work_root / "data" / "rapidapi_discovery_Data_all_apis.csv")
PY

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

run "base_plan_panel" \
  "$PY" rapidapi_crawl/scripts/build_rapidapi_panel.py \
  --root "$WORK_ROOT" \
  --category Data

run "static_enrichment_playground_billing_owner" \
  "$PY" rapidapi_crawl/scripts/rapidapi_static_enrichment.py \
  --root "$WORK_ROOT" \
  --category Data \
  --kinds playground,billing_endpoints,owner \
  --workers "$STATIC_WORKERS" \
  --delay "$STATIC_DELAY" \
  --retry-errors

run "static_enriched_panel" \
  "$PY" rapidapi_crawl/scripts/build_static_enriched_panel.py \
  --root "$WORK_ROOT" \
  --category Data

run "additional_health_restrictions_spotlights" \
  "$PY" rapidapi_crawl/scripts/rapidapi_additional_market_data.py \
  --root "$WORK_ROOT" \
  --category Data \
  --kinds healthcheck,detail_extras \
  --workers "$ADDITIONAL_WORKERS" \
  --delay "$ADDITIONAL_DELAY" \
  --retry-errors

run "build_search_terms" \
  "$PY" - "$WORK_ROOT" <<'PY'
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

root = Path(sys.argv[1]) / "data"
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
print({"common_terms": len(common), "extra_terms": len(extra), "tail_terms": len(tail)})
PY

run "search_exposure_comprehensive" \
  "$PY" rapidapi_crawl/scripts/rapidapi_search_exposure_crawler.py \
  --root "$WORK_ROOT" \
  --category Data \
  --terms-mode comprehensive \
  --first 100 \
  --max-pages-per-combo "$EXPOSURE_MAX_PAGES" \
  --workers "$EXPOSURE_WORKERS" \
  --delay "$EXPOSURE_DELAY" \
  --retry-errors \
  --save-every 25

run "additional_market_panel" \
  "$PY" rapidapi_crawl/scripts/build_additional_market_panel.py \
  --root "$WORK_ROOT" \
  --category Data

run "build_aligned_delta_tables_full_merge_logic" \
  "$PY" rapidapi_crawl/scripts/build_weekly_incremental_delta.py build \
  --work-root "$WORK_ROOT" \
  --merged-dir rapidapi_crawl/data_merged \
  --history-dir rapidapi_crawl/data_incremental \
  --out-dir "$OUT_DIR" \
  --run-id "$RUN_ID"

run "external_enrichment_for_new_apis" \
  "$PY" rapidapi_crawl/scripts/build_external_incremental_enrichment.py \
  --root rapidapi_crawl \
  --run-dir "$OUT_DIR" \
  --run-id "$RUN_ID" \
  --workers "$EXTERNAL_WORKERS" \
  --delay "$EXTERNAL_DELAY"

run "strict_validation" \
  "$PY" - "$OUT_DIR" <<'PY'
import json
import sys
from pathlib import Path

import pandas as pd

out_dir = Path(sys.argv[1])
checks = {}
for path in sorted(list(out_dir.glob("*.csv")) + list((out_dir / "external_incremental").glob("*.csv"))):
    if not path.exists() or path.stat().st_size == 0:
        continue
    df = pd.read_csv(path, dtype=str, low_memory=False).fillna("")
    empty = [c for c in df.columns if df[c].astype(str).str.strip().eq("").all()]
    checks[str(path.relative_to(out_dir))] = {
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "all_empty_columns": empty,
        "all_empty_column_count": len(empty),
    }
manifest = {
    "strict_validated_at_utc": pd.Timestamp.utcnow().isoformat(),
    "checks": checks,
    "files_with_all_empty_columns": {
        name: info for name, info in checks.items() if info["all_empty_column_count"]
    },
}
(out_dir / "rapidapi_weekly_strict_recrawl_validation.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(json.dumps(manifest, ensure_ascii=False, indent=2))
PY

run "promote_validated_delta" \
  "$PY" rapidapi_crawl/scripts/promote_incremental_to_baseline.py \
  --root rapidapi_crawl \
  --run-dir "$OUT_DIR"

run "refresh_data_handoff_docs" \
  "$PY" rapidapi_crawl/scripts/build_data_handoff_docs.py

status "complete" "complete" "strict recrawl finished"
echo "===== $(ts) COMPLETE strict recrawl ====="

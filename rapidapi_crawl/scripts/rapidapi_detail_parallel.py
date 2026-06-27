#!/usr/bin/env python3
"""Fetch missing RapidAPI detail JSON files in parallel.

This complements rapidapi_crawler.py. It only fills raw detail JSON files, then
rapidapi_crawler.py can be run with --details-delay 0 to normalize all cached
raw responses into CSV tables.
"""

from __future__ import annotations

import argparse
import csv
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from rapidapi_crawler import BASE, DETAIL_QUERY, RapidApiClient, read_json, safe_name


thread_state = threading.local()


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def detail_raw_path(raw_dir: Path, idx: int, row: dict[str, str]) -> Path:
    owner = row.get("owner_slugifiedName") or row.get("owner_username") or ""
    slug = row.get("slugifiedName") or ""
    return raw_dir / f"{idx:05d}_{safe_name(str(owner))}__{safe_name(str(slug))}.json"


def has_valid_api(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        data = read_json(path)
    except Exception:
        return False
    return bool((data.get("data") or {}).get("apiBySlugifiedNameAndOwnerName"))


def client_for_thread(category: str) -> RapidApiClient:
    client = getattr(thread_state, "client", None)
    if client is None:
        client = RapidApiClient(category)
        client.init()
        thread_state.client = client
    return client


def fetch_one(
    category: str,
    raw_dir: Path,
    idx: int,
    row: dict[str, str],
    delay: float,
) -> tuple[int, bool, str]:
    owner = row.get("owner_slugifiedName") or row.get("owner_username") or ""
    slug = row.get("slugifiedName") or ""
    raw_path = detail_raw_path(raw_dir, idx, row)
    if not owner or not slug:
        data = {"errors": [{"message": "missing owner or slug"}], "variables": {"apiOwnerSlug": owner, "apiSlug": slug}}
        atomic_write_json(raw_path, data)
        return idx, False, "missing owner/slug"

    referer = f"{BASE}/search/{category}?sortBy=ByRelevance"
    variables = {"apiOwnerSlug": owner, "apiSlug": slug}
    try:
        data = client_for_thread(category).graphql(DETAIL_QUERY, variables, "getApiBySlugAndOwner", referer)
        ok = bool((data.get("data") or {}).get("apiBySlugifiedNameAndOwnerName"))
        if not ok:
            data = {"errors": [{"message": "empty api response"}], "variables": variables, "response": data}
        atomic_write_json(raw_path, data)
        if delay:
            time.sleep(delay)
        return idx, ok, "ok" if ok else "empty"
    except Exception as exc:
        atomic_write_json(raw_path, {"errors": [{"message": str(exc)}], "variables": variables})
        if delay:
            time.sleep(delay)
        return idx, False, str(exc)[:200]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="rapidapi_crawl")
    parser.add_argument("--category", default="Data")
    parser.add_argument("--source", default="discovery", choices=["discovery", "search"])
    parser.add_argument("--source-csv", help="Optional custom CSV with owner_slugifiedName/owner_username and slugifiedName columns.")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--delay", type=float, default=0.03, help="Per-worker delay after each network request.")
    parser.add_argument("--retry-errors", action="store_true", help="Refetch existing raw files without a valid API payload.")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of missing targets to fetch; 0 means all.")
    args = parser.parse_args()

    root = Path(args.root)
    suffix = safe_name(args.category)
    source_csv = Path(args.source_csv) if args.source_csv else root / "data" / f"rapidapi_{args.source}_{suffix}_apis.csv"
    raw_dir = root / "raw" / "graphql" / f"details_{suffix}"
    rows = load_rows(source_csv)

    targets: list[tuple[int, dict[str, str]]] = []
    for idx, row in enumerate(rows, 1):
        raw_path = detail_raw_path(raw_dir, idx, row)
        if args.retry_errors:
            needed = not has_valid_api(raw_path)
        else:
            needed = not raw_path.exists()
        if needed:
            targets.append((idx, row))

    if args.limit:
        targets = targets[: args.limit]

    print(
        json.dumps(
            {
                "source_rows": len(rows),
                "targets": len(targets),
                "workers": args.workers,
                "retry_errors": args.retry_errors,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if not targets:
        return

    done = 0
    ok_count = 0
    fail_count = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(fetch_one, args.category, raw_dir, idx, row, args.delay)
            for idx, row in targets
        ]
        for future in as_completed(futures):
            idx, ok, message = future.result()
            done += 1
            ok_count += int(ok)
            fail_count += int(not ok)
            if done % 50 == 0 or done == len(targets):
                print(
                    f"detail-raw {done}/{len(targets)} ok={ok_count} failed={fail_count} last_idx={idx} msg={message}",
                    flush=True,
                )


if __name__ == "__main__":
    main()

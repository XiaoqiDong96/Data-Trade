#!/usr/bin/env python3
"""Recover RapidAPI detail records from public API detail HTML pages.

The detail page embeds a Next.js Flight/react-query dehydrated state that often
contains the same Api object returned by getApiBySlugAndOwner. This script
parses that embedded object and writes the same raw JSON envelope used by
rapidapi_crawler.py, so the existing normalizer can consume it.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

from rapidapi_crawler import BASE, read_json, safe_name


UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


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


def should_fetch(path: Path, retry_errors: bool, retry_no_api: bool) -> bool:
    if has_valid_api(path):
        return False
    if not path.exists():
        return True
    if not retry_errors:
        return False
    try:
        data = read_json(path)
    except Exception:
        return True
    message = ""
    if data.get("errors"):
        message = str(data["errors"][0].get("message", ""))
    if message == "html_no_api_payload" and not retry_no_api:
        return False
    return True


def walk_json(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if value.get("__typename") == "Api" and value.get("id") and value.get("slugifiedName"):
            found.append(value)
        for child in value.values():
            found.extend(walk_json(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(walk_json(child))
    return found


def parse_next_flight_api(html: str, expected_slug: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    marker = "self.__next_f.push("
    idx = 0
    while True:
        pos = html.find(marker, idx)
        if pos < 0:
            return None
        start = pos + len(marker)
        try:
            payload, end = decoder.raw_decode(html[start:])
        except json.JSONDecodeError:
            idx = start + 1
            continue
        idx = start + end
        if not (isinstance(payload, list) and len(payload) > 1 and isinstance(payload[1], str)):
            continue
        chunk = payload[1]
        if '"__typename":"Api"' not in chunk and '"billingPlans"' not in chunk:
            continue
        for line in chunk.splitlines():
            if '"__typename":"Api"' not in line:
                continue
            _, sep, json_payload = line.partition(":")
            if not sep:
                continue
            try:
                parsed = json.loads(json_payload)
            except json.JSONDecodeError:
                continue
            candidates = walk_json(parsed)
            for api in candidates:
                if api.get("slugifiedName") == expected_slug:
                    return api
            if candidates:
                return candidates[0]
    return None


def fetch_html_detail(root: Path, idx: int, row: dict[str, str], delay: float, timeout: int) -> tuple[int, bool, str]:
    raw_dir = root / "raw" / "graphql" / "details_Data"
    html_dir = root / "raw" / "html_details_Data"
    raw_path = detail_raw_path(raw_dir, idx, row)
    owner = row.get("owner_slugifiedName") or row.get("owner_username") or ""
    slug = row.get("slugifiedName") or ""
    if not owner or not slug:
        return idx, False, "missing owner/slug"

    url = f"{BASE}/{owner}/api/{slug}"
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    try:
        response = session.get(url, timeout=timeout)
        status = response.status_code
        if status == 429:
            return idx, False, "429"
        if status >= 500:
            return idx, False, f"http_{status}"
        html = response.text
        html_path = html_dir / f"{idx:05d}_{safe_name(str(owner))}__{safe_name(str(slug))}.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html, encoding="utf-8")

        api = parse_next_flight_api(html, slug)
        if not api:
            data = {
                "errors": [{"message": "html_no_api_payload"}],
                "source": "detail_html",
                "url": url,
                "http_status": status,
                "variables": {"apiOwnerSlug": owner, "apiSlug": slug},
            }
            atomic_write_json(raw_path, data)
            return idx, False, "html_no_api"

        data = {
            "data": {"apiBySlugifiedNameAndOwnerName": api},
            "source": "detail_html",
            "url": url,
            "http_status": status,
            "variables": {"apiOwnerSlug": owner, "apiSlug": slug},
        }
        atomic_write_json(raw_path, data)
        if delay:
            time.sleep(delay + random.uniform(0, delay * 0.25))
        return idx, True, "ok"
    except Exception as exc:
        return idx, False, str(exc)[:180]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="rapidapi_crawl")
    parser.add_argument("--category", default="Data")
    parser.add_argument("--source", default="discovery", choices=["discovery", "search"])
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--retry-no-api", action="store_true", help="Retry pages already classified as html_no_api_payload.")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    root = Path(args.root)
    suffix = safe_name(args.category)
    source_csv = root / "data" / f"rapidapi_{args.source}_{suffix}_apis.csv"
    raw_dir = root / "raw" / "graphql" / f"details_{suffix}"
    rows = load_rows(source_csv)

    targets: list[tuple[int, dict[str, str]]] = []
    for idx, row in enumerate(rows, 1):
        raw_path = detail_raw_path(raw_dir, idx, row)
        if should_fetch(raw_path, args.retry_errors, args.retry_no_api):
            targets.append((idx, row))
    if args.limit:
        targets = targets[: args.limit]

    print(
        json.dumps(
            {"source_rows": len(rows), "targets": len(targets), "workers": args.workers, "retry_errors": args.retry_errors},
            ensure_ascii=False,
        ),
        flush=True,
    )
    if not targets:
        return

    done = ok_count = fail_count = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(fetch_html_detail, root, idx, row, args.delay, args.timeout)
            for idx, row in targets
        ]
        for future in as_completed(futures):
            idx, ok, msg = future.result()
            done += 1
            ok_count += int(ok)
            fail_count += int(not ok)
            if msg == "429":
                print(f"detail-html hit 429 at idx={idx}; stop and retry later", flush=True)
                executor.shutdown(wait=False, cancel_futures=True)
                break
            if done % 25 == 0 or done == len(targets):
                print(f"detail-html {done}/{len(targets)} ok={ok_count} failed={fail_count} last_idx={idx} msg={msg}", flush=True)


if __name__ == "__main__":
    main()

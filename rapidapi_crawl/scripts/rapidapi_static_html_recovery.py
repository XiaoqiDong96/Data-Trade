#!/usr/bin/env python3
"""Recover static API-version endpoint metadata from public RapidAPI HTML pages.

RapidAPI detail pages often embed a dehydrated React Query / Next Flight state
that contains the current apiVersion and endpoint list. This script uses that
public HTML as a fallback when `/gateway/graphql` is rate-limited.
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


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def raw_path_for(root: Path, row: dict[str, str]) -> Path:
    return (
        root
        / "raw"
        / "graphql"
        / "static_Data"
        / "playground"
        / f"{safe_name(row.get('owner_slug') or '')}__{safe_name(row.get('api_slug') or '')}.json"
    )


def has_valid_playground(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return bool((read_json(path).get("data") or {}).get("apiVersion"))
    except Exception:
        return False


def walk_versions(value: Any, expected_version_id: str | None) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        value_id = value.get("id")
        has_endpoint_list = isinstance(value.get("endpoints"), list)
        looks_like_version = isinstance(value_id, str) and value_id.startswith("apiversion_")
        if has_endpoint_list and (value_id == expected_version_id or looks_like_version):
            found.append(value)
        for child in value.values():
            found.extend(walk_versions(child, expected_version_id))
    elif isinstance(value, list):
        for child in value:
            found.extend(walk_versions(child, expected_version_id))
    return found


def parse_versions_from_html(html: str, expected_version_id: str | None) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    marker = "self.__next_f.push("
    idx = 0
    candidates: list[dict[str, Any]] = []
    while True:
        pos = html.find(marker, idx)
        if pos < 0:
            break
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
        if "apiendpoint_" not in chunk and '"endpoints"' not in chunk:
            continue
        for line in chunk.splitlines():
            _, sep, json_payload = line.partition(":")
            if not sep:
                continue
            try:
                parsed = json.loads(json_payload)
            except json.JSONDecodeError:
                continue
            candidates.extend(walk_versions(parsed, expected_version_id))
    if expected_version_id:
        for candidate in candidates:
            if candidate.get("id") == expected_version_id:
                return candidate
    return candidates[0] if candidates else None


def normalize_version(version: dict[str, Any]) -> dict[str, Any]:
    """Fill fields expected by rapidapi_static_enrichment.normalize_playground."""
    out = dict(version)
    out.setdefault("assets", [])
    out.setdefault("targetGroup", None)
    out.setdefault("groups", [])
    out.setdefault("publicdns", [])
    out.setdefault("accessControl", {})
    out.setdefault("payloads", [])
    for endpoint in out.get("endpoints") or []:
        endpoint.setdefault("createdAt", None)
        endpoint.setdefault("externalDocs", {})
        endpoint.setdefault("params", None)
        endpoint.setdefault("requestPayloads", [])
        endpoint.setdefault("responsePayloads", [])
    return out


def fetch_one(root: Path, row: dict[str, str], delay: float, timeout: int) -> tuple[str, bool, str]:
    raw_path = raw_path_for(root, row)
    if has_valid_playground(raw_path):
        return str(raw_path), True, "already_ok"
    owner = row.get("owner_slug") or ""
    slug = row.get("api_slug") or ""
    version_id = row.get("version_id") or None
    if not owner or not slug:
        atomic_write_json(raw_path, {"errors": [{"message": "missing owner/slug"}], "source": "static_html"})
        return str(raw_path), False, "missing owner/slug"

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
        if response.status_code == 429:
            return str(raw_path), False, "429"
        if response.status_code >= 500:
            return str(raw_path), False, f"http_{response.status_code}"
        html = response.text
        html_dir = root / "raw" / "html_static_Data"
        html_path = html_dir / f"{safe_name(owner)}__{safe_name(slug)}.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html, encoding="utf-8")
        version = parse_versions_from_html(html, version_id)
        if not version:
            atomic_write_json(
                raw_path,
                {
                    "errors": [{"message": "html_no_api_version_payload"}],
                    "source": "static_html",
                    "url": url,
                    "http_status": response.status_code,
                    "variables": {"apiVersionId": version_id},
                },
            )
            return str(raw_path), False, "html_no_version"
        atomic_write_json(
            raw_path,
            {
                "data": {"apiVersion": normalize_version(version)},
                "source": "static_html",
                "url": url,
                "http_status": response.status_code,
                "variables": {"apiVersionId": version_id},
            },
        )
        if delay:
            time.sleep(delay + random.uniform(0, delay * 0.25))
        return str(raw_path), True, "ok"
    except Exception as exc:
        atomic_write_json(raw_path, {"errors": [{"message": str(exc)}], "source": "static_html", "variables": {"apiVersionId": version_id}})
        return str(raw_path), False, str(exc)[:180]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="rapidapi_crawl")
    parser.add_argument("--missing-csv", default="rapidapi_crawl/data/rapidapi_static_Data_missing_playground.csv")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    root = Path(args.root)
    rows = load_rows(Path(args.missing_csv))
    rows = [row for row in rows if not has_valid_playground(raw_path_for(root, row))]
    if args.limit:
        rows = rows[: args.limit]
    print(json.dumps({"targets": len(rows), "workers": args.workers, "delay": args.delay}, ensure_ascii=False), flush=True)
    if not rows:
        return

    done = ok_count = fail_count = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(fetch_one, root, row, args.delay, args.timeout) for row in rows]
        for future in as_completed(futures):
            path, ok, msg = future.result()
            done += 1
            ok_count += int(ok)
            fail_count += int(not ok)
            if msg == "429":
                print(f"static-html hit 429 at {Path(path).name}; stop and retry later", flush=True)
                executor.shutdown(wait=False, cancel_futures=True)
                break
            if done % 25 == 0 or done == len(rows):
                print(f"static-html {done}/{len(rows)} ok={ok_count} failed={fail_count} last={Path(path).name} msg={msg}", flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Probe RapidAPI/Nokia API Hub public data surfaces.

This script is intentionally conservative:
- public pages only
- no login, no credential use
- raw responses cached before parsing
- slow, bounded requests

It has three jobs:
1. cache the search page and all Next.js chunks it references;
2. extract categories and visible tag definitions from the server HTML;
3. locate generated frontend operation names and likely GraphQL/API surfaces.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote
from urllib.request import Request, urlopen


BASE = "https://rapidapi.com"
SEARCH_URL = f"{BASE}/search/data"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)


@dataclass
class FetchResult:
    url: str
    status: int
    body: bytes


def fetch(url: str, timeout: int = 30) -> FetchResult:
    req = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        return FetchResult(url=url, status=getattr(resp, "status", 200), body=resp.read())


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def decode_next_strings(raw: str) -> str:
    """Best-effort decode for text embedded in Next.js RSC script pushes."""
    text = html.unescape(raw)
    text = text.replace('\\"', '"').replace("\\n", "\n").replace("\\u0026", "&")
    return text


def extract_js_files(page_html: str) -> list[str]:
    files = sorted(set(re.findall(r'src="(/hub/_next/static/chunks/[^"]+\.js)"', page_html)))
    return files


def extract_balanced_objects(text: str, marker: str) -> list[dict]:
    """Extract JSON-ish objects following a marker by brace balancing.

    The Next RSC payload embeds real JSON object literals inside escaped strings.
    This function starts near each marker and tries to decode the nearest object.
    """
    out: list[dict] = []
    for m in re.finditer(re.escape(marker), text):
        start = text.rfind("{", 0, m.start())
        if start < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        end = None
        for i in range(start, min(len(text), start + 20000)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            continue
        candidate = text[start:end]
        try:
            obj = json.loads(candidate)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def extract_categories(page_html: str) -> list[dict]:
    text = decode_next_strings(page_html)
    cats: dict[str, dict] = {}
    for obj in extract_balanced_objects(text, '"slugifiedName"'):
        if {"id", "name", "slugifiedName"}.issubset(obj):
            if str(obj.get("id", "")).startswith("category_"):
                cats[obj["id"]] = {
                    "id": obj.get("id"),
                    "name": obj.get("name"),
                    "slugifiedName": obj.get("slugifiedName"),
                    "weight": obj.get("weight"),
                    "shortDescription": obj.get("shortDescription"),
                    "thumbnail": obj.get("thumbnail"),
                }
    return sorted(cats.values(), key=lambda x: (x.get("weight") is None, x.get("weight") or 9999, x.get("name") or ""))


def extract_tag_definitions(page_html: str) -> list[dict]:
    text = decode_next_strings(page_html)
    tags: dict[str, dict] = {}
    for obj in extract_balanced_objects(text, '"tagdefinition_'):
        if str(obj.get("id", "")).startswith("tagdefinition_"):
            tags[obj["id"]] = {
                "id": obj.get("id"),
                "name": obj.get("name"),
                "values": "|".join(map(str, obj.get("values") or [])),
                "description": obj.get("description"),
                "status": obj.get("status"),
                "isVisible": obj.get("isVisible"),
                "showTagName": obj.get("showTagName"),
            }
    return sorted(tags.values(), key=lambda x: x.get("name") or "")


def save_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def cache_chunks(root: Path, js_files: Iterable[str], delay: float) -> list[Path]:
    paths: list[Path] = []
    for idx, js_path in enumerate(js_files, 1):
        filename = unquote(js_path.rsplit("/", 1)[-1])
        out = root / "raw" / "js" / filename
        paths.append(out)
        if out.exists() and out.stat().st_size > 0:
            continue
        url = BASE + js_path
        try:
            res = fetch(url)
            write_bytes(out, res.body)
            print(f"cached chunk {idx}: {filename} ({len(res.body)} bytes)")
        except Exception as exc:
            print(f"failed chunk {idx}: {url}: {exc}")
        time.sleep(delay)
    return paths


def scan_chunks(paths: Iterable[Path]) -> list[dict]:
    patterns = [
        "searchApis",
        "getApiBySlugAndOwner",
        "apiBillingPlans",
        "apiBySlugifiedNameAndOwnerName",
        "popularityScore",
        "avgLatency",
        "avgServiceLevel",
        "GraphQLClient",
        "graphql",
        "operationName",
        "/api/",
    ]
    rows: list[dict] = []
    for path in paths:
        try:
            s = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat in patterns:
            count = len(re.findall(re.escape(pat), s, flags=re.I))
            if count:
                rows.append({"file": path.name, "pattern": pat, "count": count})
    return rows


def write_context_hits(root: Path, paths: Iterable[Path]) -> None:
    pats = [
        "searchApis",
        "getApiBySlugAndOwner",
        "apiBillingPlans",
        "apiBySlugifiedNameAndOwnerName",
        "popularityScore",
        "avgLatency",
        "avgServiceLevel",
        "GraphQLClient",
    ]
    lines: list[str] = []
    for path in paths:
        s = path.read_text(encoding="utf-8", errors="ignore")
        for pat in pats:
            for m in re.finditer(re.escape(pat), s, flags=re.I):
                snippet = s[max(0, m.start() - 700) : m.start() + 1700]
                lines.append(f"\n\n### {path.name} :: {pat}\n{snippet}")
                break
    write_text(root / "data" / "chunk_context_hits.txt", "\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="rapidapi_crawl")
    ap.add_argument("--delay", type=float, default=0.2)
    args = ap.parse_args()
    root = Path(args.root)

    res = fetch(SEARCH_URL)
    page_html = res.body.decode("utf-8", "ignore")
    write_text(root / "raw" / "search_data.html", page_html)
    print(f"cached search page: {len(page_html)} chars")

    categories = extract_categories(page_html)
    tags = extract_tag_definitions(page_html)
    save_csv(root / "data" / "rapidapi_categories.csv", categories)
    save_csv(root / "data" / "rapidapi_tag_definitions.csv", tags)
    write_text(root / "data" / "rapidapi_categories.json", json.dumps(categories, ensure_ascii=False, indent=2))
    write_text(root / "data" / "rapidapi_tag_definitions.json", json.dumps(tags, ensure_ascii=False, indent=2))
    print(f"parsed categories={len(categories)}, tag_definitions={len(tags)}")

    js_files = extract_js_files(page_html)
    write_text(root / "data" / "js_files.txt", "\n".join(js_files) + "\n")
    print(f"found js_files={len(js_files)}")
    paths = cache_chunks(root, js_files, args.delay)

    hits = scan_chunks(paths)
    save_csv(root / "data" / "chunk_pattern_hits.csv", hits)
    write_context_hits(root, paths)
    print(f"chunk pattern hit rows={len(hits)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Broaden RapidAPI search coverage beyond the 1,000-result window.

RapidAPI's search endpoint reports full totals but stops pagination around the
first 1,000 hits for a single query. This script partitions discovery by search
term and sort field, then unions API ids across windows.
"""

from __future__ import annotations

import argparse
import csv
import json
import string
import time
from pathlib import Path
from typing import Any

from rapidapi_crawler import RapidApiClient, SEARCH_QUERY, flatten_api, read_json, safe_name, save_csv, write_json


SORTS = ["ByRelevance", "ByUpdatedAt", "ByAlphabetical"]

COMMON_TERMS = [
    "api",
    "data",
    "scraper",
    "search",
    "linkedin",
    "twitter",
    "x",
    "youtube",
    "instagram",
    "tiktok",
    "google",
    "amazon",
    "email",
    "phone",
    "profile",
    "company",
    "people",
    "lead",
    "sales",
    "news",
    "finance",
    "stock",
    "crypto",
    "weather",
    "sports",
    "real estate",
    "location",
    "address",
    "ip",
    "domain",
    "web",
    "review",
    "product",
    "job",
    "vehicle",
    "music",
    "movie",
]


def default_terms(mode: str) -> list[str]:
    if mode == "minimal":
        return ["", "j", "k", "q", "x", "z"] + list(string.digits)
    if mode == "letters":
        return [""] + list(string.ascii_lowercase) + list(string.digits)
    if mode == "broad":
        terms = [""] + list(string.ascii_lowercase) + list(string.digits) + COMMON_TERMS
        out: list[str] = []
        seen = set()
        for term in terms:
            key = term.lower()
            if key not in seen:
                seen.add(key)
                out.append(term)
        return out
    raise ValueError(f"unknown mode: {mode}")


def load_terms(path: str | None, mode: str) -> list[str]:
    if not path:
        return default_terms(mode)
    terms = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        term = line.strip()
        if term and not term.startswith("#"):
            terms.append(term)
    return terms


def load_existing_search(root: Path, category: str) -> dict[str, dict[str, Any]]:
    path = root / "data" / f"rapidapi_search_{safe_name(category)}_apis.csv"
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            api_id = row.get("api_id")
            if api_id:
                row["discovery_sources"] = "seed_search_csv"
                out[api_id] = row
    return out


def load_existing_discovery(root: Path, category: str) -> dict[str, dict[str, Any]]:
    path = root / "data" / f"rapidapi_discovery_{safe_name(category)}_apis.csv"
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            api_id = row.get("api_id")
            if api_id:
                row["discovery_sources"] = row.get("discovery_sources") or "seed_discovery_csv"
                out[api_id] = row
    return out


def crawl_combo(
    client: RapidApiClient,
    root: Path,
    category: str,
    term: str,
    sort: str,
    first: int,
    max_pages: int,
    delay: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_dir = root / "raw" / "graphql" / f"discovery_{safe_name(category)}" / f"term_{safe_name(term or 'blank')}__sort_{sort}"
    rows: list[dict[str, Any]] = []
    after = ""
    page = 0
    total = None
    stopped_by_cap = False
    referer = f"https://rapidapi.com/search/{category}?sortBy={sort}"

    while True:
        page += 1
        raw_path = raw_dir / f"page_{page:04d}.json"
        if raw_path.exists():
            data = read_json(raw_path)
        else:
            variables = {
                "paginationInput": {"first": first, "after": after},
                "searchApiOrderByInput": {"sortingFields": [{"fieldName": sort, "by": "ASC"}]},
                "searchApiWhereInput": {"term": term, "categoryNames": [category], "tags": []},
            }
            data = client.graphql(SEARCH_QUERY, variables, "searchApis", referer)
            write_json(raw_path, data)
            time.sleep(delay)

        products = data["data"]["products"]
        total = products.get("total", total)
        nodes = products.get("nodes") or []
        for idx, node in enumerate(nodes, 1):
            row = flatten_api(node, rank=((page - 1) * first + idx), page=page)
            row["discovery_term"] = term
            row["discovery_sort"] = sort
            rows.append(row)

        page_info = products.get("pageInfo") or {}
        after = page_info.get("endCursor") or ""
        if not page_info.get("hasNextPage"):
            if total and len(rows) < total:
                stopped_by_cap = True
            break
        if max_pages and page >= max_pages:
            stopped_by_cap = bool(total and len(rows) < total)
            break

    meta = {
        "term": term,
        "sort": sort,
        "rows": len(rows),
        "unique": len({r.get("api_id") for r in rows}),
        "reported_total": total,
        "pages": page,
        "stopped_by_cap": stopped_by_cap,
    }
    return rows, meta


def merge_rows(union: dict[str, dict[str, Any]], rows: list[dict[str, Any]], source: str) -> int:
    added = 0
    for row in rows:
        api_id = row.get("api_id")
        if not api_id:
            continue
        if api_id not in union:
            row = dict(row)
            row["discovery_sources"] = source
            union[api_id] = row
            added += 1
        else:
            old = union[api_id]
            sources = set(filter(None, str(old.get("discovery_sources", "")).split("|")))
            sources.add(source)
            old["discovery_sources"] = "|".join(sorted(sources))
    return added


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="rapidapi_crawl")
    ap.add_argument("--category", default="Data")
    ap.add_argument("--terms-mode", choices=["minimal", "letters", "broad"], default="minimal")
    ap.add_argument("--terms-file")
    ap.add_argument("--sorts", default=",".join(SORTS))
    ap.add_argument("--first", type=int, default=100)
    ap.add_argument("--max-pages-per-combo", type=int, default=10)
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--max-combos", type=int, default=0)
    ap.add_argument("--seed-existing", action="store_true")
    args = ap.parse_args()

    if args.first < 1 or args.first > 100:
        raise SystemExit("--first must be between 1 and 100")

    root = Path(args.root)
    terms = load_terms(args.terms_file, args.terms_mode)
    sorts = [s.strip() for s in args.sorts.split(",") if s.strip()]
    combos = [(term, sort) for term in terms for sort in sorts]
    if args.max_combos:
        combos = combos[: args.max_combos]

    client = RapidApiClient(args.category)
    client.init()
    union: dict[str, dict[str, Any]] = {}
    if args.seed_existing:
        search_seed = load_existing_search(root, args.category)
        discovery_seed = load_existing_discovery(root, args.category)
        union = discovery_seed if len(discovery_seed) > len(search_seed) else search_seed
    combo_meta: list[dict[str, Any]] = []
    out_csv = root / "data" / f"rapidapi_discovery_{safe_name(args.category)}_apis.csv"
    out_meta = root / "data" / f"rapidapi_discovery_{safe_name(args.category)}_summary.json"
    combo_csv = root / "data" / f"rapidapi_discovery_{safe_name(args.category)}_combos.csv"

    for idx, (term, sort) in enumerate(combos, 1):
        rows, meta = crawl_combo(client, root, args.category, term, sort, args.first, args.max_pages_per_combo, args.delay)
        source = f"term={term or '<blank>'};sort={sort}"
        added = merge_rows(union, rows, source)
        meta["combo_index"] = idx
        meta["new_unique_added"] = added
        meta["union_unique"] = len(union)
        combo_meta.append(meta)
        print(
            f"combo {idx}/{len(combos)} term={term or '<blank>'} sort={sort} "
            f"rows={meta['rows']} total={meta['reported_total']} added={added} union={len(union)}",
            flush=True,
        )
        save_csv(out_csv, list(union.values()))
        save_csv(combo_csv, combo_meta)
        write_json(out_meta, {"category": args.category, "unique_apis": len(union), "combos": combo_meta})


if __name__ == "__main__":
    main()

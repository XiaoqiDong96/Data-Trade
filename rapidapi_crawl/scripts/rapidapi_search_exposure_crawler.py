#!/usr/bin/env python3
"""Crawl and normalize RapidAPI search exposure/ranking panels.

Unlike the discovery crawler, this script keeps every API appearance in every
query window. The output is a query-sort-page-rank panel for exposure controls
and competition-set construction.
"""

from __future__ import annotations

import argparse
import csv
import json
import string
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from rapidapi_crawler import RapidApiClient, SEARCH_QUERY, flatten_api, read_json, safe_name, save_csv, write_json


SORTS = ["ByRelevance", "ByUpdatedAt", "ByAlphabetical"]
thread_state = threading.local()


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
        return dedupe([""] + list(string.ascii_lowercase) + list(string.digits) + COMMON_TERMS)
    if mode == "comprehensive":
        return dedupe([""] + list(string.ascii_lowercase) + list(string.digits) + COMMON_TERMS)
    raise ValueError(f"unknown terms mode: {mode}")


def dedupe(values: list[str]) -> list[str]:
    out = []
    seen = set()
    for value in values:
        key = value.strip().lower()
        if key not in seen:
            seen.add(key)
            out.append(value.strip())
    return out


def load_terms(root: Path, terms_mode: str, terms_files: list[str]) -> list[str]:
    terms = default_terms(terms_mode)
    if terms_mode == "comprehensive" and not terms_files:
        terms_files = [
            str(root / "data" / "rapidapi_common_terms.txt"),
            str(root / "data" / "rapidapi_extra_terms.txt"),
            str(root / "data" / "rapidapi_tail_terms.txt"),
        ]
    for file_name in terms_files:
        path = Path(file_name)
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            term = line.strip()
            if term and not term.startswith("#"):
                terms.append(term)
    return dedupe(terms)


def get_thread_client(category: str) -> RapidApiClient:
    client = getattr(thread_state, "client", None)
    if client is None or getattr(thread_state, "category", None) != category:
        client = RapidApiClient(category)
        client.init()
        thread_state.client = client
        thread_state.category = category
    return client


def raw_dir_for(root: Path, category: str, term: str, sort: str) -> Path:
    return root / "raw" / "graphql" / f"discovery_{safe_name(category)}" / f"term_{safe_name(term or 'blank')}__sort_{sort}"


def read_or_fetch_page(
    root: Path,
    category: str,
    term: str,
    sort: str,
    page: int,
    after: str,
    first: int,
    retry_errors: bool,
) -> tuple[dict[str, Any], str]:
    raw_path = raw_dir_for(root, category, term, sort) / f"page_{page:04d}.json"
    if raw_path.exists():
        try:
            data = read_json(raw_path)
            if not retry_errors or "__error__" not in data:
                return data, str(raw_path)
        except Exception:
            pass

    client = get_thread_client(category)
    referer = f"https://rapidapi.com/search/{category}?sortBy={sort}"
    variables = {
        "paginationInput": {"first": first, "after": after},
        "searchApiOrderByInput": {"sortingFields": [{"fieldName": sort, "by": "ASC"}]},
        "searchApiWhereInput": {"term": term, "categoryNames": [category], "tags": []},
    }
    try:
        data = client.graphql(SEARCH_QUERY, variables, "searchApis", referer)
    except Exception as exc:
        data = {"__error__": str(exc), "__term": term, "__sort": sort, "__page": page}
    write_json(raw_path, data)
    return data, str(raw_path)


def crawl_combo(
    root: Path,
    category: str,
    term: str,
    sort: str,
    first: int,
    max_pages: int,
    delay: float,
    retry_errors: bool,
    normalize_only: bool,
) -> dict[str, Any]:
    exposure_rows: list[dict[str, Any]] = []
    facet_rows: list[dict[str, Any]] = []
    after = ""
    reported_total = None
    stopped_by_cap = False
    error = None
    pages = 0

    for page in range(1, max_pages + 1):
        raw_path = raw_dir_for(root, category, term, sort) / f"page_{page:04d}.json"
        if normalize_only and not raw_path.exists():
            break
        data, raw_file = read_or_fetch_page(root, category, term, sort, page, after, first, retry_errors)
        if data.get("__error__"):
            error = data.get("__error__")
            break

        products = (data.get("data") or {}).get("products") or {}
        nodes = products.get("nodes") or []
        page_info = products.get("pageInfo") or {}
        query_id = products.get("queryID")
        replica_index = products.get("replicaIndex")
        reported_total = products.get("total", reported_total)
        pages = page

        for idx, node in enumerate(nodes, 1):
            rank = (page - 1) * first + idx
            row = flatten_api(node, rank=rank, page=page)
            row.update(
                {
                    "search_term": term,
                    "search_sort": sort,
                    "search_page": page,
                    "search_page_position": idx,
                    "search_rank": rank,
                    "reported_total": reported_total,
                    "query_id": query_id,
                    "replica_index": replica_index,
                    "page_start_cursor": page_info.get("startCursor"),
                    "page_end_cursor": page_info.get("endCursor"),
                    "has_next_page": page_info.get("hasNextPage"),
                    "has_previous_page": page_info.get("hasPreviousPage"),
                    "raw_file": raw_file,
                }
            )
            exposure_rows.append(row)

        facets = (products.get("facets") or {}).get("category") or []
        for facet in facets:
            facet_rows.append(
                {
                    "search_term": term,
                    "search_sort": sort,
                    "search_page": page,
                    "query_id": query_id,
                    "replica_index": replica_index,
                    "reported_total": reported_total,
                    "facet": "category",
                    "facet_key": facet.get("key"),
                    "facet_count": facet.get("count"),
                    "raw_file": raw_file,
                }
            )

        after = page_info.get("endCursor") or ""
        if not page_info.get("hasNextPage"):
            if reported_total and len(exposure_rows) < reported_total:
                stopped_by_cap = True
            break
        if delay and not normalize_only:
            time.sleep(delay)
    else:
        stopped_by_cap = bool(reported_total and len(exposure_rows) < reported_total)

    return {
        "term": term,
        "sort": sort,
        "rows": len(exposure_rows),
        "unique": len({row.get("api_id") for row in exposure_rows if row.get("api_id")}),
        "reported_total": reported_total,
        "pages": pages,
        "stopped_by_cap": stopped_by_cap,
        "error": error,
        "exposure_rows": exposure_rows,
        "facet_rows": facet_rows,
    }


def write_all(root: Path, category: str, exposures: list[dict[str, Any]], facets: list[dict[str, Any]], combos: list[dict[str, Any]]) -> None:
    suffix = safe_name(category)
    data_dir = root / "data"
    save_csv(data_dir / f"rapidapi_search_{suffix}_exposure_panel.csv", exposures)
    save_csv(data_dir / f"rapidapi_search_{suffix}_exposure_facets.csv", facets)
    save_csv(data_dir / f"rapidapi_search_{suffix}_exposure_combos.csv", combos)
    summary = {
        "category": category,
        "exposure_rows": len(exposures),
        "facet_rows": len(facets),
        "combos": len(combos),
        "unique_apis": len({row.get("api_id") for row in exposures if row.get("api_id")}),
        "terms": len({row.get("search_term") for row in exposures}),
        "sorts": sorted({row.get("search_sort") for row in exposures if row.get("search_sort")}),
        "errors": sum(bool(row.get("error")) for row in combos),
        "stopped_by_cap": sum(str(row.get("stopped_by_cap")).lower() == "true" for row in combos),
    }
    (data_dir / f"rapidapi_search_{suffix}_exposure_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="rapidapi_crawl")
    ap.add_argument("--category", default="Data")
    ap.add_argument("--terms-mode", choices=["minimal", "letters", "broad", "comprehensive"], default="comprehensive")
    ap.add_argument("--terms-file", action="append", default=[])
    ap.add_argument("--sorts", default=",".join(SORTS))
    ap.add_argument("--first", type=int, default=100)
    ap.add_argument("--max-pages-per-combo", type=int, default=10)
    ap.add_argument("--delay", type=float, default=0.1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--retry-errors", action="store_true")
    ap.add_argument("--normalize-only", action="store_true")
    ap.add_argument("--max-combos", type=int, default=0)
    ap.add_argument("--save-every", type=int, default=25)
    args = ap.parse_args()

    if args.first < 1 or args.first > 100:
        raise SystemExit("--first must be between 1 and 100")
    root = Path(args.root)
    terms = load_terms(root, args.terms_mode, args.terms_file)
    sorts = [sort.strip() for sort in args.sorts.split(",") if sort.strip()]
    combos = [(term, sort) for term in terms for sort in sorts]
    if args.max_combos:
        combos = combos[: args.max_combos]

    exposures: list[dict[str, Any]] = []
    facets: list[dict[str, Any]] = []
    combo_meta: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                crawl_combo,
                root,
                args.category,
                term,
                sort,
                args.first,
                args.max_pages_per_combo,
                args.delay,
                args.retry_errors,
                args.normalize_only,
            ): (term, sort)
            for term, sort in combos
        }
        for idx, fut in enumerate(as_completed(futures), 1):
            result = fut.result()
            exposure_rows = result.pop("exposure_rows")
            facet_rows = result.pop("facet_rows")
            exposures.extend(exposure_rows)
            facets.extend(facet_rows)
            combo_meta.append(result)
            print(
                f"combo {idx}/{len(combos)} term={result['term'] or '<blank>'} sort={result['sort']} "
                f"rows={result['rows']} total={result['reported_total']} error={bool(result['error'])}",
                flush=True,
            )
            if args.save_every and idx % args.save_every == 0:
                write_all(root, args.category, exposures, facets, combo_meta)

    write_all(root, args.category, exposures, facets, combo_meta)
    summary_path = root / "data" / f"rapidapi_search_{safe_name(args.category)}_exposure_summary.json"
    print(summary_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

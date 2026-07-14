#!/usr/bin/env python3
"""Collect external data used to identify demand, substitution, and costs.

The crawler is intentionally resumable and source-separated. Raw files contain
only public metadata needed for reproducibility; code-search line contents and
credentials are never retained.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, urljoin, urlparse

import pandas as pd
import requests


UA = "RapidAPI-data-commodity-research/1.0 (public academic metadata collection)"
SOURCEGRAPH_URL = "https://sourcegraph.com/.api/search/stream"
DATAGOV_URL = "https://api.gsa.gov/technology/datagov/v4/search"
EUROPE_URL = "https://data.europa.eu/api/hub/search/search"
GLEIF_URL = "https://api.gleif.org/api/v1/lei-records"
OECD_DSTRI_URL = (
    "https://sdmx.oecd.org/public/rest/v1/data/"
    "OECD.TAD.TPD,DSD_STRI@DF_STRI_DIGITAL,1.0/A.......?startPeriod=2014"
)
CC_COLLECTIONS_URL = "https://index.commoncrawl.org/collinfo.json"
AWS_PRICE_URLS = {
    "AmazonApiGateway": "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonApiGateway/current/index.json",
    "AWSLambda": "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSLambda/current/index.json",
    "AmazonCloudFront": "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonCloudFront/current/index.json",
}
WORLD_BANK_INDICATORS = {
    "NY.GDP.PCAP.CD": "gdp_per_capita_usd",
    "IT.NET.USER.ZS": "internet_users_pct",
    "IT.NET.SECR.P6": "secure_servers_per_million",
    "BX.GSR.CCIS.ZS": "ict_service_exports_share",
    "IT.CEL.SETS.P2": "mobile_subscriptions_per_100",
}

STOP = {
    "api", "apis", "data", "service", "services", "free", "best", "new",
    "public", "real", "time", "online", "platform", "application", "v1",
    "get", "search", "information", "database", "official", "global",
}
CC_TLDS = {
    "uk": "GB", "de": "DE", "fr": "FR", "ca": "CA", "au": "AU",
    "jp": "JP", "cn": "CN", "in": "IN", "br": "BR", "ch": "CH",
    "nl": "NL", "se": "SE", "no": "NO", "fi": "FI", "dk": "DK",
    "it": "IT", "es": "ES", "pl": "PL", "ie": "IE", "sg": "SG",
    "nz": "NZ", "za": "ZA", "mx": "MX", "ru": "RU", "ua": "UA",
    "tr": "TR", "ae": "AE", "il": "IL", "kr": "KR", "tw": "TW",
}
MULTI_SUFFIX = {"co.uk", "org.uk", "com.au", "com.br", "co.jp", "co.in", "co.nz", "co.za"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def safe_name(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or ""))
    return text[:180] or "missing"


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str] | None = None) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        seen = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv_optional(path: Path) -> pd.DataFrame:
    if not path.exists() or not path.stat().st_size:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 45,
    attempts: int = 4,
) -> requests.Response:
    merged = {"User-Agent": UA, "Accept": "application/json,text/html;q=0.9,*/*;q=0.8"}
    if headers:
        merged.update(headers)
    error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.request(
                method, url, params=params, json=json_body, headers=merged,
                timeout=timeout, allow_redirects=True,
            )
            if response.status_code == 429 or response.status_code >= 500:
                raise RuntimeError(f"HTTP {response.status_code}")
            response.raise_for_status()
            return response
        except Exception as exc:
            error = exc
            if attempt < attempts:
                time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"request failed {url}: {error}")


def tokens(value: Any) -> set[str]:
    return {
        token for token in re.findall(r"[a-z][a-z0-9]{2,}", clean_text(value).lower())
        if token not in STOP and not token.isdigit()
    }


def similarity(left: Any, right: Any) -> float:
    a, b = clean_text(left).lower(), clean_text(right).lower()
    if not a or not b:
        return 0.0
    ta, tb = tokens(a), tokens(b)
    jac = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    seq = SequenceMatcher(None, a[:600], b[:600]).ratio()
    return 0.65 * jac + 0.35 * seq


def pick_language(value: Any, preferred: str = "en") -> str:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return clean_text(value.get(preferred) or next(iter(value.values()), ""))
    if isinstance(value, list):
        return " ".join(clean_text(item) for item in value if item)
    return clean_text(value)


def load_apis(root: Path) -> pd.DataFrame:
    path = root / "data_merged" / "rapidapi_merged_api_master.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path, low_memory=False)
    if "api_id" not in df:
        raise ValueError("api master lacks api_id")
    return df


def parse_hosts(value: Any) -> list[str]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except Exception:
        parsed = []
    if not isinstance(parsed, list):
        parsed = []
    return sorted({str(item).lower().strip() for item in parsed if item})


def should_fetch(path: Path, retry_errors: bool) -> bool:
    if not path.exists():
        return True
    if not retry_errors:
        return False
    try:
        return bool(read_json(path).get("error"))
    except Exception:
        return True


def parse_sse(text: str) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    event_name = "message"
    data_lines: list[str] = []
    for line in text.splitlines() + [""]:
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
        elif not line and data_lines:
            raw = "\n".join(data_lines)
            try:
                value = json.loads(raw)
            except Exception:
                value = raw
            events.append((event_name, value))
            event_name, data_lines = "message", []
    return events


def fetch_sourcegraph_one(root: Path, row: dict[str, Any], delay: float, retry_errors: bool) -> dict[str, Any]:
    api_id = str(row["api_id"])
    path = root / "external_raw" / "sourcegraph" / f"{safe_name(api_id)}.json"
    if not should_fetch(path, retry_errors):
        return read_json(path)
    hosts = parse_hosts(row.get("dns_addresses_json"))
    host = hosts[0] if hosts else ""
    payload: dict[str, Any] = {
        "api_id": api_id, "api_host": host, "fetched_at": utc_now(),
        "source_url": SOURCEGRAPH_URL,
    }
    if not host:
        payload["error"] = "missing_api_host"
        atomic_json(path, payload)
        return payload
    try:
        response = request(
            "GET", SOURCEGRAPH_URL,
            params={"q": f'context:global "{host}" count:500', "v": "V3"},
            headers={"Accept": "text/event-stream"}, timeout=75,
        )
        repositories: dict[str, dict[str, Any]] = {}
        files, matches = set(), 0
        languages: Counter[str] = Counter()
        progress: dict[str, Any] = {}
        for event, value in parse_sse(response.text):
            if event == "matches" and isinstance(value, list):
                for match in value:
                    if match.get("type") != "content":
                        continue
                    repo = clean_text(match.get("repository"))
                    file_key = f"{repo}:{clean_text(match.get('path'))}"
                    if repo:
                        repositories[repo] = {
                            "repository": repo,
                            "repo_stars": match.get("repoStars"),
                            "repo_last_fetched": match.get("repoLastFetched"),
                            "commit": match.get("commit"),
                        }
                    files.add(file_key)
                    language = clean_text(match.get("language")) or "unknown"
                    line_count = len(match.get("lineMatches") or [])
                    matches += max(1, line_count)
                    languages[language] += max(1, line_count)
            if event == "progress" and isinstance(value, dict) and value.get("done"):
                progress = value
        payload.update({
            "match_count": progress.get("matchCount", matches),
            "repository_count": progress.get("repositoriesCount", len(repositories)),
            "matched_file_count": len(files),
            "repositories": list(repositories.values()),
            "repo_star_sum": sum(float(v.get("repo_stars") or 0) for v in repositories.values()),
            "repo_star_max": max([float(v.get("repo_stars") or 0) for v in repositories.values()] or [0]),
            "languages": dict(languages),
            "result_truncated": int((progress.get("matchCount") or matches) >= 500),
        })
    except Exception as exc:
        payload["error"] = str(exc)
    atomic_json(path, payload)
    if delay:
        time.sleep(delay)
    return payload


def fetch_sourcegraph_batch(root: Path, rows: list[dict[str, Any]], delay: float) -> list[dict[str, Any]]:
    targets = []
    for row in rows:
        hosts = parse_hosts(row.get("dns_addresses_json"))
        targets.append((str(row["api_id"]), hosts[0] if hosts else ""))
    host_to_api = {host: api_id for api_id, host in targets if host}
    fetched_at = utc_now()
    payloads = {
        api_id: {"api_id": api_id, "api_host": host, "fetched_at": fetched_at, "source_url": SOURCEGRAPH_URL}
        for api_id, host in targets
    }
    if not host_to_api:
        for payload in payloads.values(): payload["error"] = "missing_api_host"
    else:
        pattern = "(?:" + "|".join(re.escape(host) for host in host_to_api) + ")"
        try:
            response = request(
                "GET", SOURCEGRAPH_URL,
                params={"q": f"context:global patterntype:regexp {pattern} count:5000", "v": "V3"},
                headers={"Accept": "text/event-stream"}, timeout=90,
            )
            repos: dict[str, dict[str, dict[str, Any]]] = {host: {} for host in host_to_api}
            files: dict[str, set[str]] = {host: set() for host in host_to_api}
            matches: Counter[str] = Counter()
            languages: dict[str, Counter[str]] = {host: Counter() for host in host_to_api}
            final_progress: dict[str, Any] = {}
            for event, value in parse_sse(response.text):
                if event == "matches" and isinstance(value, list):
                    for match in value:
                        if match.get("type") != "content": continue
                        repo = clean_text(match.get("repository")); path = clean_text(match.get("path"))
                        language = clean_text(match.get("language")) or "unknown"
                        for line_match in match.get("lineMatches") or []:
                            line = clean_text(line_match.get("line")).lower()
                            for host in host_to_api:
                                if host not in line: continue
                                if repo:
                                    repos[host][repo] = {
                                        "repository": repo, "repo_stars": match.get("repoStars"),
                                        "repo_last_fetched": match.get("repoLastFetched"), "commit": match.get("commit"),
                                    }
                                files[host].add(f"{repo}:{path}")
                                matches[host] += 1; languages[host][language] += 1
                elif event == "progress" and isinstance(value, dict) and value.get("done"):
                    final_progress = value
            batch_truncated = int(float(final_progress.get("matchCount") or 0) >= 5000)
            for host, api_id in host_to_api.items():
                values = repos[host]
                payloads[api_id].update({
                    "match_count": matches[host], "repository_count": len(values),
                    "matched_file_count": len(files[host]), "repositories": list(values.values()),
                    "repo_star_sum": sum(float(v.get("repo_stars") or 0) for v in values.values()),
                    "repo_star_max": max([float(v.get("repo_stars") or 0) for v in values.values()] or [0]),
                    "languages": dict(languages[host]), "result_truncated": batch_truncated,
                    "batch_size": len(host_to_api),
                })
        except Exception as exc:
            for payload in payloads.values(): payload["error"] = str(exc)
    for api_id, payload in payloads.items():
        atomic_json(root / "external_raw" / "sourcegraph" / f"{safe_name(api_id)}.json", payload)
    if delay: time.sleep(delay)
    return list(payloads.values())


def stage_adoption(root: Path, workers: int, delay: float, retry_errors: bool, limit: int) -> None:
    df = load_apis(root)
    rows = df.to_dict("records")[:limit or None]
    results: list[dict[str, Any]] = []
    pending = []
    for row in rows:
        path = root / "external_raw" / "sourcegraph" / f"{safe_name(row['api_id'])}.json"
        if should_fetch(path, retry_errors): pending.append(row)
        else: results.append(read_json(path))
    batch_size = max(4, int(os.environ.get("SOURCEGRAPH_BATCH_SIZE", "12")))
    batches = [pending[i:i + batch_size] for i in range(0, len(pending), batch_size)]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(fetch_sourcegraph_batch, root, batch, delay) for batch in batches]
        for idx, future in enumerate(as_completed(futures), 1):
            results.extend(future.result())
            if idx % 10 == 0 or idx == len(futures):
                ok = sum(not row.get("error") for row in results)
                print(f"adoption_batches {idx}/{len(futures)} api_results={len(results)}/{len(rows)} ok={ok}", flush=True)
    api_rows, repo_rows = [], []
    for item in results:
        repos = item.get("repositories") or []
        api_rows.append({
            "api_id": item.get("api_id"), "api_host": item.get("api_host"),
            "github_code_match_count": item.get("match_count"),
            "github_repository_count": item.get("repository_count"),
            "github_matched_file_count": item.get("matched_file_count"),
            "github_repo_star_sum": item.get("repo_star_sum"),
            "github_repo_star_max": item.get("repo_star_max"),
            "github_languages_json": compact_json(item.get("languages") or {}),
            "github_result_truncated": item.get("result_truncated"),
            "github_search_error": item.get("error"),
            "github_fetched_at": item.get("fetched_at"),
            "github_source": "Sourcegraph public index of GitHub repositories",
        })
        for repo in repos:
            repo_rows.append({"api_id": item.get("api_id"), "api_host": item.get("api_host"), **repo})
    save_csv(root / "data_external" / "external_api_adoption.csv", sorted(api_rows, key=lambda r: str(r["api_id"])))
    save_csv(root / "data_external" / "external_code_repositories.csv", repo_rows, ["api_id", "api_host", "repository", "repo_stars", "repo_last_fetched", "commit"])


def query_for_api(row: dict[str, Any]) -> str:
    title = clean_text(row.get("api_title") or row.get("api_name") or row.get("api_slug"))
    chosen = [word for word in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", title) if word.lower() not in STOP]
    if not chosen:
        chosen = list(tokens(row.get("api_description")))[:4]
    return " ".join(chosen[:6])[:120]


def normalize_datagov(item: dict[str, Any]) -> dict[str, Any]:
    dcat = item.get("dcat") or item
    publisher = dcat.get("publisher") or {}
    return {
        "candidate_id": pick_language(dcat.get("identifier")),
        "candidate_title": pick_language(dcat.get("title")),
        "candidate_description": pick_language(dcat.get("description")),
        "candidate_keywords": pick_language(dcat.get("keyword")),
        "candidate_publisher": pick_language(publisher.get("name") if isinstance(publisher, dict) else publisher),
        "candidate_url": pick_language(dcat.get("landingPage") or dcat.get("accessURL")),
        "candidate_country": "US",
    }


def normalize_europe(item: dict[str, Any]) -> dict[str, Any]:
    country = item.get("country") or {}
    catalog = item.get("catalog") or {}
    publisher = catalog.get("publisher") or {}
    return {
        "candidate_id": pick_language(item.get("identifier")),
        "candidate_title": pick_language(item.get("title")),
        "candidate_description": pick_language(item.get("description")),
        "candidate_keywords": " ".join(pick_language(x.get("label")) for x in (item.get("keywords") or []) if isinstance(x, dict)),
        "candidate_publisher": pick_language(publisher.get("name") if isinstance(publisher, dict) else publisher),
        "candidate_url": pick_language(item.get("resource")),
        "candidate_country": pick_language(country.get("id") if isinstance(country, dict) else country).upper(),
    }


def fetch_open_one(root: Path, row: dict[str, Any], source: str, delay: float, retry_errors: bool) -> dict[str, Any]:
    api_id = str(row["api_id"])
    path = root / "external_raw" / "open_data" / source / f"{safe_name(api_id)}.json"
    if not should_fetch(path, retry_errors):
        return read_json(path)
    query = query_for_api(row)
    payload: dict[str, Any] = {"api_id": api_id, "source": source, "query": query, "fetched_at": utc_now()}
    try:
        if not query:
            raise RuntimeError("empty_query")
        if source == "data_gov":
            response = request("GET", DATAGOV_URL, params={"q": query, "size": 12}, headers={"X-Api-Key": os.environ.get("DATA_GOV_API_KEY", "DEMO_KEY")})
            body = response.json()
            candidates = [normalize_datagov(item) for item in (body.get("results") or [])]
            payload["source_url"] = response.url
        else:
            response = request("POST", EUROPE_URL, json_body={"q": query, "filters": ["dataset", "dataservice"], "page": 0, "limit": 12})
            body = response.json().get("result") or {}
            candidates = [normalize_europe(item) for item in (body.get("results") or [])]
            payload["source_url"] = EUROPE_URL
        api_text = " ".join([clean_text(row.get("api_title")), clean_text(row.get("api_description")), clean_text(row.get("api_slug"))])
        for candidate in candidates:
            candidate_text = " ".join([candidate["candidate_title"], candidate["candidate_description"], candidate["candidate_keywords"]])
            candidate["match_score"] = round(similarity(api_text, candidate_text), 6)
        payload["candidates"] = sorted(candidates, key=lambda x: x["match_score"], reverse=True)
        payload["reported_total"] = (body.get("count") or {}).get("total") if source == "data_europa" else body.get("total")
    except Exception as exc:
        payload["error"] = str(exc)
    atomic_json(path, payload)
    if delay:
        time.sleep(delay)
    return payload


def stage_open_substitutes(root: Path, workers: int, delay: float, retry_errors: bool, limit: int) -> None:
    df = load_apis(root)
    if limit:
        df = df.head(limit)
    fixed_queries = [
        "geolocation", "address", "weather", "finance", "stock market", "company",
        "people", "social media", "public records", "real estate", "transport",
        "health", "sports", "news", "ecommerce", "prices", "jobs", "demographics",
        "census", "satellite", "crime", "legal", "education", "environment", "energy",
        "tourism", "maps", "identity", "phone", "email", "web data",
    ]
    title_counter: Counter[str] = Counter()
    for value in df.get("api_title", pd.Series(dtype=str)).fillna(""):
        title_counter.update(tokens(value))
    extra_queries = [token for token, _ in title_counter.most_common(35) if token not in set(fixed_queries)]
    eu_queries = list(dict.fromkeys(fixed_queries + extra_queries))
    jobs = [(query, "data_gov") for query in fixed_queries] + [(query, "data_europa") for query in eu_queries]

    def fetch_query(query: str, source: str) -> dict[str, Any]:
        query_id = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
        path = root / "external_raw" / "open_data_catalog" / source / f"{query_id}.json"
        if not should_fetch(path, retry_errors):
            return read_json(path)
        payload: dict[str, Any] = {"source": source, "query": query, "fetched_at": utc_now()}
        try:
            if source == "data_gov":
                response = request("GET", DATAGOV_URL, params={"q": query, "size": 100}, headers={"X-Api-Key": os.environ.get("DATA_GOV_API_KEY", "DEMO_KEY")})
                body = response.json()
                candidates = [normalize_datagov(item) for item in (body.get("results") or [])]
                payload["source_url"] = response.url
            else:
                response = request("POST", EUROPE_URL, json_body={"q": query, "filters": ["dataset", "dataservice"], "page": 0, "limit": 100})
                body = response.json().get("result") or {}
                candidates = [normalize_europe(item) for item in (body.get("results") or [])]
                payload["source_url"] = EUROPE_URL
            payload["candidates"] = candidates
        except Exception as exc:
            payload["error"] = str(exc)
        atomic_json(path, payload)
        if delay:
            time.sleep(delay)
        return payload

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(fetch_query, query, source) for query, source in jobs]
        for idx, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if idx % 20 == 0 or idx == len(futures):
                ok = sum(not row.get("error") for row in results)
                print(f"open_catalog_queries {idx}/{len(futures)} ok={ok} error={idx-ok}", flush=True)

    catalog: dict[tuple[str, str], dict[str, Any]] = {}
    for result in results:
        for candidate in result.get("candidates") or []:
            key = (result.get("source", ""), candidate.get("candidate_id") or candidate.get("candidate_url") or candidate.get("candidate_title"))
            catalog[key] = {"open_source": result.get("source"), **candidate}
    candidates = list(catalog.values())
    inverted: dict[str, set[int]] = defaultdict(set)
    candidate_texts = []
    for idx, candidate in enumerate(candidates):
        text = " ".join([candidate.get("candidate_title", ""), candidate.get("candidate_description", ""), candidate.get("candidate_keywords", "")])
        candidate_texts.append(text)
        for token in tokens(text):
            inverted[token].add(idx)

    candidate_rows, summary_map = [], defaultdict(list)
    for api in df.to_dict("records"):
        api_id = str(api["api_id"])
        api_text = " ".join([clean_text(api.get("api_title")), clean_text(api.get("api_description")), clean_text(api.get("api_slug"))])
        shortlist: set[int] = set()
        for token in tokens(api_text):
            shortlist.update(inverted.get(token, set()))
        scored = []
        for idx in shortlist:
            score = similarity(api_text, candidate_texts[idx])
            if score >= 0.12:
                scored.append((score, idx))
        for rank, (score, idx) in enumerate(sorted(scored, reverse=True)[:12], 1):
            row = {"api_id": api_id, "candidate_rank": rank, **candidates[idx], "match_score": round(score, 6)}
            candidate_rows.append(row)
            summary_map[api_id].append(row)
    print(f"open_catalog unique_candidates={len(candidates)} matched_apis={len(summary_map)}", flush=True)
    summaries = []
    for api_id in df["api_id"].astype(str):
        candidates = summary_map.get(api_id, [])
        best = max(candidates, key=lambda x: x.get("match_score", 0), default={})
        scores = [float(x.get("match_score") or 0) for x in candidates]
        summaries.append({
            "api_id": api_id,
            "open_candidate_count": len(candidates),
            "open_match_count_030": sum(score >= 0.30 for score in scores),
            "open_match_count_045": sum(score >= 0.45 for score in scores),
            "open_best_score": max(scores, default=0),
            "open_best_source": best.get("open_source"),
            "open_best_title": best.get("candidate_title"),
            "open_best_url": best.get("candidate_url"),
            "open_substitute_indicator": int(max(scores, default=0) >= 0.45),
        })
    save_csv(root / "data_external" / "open_data_candidates.csv", candidate_rows)
    save_csv(root / "data_external" / "external_open_substitutes.csv", summaries)


def endpoint_token_sets(root: Path) -> dict[str, set[str]]:
    path = root / "data_merged" / "rapidapi_merged_endpoint_schema.csv"
    df = pd.read_csv(path, low_memory=False).fillna("")
    fields = [
        "route", "endpoint_name", "param_names_json", "payload_names_json",
        "payload_types", "payload_formats", "payload_status_codes",
    ]
    result: dict[str, set[str]] = defaultdict(set)
    for row in df[["api_id", *fields]].itertuples(index=False, name=None):
        api_id = str(row[0])
        for value in row[1:]:
            result[api_id].update(tokens(value))
    return result


def stage_schema_overlap(root: Path, limit: int) -> None:
    apis = load_apis(root)
    if limit:
        apis = apis.head(limit)
    token_map = endpoint_token_sets(root)
    groups = apis.groupby("primary_type", dropna=False)["api_id"].apply(lambda s: [str(x) for x in s]).to_dict()
    pairs: list[dict[str, Any]] = []
    best: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for market, ids in groups.items():
        for i, left in enumerate(ids):
            a = token_map.get(left, set())
            if not a:
                continue
            for right in ids[i + 1:]:
                b = token_map.get(right, set())
                if not b:
                    continue
                shared = len(a & b)
                if shared < 2:
                    continue
                score = shared / len(a | b)
                if score < 0.08:
                    continue
                best[left].append((score, right))
                best[right].append((score, left))
                if score >= 0.20:
                    pairs.append({"api_id_left": left, "api_id_right": right, "primary_type": market, "schema_jaccard": round(score, 6), "shared_tokens": shared})
        print(f"schema_overlap market={market} n={len(ids)}", flush=True)
    summaries = []
    for api_id in apis["api_id"].astype(str):
        values = sorted(best.get(api_id, []), reverse=True)[:10]
        summaries.append({
            "api_id": api_id,
            "schema_overlap_best": values[0][0] if values else 0,
            "schema_overlap_mean_top5": sum(v[0] for v in values[:5]) / min(5, len(values)) if values else 0,
            "schema_near_substitutes_020": sum(v[0] >= 0.20 for v in best.get(api_id, [])),
            "schema_best_match_api_id": values[0][1] if values else "",
            "schema_token_count": len(token_map.get(api_id, set())),
            "schema_overlap_definition": "Jaccard overlap of endpoint routes, parameter names, payload names, formats and types",
        })
    save_csv(
        root / "data_external" / "schema_overlap_pairs.csv",
        sorted(pairs, key=lambda x: x["schema_jaccard"], reverse=True),
        ["api_id_left", "api_id_right", "primary_type", "schema_jaccard", "shared_tokens"],
    )
    save_csv(root / "data_external" / "api_schema_replicability.csv", summaries)


def flatten_response(value: Any, prefix: str = "", depth: int = 0) -> tuple[set[str], set[str]]:
    fields: set[str] = set()
    value_hashes: set[str] = set()
    if depth > 6:
        return fields, value_hashes
    if isinstance(value, dict):
        for key, item in list(value.items())[:250]:
            path = f"{prefix}.{key}" if prefix else str(key)
            fields.add(path.lower())
            child_fields, child_hashes = flatten_response(item, path, depth + 1)
            fields.update(child_fields)
            value_hashes.update(child_hashes)
    elif isinstance(value, list):
        for item in value[:20]:
            child_fields, child_hashes = flatten_response(item, prefix + "[]", depth + 1)
            fields.update(child_fields)
            value_hashes.update(child_hashes)
    elif value is not None:
        normalized = clean_text(value).lower()[:500]
        if normalized:
            value_hashes.add(hashlib.sha256(normalized.encode("utf-8")).hexdigest())
    return fields, value_hashes


def stage_response_samples(root: Path, workers: int, delay: float, retry_errors: bool, limit: int) -> None:
    key = os.environ.get("RAPIDAPI_KEY") or os.environ.get("X_RAPIDAPI_KEY")
    status_path = root / "data_external" / "response_sampling_status.json"
    if not key:
        atomic_json(status_path, {"state": "credential_missing", "required_env": ["RAPIDAPI_KEY", "X_RAPIDAPI_KEY"], "updated_at": utc_now()})
        print("response_samples skipped: RAPIDAPI_KEY is not set", flush=True)
        return

    apis = load_apis(root).fillna("")
    apis = apis[apis.get("has_free_plan", 0).eq(1)].copy()
    apis["api_host"] = apis["dns_addresses_json"].map(lambda x: (parse_hosts(x) or [""])[0])
    endpoints = pd.read_csv(root / "data_merged" / "rapidapi_merged_endpoint_schema.csv", low_memory=False).fillna("")
    endpoints = endpoints[
        endpoints["method"].astype(str).str.upper().eq("GET")
        & pd.to_numeric(endpoints["required_params_count"], errors="coerce").fillna(0).eq(0)
        & ~endpoints["route"].astype(str).str.contains(r"[{}]", regex=True)
    ].sort_values(["api_id", "endpoint_index"]).drop_duplicates("api_id")
    targets = apis.merge(endpoints[["api_id", "endpoint_id", "route"]], on="api_id", how="inner")
    targets = targets[targets["api_host"] != ""].head(limit or len(targets))

    def fetch_one(row: dict[str, Any]) -> dict[str, Any]:
        api_id = str(row["api_id"])
        path = root / "external_raw" / "response_fingerprints" / f"{safe_name(api_id)}.json"
        if not should_fetch(path, retry_errors):
            return read_json(path)
        route = "/" + str(row["route"]).lstrip("/")
        url = f"https://{row['api_host']}{route}"
        payload = {"api_id": api_id, "api_host": row["api_host"], "endpoint_id": row["endpoint_id"], "route": route, "fetched_at": utc_now()}
        try:
            response = requests.get(
                url,
                headers={"User-Agent": UA, "X-RapidAPI-Key": key, "X-RapidAPI-Host": row["api_host"]},
                timeout=30,
            )
            payload.update({"http_status": response.status_code, "content_type": response.headers.get("content-type"), "response_bytes": len(response.content)})
            if "json" in (response.headers.get("content-type") or "").lower() and len(response.content) <= 5_000_000:
                fields, hashes = flatten_response(response.json())
                payload["field_paths"] = sorted(fields)[:5000]
                payload["value_hashes"] = sorted(hashes)[:5000]
            else:
                payload["body_sha256"] = hashlib.sha256(response.content[:5_000_000]).hexdigest()
        except Exception as exc:
            payload["error"] = str(exc)
        atomic_json(path, payload)
        if delay:
            time.sleep(delay)
        return payload

    results = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(fetch_one, row) for row in targets.to_dict("records")]
        for idx, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if idx % 100 == 0 or idx == len(futures):
                print(f"response_samples {idx}/{len(futures)}", flush=True)
    summaries = [{
        "api_id": item.get("api_id"), "sample_http_status": item.get("http_status"),
        "sample_response_bytes": item.get("response_bytes"), "sample_field_count": len(item.get("field_paths") or []),
        "sample_value_hash_count": len(item.get("value_hashes") or []), "sample_error": item.get("error"),
    } for item in results]
    save_csv(
        root / "data_external" / "api_response_fingerprints.csv", summaries,
        ["api_id", "sample_http_status", "sample_response_bytes", "sample_field_count", "sample_value_hash_count", "sample_error"],
    )
    atomic_json(status_path, {"state": "complete", "target_count": len(targets), "result_count": len(results), "updated_at": utc_now()})


def meta_value(page: str, key: str) -> str:
    patterns = [
        rf'<meta[^>]+(?:name|property)=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']*)',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:name|property)=["\']{re.escape(key)}["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, page, re.I)
        if match:
            return clean_text(match.group(1))
    return ""


def page_title(page: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    return clean_text(match.group(1)) if match else ""


def scrape_product(url: str, market: str, market_id: str = "", category: str = "") -> dict[str, Any]:
    response = request("GET", url, headers={"Accept": "text/html"}, timeout=45)
    page = response.text
    text = clean_text(page)
    prices = sorted({float(x.replace(",", "")) for x in re.findall(r"\$\s*(?:<!--.*?-->)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", page, re.S)})
    endpoints = sorted(set(re.findall(r"/v\d+/[A-Za-z0-9_{}./-]+", text)))
    return {
        "market": market, "market_product_id": market_id,
        "product_url": response.url, "product_slug": urlparse(response.url).path.rstrip("/").split("/")[-1],
        "product_title": meta_value(page, "og:title") or page_title(page),
        "product_description": meta_value(page, "og:description") or meta_value(page, "description"),
        "category": category, "prices_usd_json": compact_json(prices),
        "min_public_price_usd": min(prices) if prices else None,
        "max_public_price_usd": max(prices) if prices else None,
        "has_free_text": int(bool(re.search(r"\bfree\b", text, re.I))),
        "endpoint_count_visible": len(endpoints), "endpoints_json": compact_json(endpoints[:100]),
        "page_bytes": len(response.content), "http_status": response.status_code,
        "fetched_at": utc_now(),
    }


def stage_competitors(root: Path, workers: int, delay: float, retry_errors: bool, limit: int) -> None:
    raw = root / "external_raw" / "competitors"
    raw.mkdir(parents=True, exist_ok=True)
    products: list[dict[str, Any]] = []

    listing = request("GET", "https://marketplace.apilayer.com/get-api-data", params={"sort_by": "featured", "q": "", "view_by": "grid_view"}, headers={"Accept": "text/html"}).text
    apilayer_jobs, seen = [], set()
    pattern = re.compile(r'data-api="([^"]+)"[^>]*data-category="([^"]+)"[^>]*.*?href="(/[^"]+-api)(?:\?[^"]*)?"', re.I | re.S)
    for market_id, category, href in pattern.findall(listing):
        url = urljoin("https://marketplace.apilayer.com", href)
        if url not in seen:
            seen.add(url)
            apilayer_jobs.append((url, "apilayer", market_id, category))
    if not apilayer_jobs:
        for href in re.findall(r'href="(/[^"]+-api)(?:\?[^"]*)?"', listing, re.I):
            url = urljoin("https://marketplace.apilayer.com", href)
            if url not in seen:
                seen.add(url); apilayer_jobs.append((url, "apilayer", "", ""))

    sitemap = request("GET", "https://api-ninjas.com/sitemap.xml", headers={"Accept": "application/xml"}).text
    ninja_jobs = []
    for loc in re.findall(r"<loc>(.*?)</loc>", sitemap):
        if re.fullmatch(r"https://api-ninjas\.com/api/[^/?#]+", loc):
            ninja_jobs.append((loc, "api_ninjas", loc.rsplit("/", 1)[-1], ""))
    jobs = (apilayer_jobs + ninja_jobs)[:limit or None]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {}
        for url, market, market_id, category in jobs:
            cache = raw / market / f"{safe_name(market_id or url)}.json"
            if not should_fetch(cache, retry_errors):
                products.append(read_json(cache)); continue
            future = pool.submit(scrape_product, url, market, market_id, category)
            futures[future] = cache
        for idx, future in enumerate(as_completed(futures), 1):
            cache = futures[future]
            try:
                item = future.result()
            except Exception as exc:
                item = {"market": cache.parent.name, "error": str(exc), "fetched_at": utc_now()}
            atomic_json(cache, item); products.append(item)
            if delay: time.sleep(delay)
            if idx % 50 == 0 or idx == len(futures):
                print(f"competitors {idx}/{len(futures)}", flush=True)

    pricing = scrape_product("https://api-ninjas.com/pricing", "api_ninjas_pricing", "plans")
    atomic_json(raw / "api_ninjas" / "pricing.json", pricing)
    save_csv(root / "data_external" / "competitor_products.csv", products)

    apis = load_apis(root).fillna("")
    token_index: dict[str, set[int]] = defaultdict(set)
    product_tokens: list[set[str]] = []
    for idx, product in enumerate(products):
        value = " ".join([clean_text(product.get("product_title")), clean_text(product.get("product_slug")), clean_text(product.get("product_description"))])
        tok = tokens(value)
        product_tokens.append(tok)
        for token in tok:
            token_index[token].add(idx)
    matches = []
    for row in apis.to_dict("records"):
        api_text = " ".join([clean_text(row.get("api_title")), clean_text(row.get("api_name")), clean_text(row.get("api_slug")), clean_text(row.get("api_description"))])
        api_tokens = tokens(api_text)
        candidates: set[int] = set()
        for token in api_tokens:
            candidates.update(token_index.get(token, set()))
        scored = []
        for idx in candidates:
            product = products[idx]
            ptext = " ".join([clean_text(product.get("product_title")), clean_text(product.get("product_slug")), clean_text(product.get("product_description"))])
            score = similarity(api_text, ptext)
            if score >= 0.42:
                scored.append((score, idx))
        for rank, (score, idx) in enumerate(sorted(scored, reverse=True)[:5], 1):
            product = products[idx]
            matches.append({
                "api_id": row.get("api_id"), "match_rank": rank, "match_score": round(score, 6),
                "market": product.get("market"), "market_product_id": product.get("market_product_id"),
                "product_title": product.get("product_title"), "product_url": product.get("product_url"),
                "min_public_price_usd": product.get("min_public_price_usd"),
                "has_free_text": product.get("has_free_text"),
            })
    save_csv(
        root / "data_external" / "competitor_matches.csv", matches,
        ["api_id", "match_rank", "match_score", "market", "market_product_id", "product_title", "product_url", "min_public_price_usd", "has_free_text"],
    )


def registrable_domain(url: Any) -> str:
    text = clean_text(url)
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else "https://" + text)
    host = (parsed.hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if re.fullmatch(r"\d+(?:\.\d+){3}", host):
        return host
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    suffix2 = ".".join(parts[-2:])
    return ".".join(parts[-3:]) if suffix2 in MULTI_SUFFIX else suffix2


def rdap_summary(domain: str) -> dict[str, Any]:
    response = request("GET", f"https://rdap.org/domain/{quote(domain)}", timeout=45)
    body = response.json()
    events = {item.get("eventAction"): item.get("eventDate") for item in (body.get("events") or [])}
    registrar = ""
    for entity in body.get("entities") or []:
        if "registrar" in (entity.get("roles") or []):
            cards = entity.get("vcardArray") or []
            if len(cards) > 1:
                for card in cards[1]:
                    if card and card[0] == "fn": registrar = clean_text(card[-1])
    return {
        "domain": domain, "rdap_handle": body.get("handle"), "rdap_statuses_json": compact_json(body.get("status") or []),
        "domain_registration_date": events.get("registration"), "domain_expiration_date": events.get("expiration"),
        "domain_last_changed": events.get("last changed"), "domain_registrar": registrar,
        "domain_nameservers_json": compact_json([x.get("ldhName") for x in (body.get("nameservers") or []) if x.get("ldhName")]),
        "domain_dnssec": int(bool((body.get("secureDNS") or {}).get("delegationSigned"))),
        "rdap_source_url": response.url,
    }


def commoncrawl_summary(domain: str, endpoint: str) -> dict[str, Any]:
    response = request("GET", endpoint, params={"url": f"{domain}/*", "output": "json", "filter": "status:200", "collapse": "urlkey", "pageSize": 5, "showNumPages": "true"}, timeout=60)
    text = response.text.strip()
    pages = None
    sample_count = 0
    try:
        body = json.loads(text)
        if isinstance(body, dict): pages = body.get("pages")
        elif isinstance(body, list): sample_count = len(body)
    except Exception:
        sample_count = len([line for line in text.splitlines() if line.strip().startswith("{")])
    return {"commoncrawl_pages": pages, "commoncrawl_sample_count": sample_count, "commoncrawl_index_url": response.url}


def fetch_domain_one(root: Path, domain: str, website: str, cc_endpoint: str, retry_errors: bool, delay: float) -> dict[str, Any]:
    path = root / "external_raw" / "owners" / "domains" / f"{safe_name(domain)}.json"
    if not should_fetch(path, retry_errors):
        return read_json(path)
    payload: dict[str, Any] = {"domain": domain, "website_url": website, "fetched_at": utc_now()}
    errors = []
    try:
        response = request("GET", website, headers={"Accept": "text/html"}, timeout=30, attempts=2)
        page = response.text[:2_000_000]
        payload.update({
            "website_final_url": response.url, "website_http_status": response.status_code,
            "website_title": page_title(page), "website_description": meta_value(page, "description"),
            "website_language": (re.search(r'<html[^>]+lang=["\']([^"\']+)', page, re.I) or [None, ""])[1],
            "website_page_bytes": len(response.content),
        })
    except Exception as exc:
        errors.append(f"website:{exc}")
    try: payload.update(rdap_summary(domain))
    except Exception as exc: errors.append(f"rdap:{exc}")
    try: payload.update(commoncrawl_summary(domain, cc_endpoint))
    except Exception as exc: errors.append(f"commoncrawl:{exc}")
    tld = domain.rsplit(".", 1)[-1]
    payload["tld_country"] = CC_TLDS.get(tld, "")
    payload["error"] = "; ".join(errors)
    atomic_json(path, payload)
    if delay: time.sleep(delay)
    return payload


def fetch_lei_one(root: Path, owner: dict[str, Any], retry_errors: bool, delay: float) -> dict[str, Any]:
    owner_slug = str(owner.get("owner_slug") or owner.get("owner_id") or owner.get("owner_name"))
    path = root / "external_raw" / "owners" / "gleif" / f"{safe_name(owner_slug)}.json"
    if not should_fetch(path, retry_errors): return read_json(path)
    name = clean_text(owner.get("parent_org_name") or owner.get("owner_name"))
    payload: dict[str, Any] = {"owner_slug": owner_slug, "query_name": name, "fetched_at": utc_now(), "source_url": GLEIF_URL}
    try:
        if len(name) < 3: raise RuntimeError("owner_name_too_short")
        response = request("GET", GLEIF_URL, params={"filter[entity.legalName]": name, "page[size]": 5}, timeout=45)
        candidates = []
        for item in response.json().get("data") or []:
            attrs = item.get("attributes") or {}; entity = attrs.get("entity") or {}; registration = attrs.get("registration") or {}
            legal_name = clean_text((entity.get("legalName") or {}).get("name"))
            candidates.append({
                "lei": attrs.get("lei") or item.get("id"), "legal_name": legal_name,
                "match_score": round(similarity(name, legal_name), 6),
                "legal_country": (entity.get("legalAddress") or {}).get("country"),
                "headquarters_country": (entity.get("headquartersAddress") or {}).get("country"),
                "jurisdiction": entity.get("jurisdiction"), "entity_status": entity.get("status"),
                "registration_status": registration.get("status"), "initial_registration_date": registration.get("initialRegistrationDate"),
            })
        payload["candidates"] = sorted(candidates, key=lambda x: x["match_score"], reverse=True)
    except Exception as exc: payload["error"] = str(exc)
    atomic_json(path, payload)
    if delay: time.sleep(delay)
    return payload


def stage_owners(root: Path, workers: int, delay: float, retry_errors: bool, limit: int) -> None:
    apis = load_apis(root).fillna("")
    mapping = apis[["api_id", "owner_id", "owner_slug", "owner_name", "parent_org_name", "website_url"]].copy()
    mapping["domain"] = mapping["website_url"].map(registrable_domain)
    mapping = mapping[mapping["domain"] != ""]
    domain_first = mapping.drop_duplicates("domain")
    collections = request("GET", CC_COLLECTIONS_URL).json()
    cc_endpoint = collections[0]["cdx-api"]
    domain_jobs = domain_first.to_dict("records")[:limit or None]
    domain_results = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(fetch_domain_one, root, row["domain"], row["website_url"], cc_endpoint, retry_errors, delay) for row in domain_jobs]
        for idx, future in enumerate(as_completed(futures), 1):
            domain_results.append(future.result())
            if idx % 100 == 0 or idx == len(futures): print(f"owner_domains {idx}/{len(futures)}", flush=True)
    domain_df = pd.DataFrame(domain_results)
    owner_domain = mapping.merge(domain_df, on="domain", how="left", suffixes=("", "_domain"))
    save_csv(root / "data_external" / "owner_domain_enrichment.csv", owner_domain.to_dict("records"))

    owners = apis[["owner_id", "owner_slug", "owner_name", "parent_org_name"]].drop_duplicates("owner_slug").to_dict("records")[:limit or None]
    lei_results = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 3))) as pool:
        futures = [pool.submit(fetch_lei_one, root, owner, retry_errors, delay) for owner in owners]
        for idx, future in enumerate(as_completed(futures), 1):
            lei_results.append(future.result())
            if idx % 100 == 0 or idx == len(futures): print(f"owner_gleif {idx}/{len(futures)}", flush=True)
    candidates, summaries = [], []
    for item in lei_results:
        values = item.get("candidates") or []
        for rank, candidate in enumerate(values, 1): candidates.append({"owner_slug": item.get("owner_slug"), "lei_rank": rank, **candidate})
        best = values[0] if values else {}
        summaries.append({
            "owner_slug": item.get("owner_slug"), "lei": best.get("lei"), "lei_legal_name": best.get("legal_name"),
            "lei_match_score": best.get("match_score"), "lei_country": best.get("headquarters_country") or best.get("legal_country"),
            "lei_jurisdiction": best.get("jurisdiction"), "lei_entity_status": best.get("entity_status"),
            "lei_high_confidence": int(float(best.get("match_score") or 0) >= 0.82), "lei_error": item.get("error"),
        })
    save_csv(root / "data_external" / "owner_lei_candidates.csv", candidates, ["owner_slug", "lei_rank", "lei", "legal_name", "match_score", "legal_country", "headquarters_country", "jurisdiction", "entity_status", "registration_status", "initial_registration_date"])
    save_csv(root / "data_external" / "owner_legal_entity_summary.csv", summaries)


def parse_aws_prices(service: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    products = body.get("products") or {}; terms = (body.get("terms") or {}).get("OnDemand") or {}
    rows = []
    for sku, offers in terms.items():
        attrs = (products.get(sku) or {}).get("attributes") or {}
        signal = " ".join(clean_text(attrs.get(k)) for k in ["usagetype", "group", "groupDescription", "operation", "location"])
        if not re.search(r"request|duration|transfer|invocation|edge|gateway|lambda", signal, re.I): continue
        for offer in offers.values():
            for dimension in (offer.get("priceDimensions") or {}).values():
                price = (dimension.get("pricePerUnit") or {}).get("USD")
                rows.append({
                    "cloud_provider": "AWS", "service": service, "sku": sku,
                    "region": attrs.get("regionCode"), "location": attrs.get("location"),
                    "usage_type": attrs.get("usagetype"), "operation": attrs.get("operation"),
                    "group": attrs.get("group"), "description": dimension.get("description"),
                    "unit": dimension.get("unit"), "begin_range": dimension.get("beginRange"),
                    "end_range": dimension.get("endRange"), "price_usd": price,
                    "effective_date": offer.get("effectiveDate"),
                })
    return rows


def stage_macro(root: Path, retry_errors: bool) -> None:
    out = root / "data_external"; raw = root / "external_raw" / "macro"
    raw.mkdir(parents=True, exist_ok=True)
    response = request("GET", OECD_DSTRI_URL, headers={"Accept": "text/csv"}, timeout=90)
    (raw / "oecd_dstri.csv").write_bytes(response.content)
    dstri = pd.read_csv(raw / "oecd_dstri.csv")
    dstri["source_url"] = OECD_DSTRI_URL; dstri["fetched_at"] = utc_now()
    dstri.to_csv(out / "oecd_digital_stri.csv", index=False)

    wb_rows = []
    for indicator, label in WORLD_BANK_INDICATORS.items():
        url = f"https://api.worldbank.org/v2/country/all/indicator/{indicator}"
        body = request("GET", url, params={"format": "json", "per_page": 20000}, timeout=90).json()
        for item in (body[1] if isinstance(body, list) and len(body) > 1 else []):
            wb_rows.append({
                "country_iso3": item.get("countryiso3code"), "country_name": (item.get("country") or {}).get("value"),
                "year": item.get("date"), "indicator_code": indicator, "indicator_name": label,
                "value": item.get("value"), "source_url": url,
            })
    save_csv(out / "world_bank_digital_macro.csv", wb_rows)

    country_url = "https://api.worldbank.org/v2/country"
    country_body = request("GET", country_url, params={"format": "json", "per_page": 400}, timeout=90).json()
    country_rows = []
    for item in (country_body[1] if isinstance(country_body, list) and len(country_body) > 1 else []):
        country_rows.append({
            "country_iso2": item.get("iso2Code"), "country_iso3": item.get("id"),
            "country_name": item.get("name"), "region": (item.get("region") or {}).get("value"),
            "income_level": (item.get("incomeLevel") or {}).get("value"), "source_url": country_url,
        })
    save_csv(out / "world_bank_country_codes.csv", country_rows)

    cloud_rows = []
    for service, url in AWS_PRICE_URLS.items():
        path = raw / f"aws_{service}.json"
        if not path.exists() or retry_errors:
            response = request("GET", url, timeout=180)
            path.write_bytes(response.content)
        cloud_rows.extend(parse_aws_prices(service, read_json(path)))
    save_csv(out / "cloud_api_costs.csv", cloud_rows)


def merge_if_exists(frame: pd.DataFrame, path: Path, key: str = "api_id") -> pd.DataFrame:
    right = read_csv_optional(path)
    if right.empty: return frame
    if key not in right: return frame
    right[key] = right[key].astype(str); frame[key] = frame[key].astype(str)
    right = right.drop_duplicates(key)
    return frame.merge(right, on=key, how="left", suffixes=("", "_external"))


def stage_build(root: Path) -> None:
    out = root / "data_external"; apis = load_apis(root)
    base_cols = [c for c in ["api_id", "api_slug", "api_title", "owner_id", "owner_slug", "primary_type", "subscriptions_count", "website_url"] if c in apis]
    panel = apis[base_cols].copy(); panel["api_id"] = panel["api_id"].astype(str)
    for name in ["external_api_adoption.csv", "external_open_substitutes.csv", "api_schema_replicability.csv", "api_response_fingerprints.csv"]:
        panel = merge_if_exists(panel, out / name)
    matches_path = out / "competitor_matches.csv"
    if matches_path.exists() and matches_path.stat().st_size:
        matches = read_csv_optional(matches_path)
        if matches.empty or "api_id" not in matches:
            matches = pd.DataFrame()
    else:
        matches = pd.DataFrame()
    if not matches.empty:
        matches["api_id"] = matches["api_id"].astype(str)
        comp = matches.groupby("api_id").agg(
            competitor_match_count=("match_score", "size"), competitor_best_match_score=("match_score", "max"),
            competitor_platform_count=("market", "nunique"),
        ).reset_index()
        panel = panel.merge(comp, on="api_id", how="left")
    domain_path = out / "owner_domain_enrichment.csv"
    if domain_path.exists() and domain_path.stat().st_size:
        domain = read_csv_optional(domain_path)
        if not domain.empty:
            domain = domain.sort_values("api_id").drop_duplicates("api_id")
    else:
        domain = pd.DataFrame()
    if not domain.empty:
        keep = [c for c in ["api_id", "domain", "domain_registration_date", "domain_last_changed", "domain_registrar", "domain_dnssec", "website_http_status", "website_title", "website_language", "website_page_bytes", "commoncrawl_pages", "commoncrawl_sample_count", "tld_country"] if c in domain]
        domain["api_id"] = domain["api_id"].astype(str)
        panel = panel.merge(domain[keep], on="api_id", how="left")
    lei_path = out / "owner_legal_entity_summary.csv"
    if lei_path.exists() and lei_path.stat().st_size:
        lei = read_csv_optional(lei_path)
    else:
        lei = pd.DataFrame()
    if not lei.empty:
        panel = panel.merge(lei, on="owner_slug", how="left")
    if "lei_country" in panel:
        confidence = panel["lei_high_confidence"].fillna(0) if "lei_high_confidence" in panel else pd.Series(0, index=panel.index)
        fallback = panel["tld_country"] if "tld_country" in panel else pd.Series("", index=panel.index)
        panel["owner_country"] = panel["lei_country"].where(confidence.eq(1), fallback)
    elif "tld_country" in panel: panel["owner_country"] = panel["tld_country"]

    country_path = out / "world_bank_country_codes.csv"
    if country_path.exists() and "owner_country" in panel:
        countries = read_csv_optional(country_path)
        if not countries.empty:
            crosswalk = countries.dropna(subset=["country_iso2", "country_iso3"]).drop_duplicates("country_iso2").set_index("country_iso2")["country_iso3"]
            owner_country = panel["owner_country"].fillna("").astype(str).str.upper()
            panel["owner_country_iso3"] = owner_country.where(owner_country.str.len().eq(3), owner_country.map(crosswalk))

    dstri_path = out / "oecd_digital_stri.csv"
    if dstri_path.exists() and "owner_country_iso3" in panel:
        dstri = pd.read_csv(dstri_path, low_memory=False)
        dstri = dstri[dstri["MEASURE"].eq("STRI")].sort_values("TIME_PERIOD").drop_duplicates("REF_AREA", keep="last")
        dstri = dstri[["REF_AREA", "TIME_PERIOD", "OBS_VALUE"]].rename(columns={"REF_AREA": "owner_country_iso3", "TIME_PERIOD": "dstri_year", "OBS_VALUE": "digital_stri"})
        panel["owner_country_iso3"] = panel["owner_country_iso3"].fillna("").astype(str)
        dstri["owner_country_iso3"] = dstri["owner_country_iso3"].fillna("").astype(str)
        panel = panel.merge(dstri, on="owner_country_iso3", how="left")

    wb_path = out / "world_bank_digital_macro.csv"
    if wb_path.exists() and "owner_country_iso3" in panel:
        wb = read_csv_optional(wb_path)
        if not wb.empty:
            wb["year_num"] = pd.to_numeric(wb["year"], errors="coerce")
            wb["value_num"] = pd.to_numeric(wb["value"], errors="coerce")
            latest = wb.dropna(subset=["country_iso3", "indicator_name", "value_num"]).sort_values("year_num").drop_duplicates(["country_iso3", "indicator_name"], keep="last")
            latest = latest.pivot(index="country_iso3", columns="indicator_name", values="value_num").reset_index().rename(columns={"country_iso3": "owner_country_iso3"})
            panel = panel.merge(latest, on="owner_country_iso3", how="left")
    panel.to_csv(out / "rapidapi_external_enriched_panel.csv", index=False)

    coverage = []
    variables = {
        "api_host": "valid RapidAPI public DNS host",
        "github_repository_count": "external GitHub repository adoption",
        "open_best_score": "open-data substitute similarity",
        "schema_overlap_best": "within-market endpoint/schema replicability",
        "competitor_best_match_score": "cross-platform product match",
        "domain": "owner registrable web domain",
        "owner_country": "high-confidence LEI country or country-code TLD",
        "owner_country_iso3": "ISO alpha-3 owner country used for international merges",
        "digital_stri": "latest OECD Digital STRI score",
    }
    for variable, meaning in variables.items():
        if variable not in panel: continue
        nonmissing = panel[variable].notna() & panel[variable].astype(str).ne("")
        coverage.append({"variable": variable, "meaning": meaning, "nonmissing_n": int(nonmissing.sum()), "coverage_share": float(nonmissing.mean()), "total_apis": len(panel)})
    save_csv(out / "external_coverage_report.csv", coverage)
    atomic_json(out / "external_coverage_report.json", {"generated_at": utc_now(), "api_rows": len(panel), "coverage": coverage})
    dictionary = [
        {"variable": row["variable"], "definition": row["meaning"], "unit": "count/index/indicator as named", "source": "See source-specific table and URL columns", "merge_key": "api_id or owner_slug"}
        for row in coverage
    ]
    save_csv(out / "external_variable_dictionary.csv", dictionary)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("rapidapi_crawl"))
    parser.add_argument("--stages", default="adoption,open_substitutes,schema_overlap,response_samples,competitors,owners,macro,build")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    stages = [x.strip() for x in args.stages.split(",") if x.strip()]
    for stage in stages:
        print(f"===== {utc_now()} stage={stage} =====", flush=True)
        if stage == "adoption": stage_adoption(args.root, args.workers, args.delay, args.retry_errors, args.limit)
        elif stage == "open_substitutes": stage_open_substitutes(args.root, args.workers, args.delay, args.retry_errors, args.limit)
        elif stage == "schema_overlap": stage_schema_overlap(args.root, args.limit)
        elif stage == "response_samples": stage_response_samples(args.root, args.workers, args.delay, args.retry_errors, args.limit)
        elif stage == "competitors": stage_competitors(args.root, args.workers, args.delay, args.retry_errors, args.limit)
        elif stage == "owners": stage_owners(args.root, args.workers, args.delay, args.retry_errors, args.limit)
        elif stage == "macro": stage_macro(args.root, args.retry_errors)
        elif stage == "build": stage_build(args.root)
        else: raise ValueError(f"unknown stage: {stage}")


if __name__ == "__main__":
    main()

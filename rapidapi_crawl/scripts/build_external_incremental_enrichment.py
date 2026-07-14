#!/usr/bin/env python3
"""Enrich only the API ids produced by one weekly incremental run.

The script reuses the global external raw cache, never rewrites the baseline
external panel, and writes run-scoped tables under ``external_incremental``.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd

import external_research_enrichment as ext


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or not path.stat().st_size:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def adoption(root: Path, apis: pd.DataFrame, out: Path, workers: int, delay: float) -> None:
    rows = apis.to_dict("records")
    results: list[dict[str, Any]] = []
    pending = []
    for row in rows:
        path = root / "external_raw" / "sourcegraph" / f"{ext.safe_name(row['api_id'])}.json"
        if ext.should_fetch(path, True):
            pending.append(row)
        else:
            results.append(ext.read_json(path))
    batch_size = max(4, int(os.environ.get("SOURCEGRAPH_BATCH_SIZE", "12")))
    batches = [pending[i:i + batch_size] for i in range(0, len(pending), batch_size)]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(ext.fetch_sourcegraph_batch, root, batch, delay) for batch in batches]
        for future in as_completed(futures):
            results.extend(future.result())
    api_rows, repo_rows = [], []
    for item in results:
        api_rows.append({
            "api_id": item.get("api_id"), "api_host": item.get("api_host"),
            "github_code_match_count": item.get("match_count"),
            "github_repository_count": item.get("repository_count"),
            "github_matched_file_count": item.get("matched_file_count"),
            "github_repo_star_sum": item.get("repo_star_sum"),
            "github_repo_star_max": item.get("repo_star_max"),
            "github_languages_json": ext.compact_json(item.get("languages") or {}),
            "github_result_truncated": item.get("result_truncated"),
            "github_search_error": item.get("error"),
            "github_fetched_at": item.get("fetched_at"),
        })
        for repo in item.get("repositories") or []:
            repo_rows.append({"api_id": item.get("api_id"), "api_host": item.get("api_host"), **repo})
    ext.save_csv(out / "external_api_adoption_incremental.csv", api_rows)
    ext.save_csv(out / "external_code_repositories_incremental.csv", repo_rows, ["api_id", "api_host", "repository", "repo_stars", "repo_last_fetched", "commit"])


def open_substitutes(root: Path, apis: pd.DataFrame, out: Path) -> None:
    catalog: dict[tuple[str, str], dict[str, Any]] = {}
    raw = root / "external_raw" / "open_data_catalog"
    for source_dir in raw.iterdir() if raw.exists() else []:
        if not source_dir.is_dir():
            continue
        for path in source_dir.glob("*.json"):
            data = ext.read_json(path)
            if data.get("error"):
                continue
            for candidate in data.get("candidates") or []:
                key = (
                    data.get("source", source_dir.name),
                    candidate.get("candidate_id") or candidate.get("candidate_url") or candidate.get("candidate_title"),
                )
                catalog[key] = {"open_source": data.get("source", source_dir.name), **candidate}
    candidates = list(catalog.values())
    candidate_texts, inverted = [], defaultdict(set)
    for idx, candidate in enumerate(candidates):
        text = " ".join([candidate.get("candidate_title", ""), candidate.get("candidate_description", ""), candidate.get("candidate_keywords", "")])
        candidate_texts.append(text)
        for token in ext.tokens(text):
            inverted[token].add(idx)
    candidate_rows, summaries = [], []
    for api in apis.to_dict("records"):
        api_id = str(api["api_id"])
        api_text = " ".join([ext.clean_text(api.get("api_title")), ext.clean_text(api.get("api_description")), ext.clean_text(api.get("api_slug"))])
        shortlist: set[int] = set()
        for token in ext.tokens(api_text):
            shortlist.update(inverted.get(token, set()))
        scored = []
        for idx in shortlist:
            score = ext.similarity(api_text, candidate_texts[idx])
            if score >= 0.12:
                scored.append((score, idx))
        matched = []
        for rank, (score, idx) in enumerate(sorted(scored, reverse=True)[:12], 1):
            row = {"api_id": api_id, "candidate_rank": rank, **candidates[idx], "match_score": round(score, 6)}
            candidate_rows.append(row); matched.append(row)
        best = matched[0] if matched else {}
        scores = [float(row["match_score"]) for row in matched]
        summaries.append({
            "api_id": api_id, "open_candidate_count": len(matched),
            "open_match_count_030": sum(score >= 0.30 for score in scores),
            "open_match_count_045": sum(score >= 0.45 for score in scores),
            "open_best_score": max(scores, default=0), "open_best_source": best.get("open_source"),
            "open_best_title": best.get("candidate_title"), "open_best_url": best.get("candidate_url"),
            "open_substitute_indicator": int(max(scores, default=0) >= 0.45),
        })
    ext.save_csv(out / "open_data_candidates_incremental.csv", candidate_rows)
    ext.save_csv(out / "external_open_substitutes_incremental.csv", summaries)


def token_sets(endpoint: pd.DataFrame) -> dict[str, set[str]]:
    fields = ["route", "endpoint_name", "param_names_json", "payload_names_json", "payload_types", "payload_formats", "payload_status_codes"]
    result: dict[str, set[str]] = defaultdict(set)
    for field in fields:
        if field not in endpoint:
            endpoint[field] = ""
    for row in endpoint[["api_id", *fields]].fillna("").itertuples(index=False, name=None):
        api_id = str(row[0])
        for value in row[1:]:
            result[api_id].update(ext.tokens(value))
    return result


def schema_overlap(root: Path, run_dir: Path, apis: pd.DataFrame, out: Path) -> None:
    baseline_apis = read_csv(root / "data_merged" / "rapidapi_merged_api_master.csv")
    all_apis = pd.concat([baseline_apis, apis], ignore_index=True, sort=False).drop_duplicates("api_id", keep="last")
    baseline_endpoints = read_csv(root / "data_merged" / "rapidapi_merged_endpoint_schema.csv")
    new_endpoints = read_csv(run_dir / "rapidapi_merged_endpoint_schema.csv")
    all_endpoints = pd.concat([baseline_endpoints, new_endpoints], ignore_index=True, sort=False)
    sets = token_sets(all_endpoints)
    market = all_apis.set_index(all_apis["api_id"].astype(str))["primary_type"].to_dict()
    by_market: dict[str, list[str]] = defaultdict(list)
    for api_id, kind in market.items():
        by_market[str(kind)].append(str(api_id))
    summaries, pairs = [], []
    for api_id in apis["api_id"].astype(str):
        values = []
        left = sets.get(api_id, set())
        for rival in by_market.get(str(market.get(api_id)), []):
            if rival == api_id:
                continue
            right = sets.get(rival, set())
            shared = len(left & right)
            if shared < 2 or not left or not right:
                continue
            score = shared / len(left | right)
            if score >= 0.08:
                values.append((score, rival, shared))
        values.sort(reverse=True)
        for score, rival, shared in values[:20]:
            pairs.append({"api_id": api_id, "rival_api_id": rival, "primary_type": market.get(api_id), "schema_jaccard": score, "shared_tokens": shared})
        summaries.append({
            "api_id": api_id, "schema_overlap_best": values[0][0] if values else 0,
            "schema_overlap_mean_top5": sum(x[0] for x in values[:5]) / min(5, len(values)) if values else 0,
            "schema_near_substitutes_020": sum(x[0] >= 0.20 for x in values),
            "schema_best_match_api_id": values[0][1] if values else "", "schema_token_count": len(left),
        })
    ext.save_csv(out / "schema_overlap_pairs_incremental.csv", pairs)
    ext.save_csv(out / "api_schema_replicability_incremental.csv", summaries)


def competitor_matches(root: Path, apis: pd.DataFrame, out: Path) -> None:
    products = read_csv(root / "data_external" / "competitor_products.csv").fillna("")
    token_index: dict[str, set[int]] = defaultdict(set)
    texts = []
    for idx, product in enumerate(products.to_dict("records")):
        text = " ".join([ext.clean_text(product.get("product_title")), ext.clean_text(product.get("product_slug")), ext.clean_text(product.get("product_description"))])
        texts.append(text)
        for token in ext.tokens(text): token_index[token].add(idx)
    rows = products.to_dict("records"); matches = []
    for api in apis.to_dict("records"):
        text = " ".join([ext.clean_text(api.get("api_title")), ext.clean_text(api.get("api_name")), ext.clean_text(api.get("api_slug")), ext.clean_text(api.get("api_description"))])
        candidate_ids: set[int] = set()
        for token in ext.tokens(text): candidate_ids.update(token_index.get(token, set()))
        scored = [(ext.similarity(text, texts[idx]), idx) for idx in candidate_ids]
        for rank, (score, idx) in enumerate(sorted((x for x in scored if x[0] >= 0.42), reverse=True)[:5], 1):
            product = rows[idx]
            matches.append({
                "api_id": api.get("api_id"), "match_rank": rank, "match_score": score,
                "market": product.get("market"), "market_product_id": product.get("market_product_id"),
                "product_title": product.get("product_title"), "product_url": product.get("product_url"),
                "min_public_price_usd": product.get("min_public_price_usd"), "has_free_text": product.get("has_free_text"),
            })
    ext.save_csv(out / "competitor_matches_incremental.csv", matches, ["api_id", "match_rank", "match_score", "market", "market_product_id", "product_title", "product_url", "min_public_price_usd", "has_free_text"])


def owners(root: Path, apis: pd.DataFrame, out: Path, workers: int, delay: float) -> None:
    mapping = apis[[c for c in ["api_id", "owner_id", "owner_slug", "owner_name", "parent_org_name", "website_url"] if c in apis]].copy()
    mapping["domain"] = mapping.get("website_url", pd.Series("", index=mapping.index)).map(ext.registrable_domain)
    domain_first = mapping[mapping["domain"] != ""].drop_duplicates("domain")
    cc_endpoint = ext.request("GET", ext.CC_COLLECTIONS_URL).json()[0]["cdx-api"]
    domain_results = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(ext.fetch_domain_one, root, row["domain"], row.get("website_url", ""), cc_endpoint, True, delay) for row in domain_first.to_dict("records")]
        for future in as_completed(futures): domain_results.append(future.result())
    domain_df = pd.DataFrame(domain_results)
    owner_domain = mapping.merge(domain_df, on="domain", how="left", suffixes=("", "_domain")) if not domain_df.empty else mapping
    owner_domain.to_csv(out / "owner_domain_enrichment_incremental.csv", index=False)

    owner_rows = apis[[c for c in ["owner_id", "owner_slug", "owner_name", "parent_org_name"] if c in apis]].drop_duplicates("owner_slug").to_dict("records")
    lei_results = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 3))) as pool:
        futures = [pool.submit(ext.fetch_lei_one, root, row, True, delay) for row in owner_rows]
        for future in as_completed(futures): lei_results.append(future.result())
    summaries = []
    for item in lei_results:
        values = item.get("candidates") or []; best = values[0] if values else {}
        summaries.append({
            "owner_slug": item.get("owner_slug"), "lei": best.get("lei"), "lei_legal_name": best.get("legal_name"),
            "lei_match_score": best.get("match_score"), "lei_country": best.get("headquarters_country") or best.get("legal_country"),
            "lei_jurisdiction": best.get("jurisdiction"), "lei_entity_status": best.get("entity_status"),
            "lei_high_confidence": int(float(best.get("match_score") or 0) >= 0.82), "lei_error": item.get("error"),
        })
    ext.save_csv(out / "owner_legal_entity_incremental.csv", summaries)


def build(root: Path, run_dir: Path, apis: pd.DataFrame, out: Path, run_id: str) -> None:
    panel = apis.copy(); panel["api_id"] = panel["api_id"].astype(str)
    for name in ["external_api_adoption_incremental.csv", "external_open_substitutes_incremental.csv", "api_schema_replicability_incremental.csv"]:
        right = read_csv(out / name)
        if not right.empty:
            right["api_id"] = right["api_id"].astype(str); panel = panel.merge(right.drop_duplicates("api_id"), on="api_id", how="left")
    matches = read_csv(out / "competitor_matches_incremental.csv")
    if not matches.empty:
        matches["api_id"] = matches["api_id"].astype(str)
        comp = matches.groupby("api_id").agg(competitor_match_count=("match_score", "size"), competitor_best_match_score=("match_score", "max"), competitor_platform_count=("market", "nunique")).reset_index()
        panel = panel.merge(comp, on="api_id", how="left")
    domain = read_csv(out / "owner_domain_enrichment_incremental.csv")
    if not domain.empty:
        domain["api_id"] = domain["api_id"].astype(str)
        keep = [c for c in ["api_id", "domain", "domain_registration_date", "domain_last_changed", "domain_registrar", "domain_dnssec", "website_http_status", "website_title", "website_language", "website_page_bytes", "commoncrawl_pages", "commoncrawl_sample_count", "tld_country"] if c in domain]
        panel = panel.merge(domain[keep].drop_duplicates("api_id"), on="api_id", how="left")
    lei = read_csv(out / "owner_legal_entity_incremental.csv")
    if not lei.empty: panel = panel.merge(lei.drop_duplicates("owner_slug"), on="owner_slug", how="left")
    confidence = panel.get("lei_high_confidence", pd.Series(0, index=panel.index)).fillna(0)
    fallback = panel.get("tld_country", pd.Series("", index=panel.index))
    panel["owner_country"] = panel.get("lei_country", pd.Series("", index=panel.index)).where(confidence.eq(1), fallback)
    countries = read_csv(root / "data_external" / "world_bank_country_codes.csv")
    if not countries.empty:
        crosswalk = countries.dropna(subset=["country_iso2", "country_iso3"]).drop_duplicates("country_iso2").set_index("country_iso2")["country_iso3"]
        country = panel["owner_country"].fillna("").astype(str).str.upper()
        panel["owner_country_iso3"] = country.where(country.str.len().eq(3), country.map(crosswalk))
    dstri = read_csv(root / "data_external" / "oecd_digital_stri.csv")
    if not dstri.empty:
        dstri = dstri[dstri["MEASURE"].eq("STRI")].sort_values("TIME_PERIOD").drop_duplicates("REF_AREA", keep="last")[["REF_AREA", "TIME_PERIOD", "OBS_VALUE"]]
        dstri.columns = ["owner_country_iso3", "dstri_year", "digital_stri"]
        panel = panel.merge(dstri, on="owner_country_iso3", how="left")
    wb = read_csv(root / "data_external" / "world_bank_digital_macro.csv")
    if not wb.empty:
        wb["year_num"] = pd.to_numeric(wb["year"], errors="coerce"); wb["value_num"] = pd.to_numeric(wb["value"], errors="coerce")
        latest = wb.dropna(subset=["country_iso3", "indicator_name", "value_num"]).sort_values("year_num").drop_duplicates(["country_iso3", "indicator_name"], keep="last").pivot(index="country_iso3", columns="indicator_name", values="value_num").reset_index().rename(columns={"country_iso3": "owner_country_iso3"})
        panel = panel.merge(latest, on="owner_country_iso3", how="left")
    panel.insert(0, "incremental_run_id", run_id)
    panel.to_csv(out / "rapidapi_external_incremental_panel.csv", index=False)

    manifest = {
        "run_id": run_id, "generated_at": ext.utc_now(), "new_api_rows": len(panel),
        "api_id_duplicates": int(panel.duplicated("api_id").sum()),
        "github_covered": int(panel.get("github_repository_count", pd.Series(dtype=float)).notna().sum()),
        "open_substitute_covered": int(panel.get("open_best_score", pd.Series(dtype=float)).notna().sum()),
        "domain_covered": int(panel.get("domain", pd.Series(dtype=float)).notna().sum()),
        "owner_country_covered": int(panel.get("owner_country_iso3", pd.Series(dtype=float)).notna().sum()),
        "output": str(out / "rapidapi_external_incremental_panel.csv"),
    }
    ext.atomic_json(out / "external_incremental_manifest.json", manifest)

    index_path = root / "data_external" / "external_incremental_run_index.csv"
    index = read_csv(index_path)
    row = pd.DataFrame([manifest])
    index = pd.concat([index, row], ignore_index=True, sort=False).drop_duplicates("run_id", keep="last") if not index.empty else row
    index.to_csv(index_path, index=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("rapidapi_crawl"))
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--delay", type=float, default=0.75)
    args = parser.parse_args()
    apis = read_csv(args.run_dir / "rapidapi_merged_api_master.csv")
    out = args.run_dir / "external_incremental"; out.mkdir(parents=True, exist_ok=True)
    if apis.empty or "api_id" not in apis:
        ext.atomic_json(out / "external_incremental_manifest.json", {"run_id": args.run_id, "generated_at": ext.utc_now(), "new_api_rows": 0, "state": "no_new_api_rows"})
        return
    apis["api_id"] = apis["api_id"].astype(str)
    adoption(args.root, apis, out, args.workers, args.delay)
    open_substitutes(args.root, apis, out)
    schema_overlap(args.root, args.run_dir, apis, out)
    competitor_matches(args.root, apis, out)
    owners(args.root, apis, out, args.workers, args.delay)
    build(args.root, args.run_dir, apis, out, args.run_id)


if __name__ == "__main__":
    main()

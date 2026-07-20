#!/usr/bin/env python3
"""Promote a validated weekly delta into the static research baseline.

The weekly crawler intentionally writes run-scoped delta files.  This module
performs the separate, conservative promotion step: it validates keys, merges
new rows, recomputes all sample-standardized economic features on the joint
universe, refreshes API features embedded in listing/search tables, and then
atomically replaces the baseline bundle.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


MARKET_TYPES = {
    "web_scraping": ["scraper", "scrape", "scraping", "crawler", "crawl", "extractor", "extraction", "parse", "parser"],
    "social_profile": ["linkedin", "instagram", "tiktok", "twitter", "x.com", "youtube", "facebook", "reddit", "telegram", "social"],
    "geo_identity": ["geolocation", "location", "address", "postcode", "zipcode", "maps", "places", "ip geo", "whois", "phone", "email"],
    "firm_lead": ["company", "business", "lead", "apollo", "email finder", "enrich", "b2b", "firmographic"],
    "finance_market": ["stock", "crypto", "forex", "finance", "trading", "ticker", "market data"],
    "ecommerce_price": ["amazon", "ebay", "shopify", "product", "price tracker", "reviews", "walmart", "store"],
    "document_text": ["pdf", "ocr", "document", "invoice", "image", "text extraction", "sentiment", "nlp"],
    "real_estate_mobility": ["real estate", "property", "zillow", "realtor", "apartments", "airbnb", "hotel", "flight", "travel"],
    "public_reference": ["country", "city", "state", "population", "census", "statistics", "public data"],
}

CORE_TABLES = {
    "rapidapi_merged_api_master.csv": ["api_id"],
    "rapidapi_merged_plan_contracts.csv": ["api_id", "plan_id", "version_id"],
    "rapidapi_merged_endpoint_schema.csv": ["api_id", "endpoint_id"],
    "rapidapi_merged_search_exposure.csv": ["query_id", "replica_index", "search_rank", "api_id"],
    "rapidapi_merged_marketplace_listings.csv": ["listing_source", "rank", "page", "api_id"],
}

CORE_REQUIRED_KEYS = {
    "rapidapi_merged_api_master.csv": ["api_id"],
    "rapidapi_merged_plan_contracts.csv": ["api_id", "plan_id"],
    "rapidapi_merged_endpoint_schema.csv": ["api_id", "endpoint_id"],
    "rapidapi_merged_search_exposure.csv": ["query_id", "replica_index", "search_rank", "api_id"],
    "rapidapi_merged_marketplace_listings.csv": ["listing_source", "rank", "page", "api_id"],
}

STRICT_SOURCE_TABLES = {
    "rapidapi_merged_api_master.csv",
    "rapidapi_merged_plan_contracts.csv",
    "rapidapi_merged_endpoint_schema.csv",
}

EXTERNAL_TABLES = {
    "rapidapi_external_enriched_panel.csv": ("rapidapi_external_incremental_panel.csv", ["api_id"]),
    "external_code_repositories.csv": ("external_code_repositories_incremental.csv", ["api_id", "repository", "commit"]),
    "external_open_substitutes.csv": ("external_open_substitutes_incremental.csv", ["api_id"]),
    "api_schema_replicability.csv": ("api_schema_replicability_incremental.csv", ["api_id"]),
    "schema_overlap_pairs.csv": ("schema_overlap_pairs_incremental.csv", ["api_id_left", "api_id_right"]),
    "competitor_matches.csv": ("competitor_matches_incremental.csv", ["api_id", "market", "market_product_id"]),
    "owner_domain_enrichment.csv": ("owner_domain_enrichment_incremental.csv", ["api_id"]),
    "owner_legal_entity_summary.csv": ("owner_legal_entity_incremental.csv", ["owner_slug"]),
    "external_api_adoption.csv": ("external_api_adoption_incremental.csv", ["api_id"]),
    "open_data_candidates.csv": ("open_data_candidates_incremental.csv", ["api_id", "open_source", "candidate_key"]),
}

EXTERNAL_REQUIRED_KEYS = {
    "rapidapi_external_enriched_panel.csv": ["api_id"],
    "external_code_repositories.csv": ["api_id", "repository"],
    "external_open_substitutes.csv": ["api_id"],
    "api_schema_replicability.csv": ["api_id"],
    "schema_overlap_pairs.csv": ["api_id_left", "api_id_right"],
    "competitor_matches.csv": ["api_id", "market", "market_product_id"],
    "owner_domain_enrichment.csv": ["api_id"],
    "owner_legal_entity_summary.csv": ["owner_slug"],
    "external_api_adoption.csv": ["api_id"],
    "open_data_candidates.csv": ["api_id", "open_source", "candidate_key"],
}

STALE_STRUCTURAL_COLUMNS = {
    "delta0_calibrated",
    "markup_100",
    "markup_usd",
    "mc_100",
    "mc_usd",
    "own_elasticity",
    "mc_usd_floored",
    "mc_100_floored",
    "lerner_index",
}

CRITICAL_FEATURES = [
    "primary_type",
    "has_free_plan",
    "min_paid_price",
    "ln_free_quota",
    "ln_max_paid_quota",
    "data_scope_index",
    "data_complexity_index",
    "disclosure_index",
    "reliability_index",
    "ln_public_plan_count",
    "versioning_index",
    "uncertainty_index",
    "restricted_access_index",
    "z_owner_other_market_price",
    "z_contract_metering",
]


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def series(frame: pd.DataFrame, column: str, default: object = np.nan) -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series(default, index=frame.index)


def numeric(values: pd.Series, fill: float | None = None) -> pd.Series:
    out = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
    return out.fillna(fill) if fill is not None else out


def boolean(values: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(False).astype(int)
    mapped = values.astype("string").str.strip().str.lower().map(
        {"true": 1, "false": 0, "1": 1, "0": 0, "yes": 1, "no": 0}
    )
    return mapped.fillna(0).astype(int)


def ln1p(values: pd.Series) -> pd.Series:
    return np.log1p(numeric(values, 0).clip(lower=0))


def zscore(values: pd.Series) -> pd.Series:
    out = numeric(values, 0)
    sd = float(out.std())
    return (out - float(out.mean())) / sd if np.isfinite(sd) and sd > 0 else out * 0


def winsor(values: pd.Series, lower: float = 0.0, upper: float = 0.99) -> pd.Series:
    out = numeric(values)
    valid = out.dropna()
    if valid.empty:
        return out
    return out.clip(valid.quantile(lower), valid.quantile(upper))


def nonempty_text(values: pd.Series) -> pd.Series:
    out = values.astype("string").str.strip()
    return out.mask(out.str.lower().isin(["", "nan", "none", "<na>"]))


def first_nonempty(frame: pd.DataFrame, columns: Iterable[str], default: object = np.nan) -> pd.Series:
    out = pd.Series(default, index=frame.index, dtype="object")
    for column in columns:
        if column in frame.columns:
            out = out.where(out.notna(), nonempty_text(frame[column]))
    return out


def classify_market(row: pd.Series) -> str:
    text = " ".join(
        str(row.get(column, "") or "")
        for column in ["api_name", "api_title", "api_slug", "api_description", "category", "pricing"]
    ).lower()
    for market, patterns in MARKET_TYPES.items():
        if any(pattern in text for pattern in patterns):
            return market
    return "other"


def valid_key_rows(frame: pd.DataFrame, keys: list[str]) -> pd.Series:
    keep = pd.Series(True, index=frame.index)
    for key in keys:
        if key not in frame.columns:
            return pd.Series(False, index=frame.index)
        keep &= nonempty_text(frame[key]).notna()
    return keep


def merge_new_rows(
    base: pd.DataFrame,
    delta: pd.DataFrame,
    keys: list[str],
    required_keys: list[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    required_keys = required_keys or keys
    before = len(base)
    delta_input = len(delta)
    invalid_base = int((~valid_key_rows(base, required_keys)).sum()) if not base.empty else 0
    invalid_delta = int((~valid_key_rows(delta, required_keys)).sum()) if not delta.empty else 0
    base = base.loc[valid_key_rows(base, required_keys)].copy() if not base.empty else base
    delta = delta.loc[valid_key_rows(delta, required_keys)].copy() if not delta.empty else delta
    if base.empty:
        combined = delta.reset_index(drop=True)
    elif delta.empty:
        combined = base.reset_index(drop=True)
    else:
        combined = pd.concat([base, delta], ignore_index=True, sort=False)
    duplicate_rows_before = int(combined.duplicated(keys, keep=False).sum()) if not combined.empty else 0
    combined = combined.drop_duplicates(keys, keep="last").reset_index(drop=True)
    return combined, {
        "before_rows": before,
        "delta_input_rows": delta_input,
        "invalid_base_key_rows": invalid_base,
        "invalid_delta_key_rows": invalid_delta,
        "duplicate_rows_before_resolution": duplicate_rows_before,
        "after_rows": len(combined),
        "net_new_rows": len(combined) - before,
    }


def build_menu_features(plans: pd.DataFrame) -> pd.DataFrame:
    frame = plans.copy()
    bool_columns = [
        "is_public_plan",
        "is_hidden_plan",
        "is_free_plan",
        "is_paid_plan",
        "requires_approval",
        "is_recommended_plan",
        "has_unlimited_limit",
        "rateLimit_enabled",
    ]
    numeric_columns = [
        "plan_monthly_price",
        "max_quota_amount",
        "min_quota_amount",
        "max_overage_price",
        "mean_overage_price",
        "hard_limits_n",
        "soft_limits_n",
        "all_endpoint_limits_n",
        "plan_mapped_endpoints_count",
        "plan_all_endpoint_items_count",
        "rate_limit_amount",
        "limits_n",
        "finite_limits_n",
    ]
    for column in bool_columns:
        frame[column] = boolean(series(frame, column, 0))
    for column in numeric_columns:
        frame[column] = numeric(series(frame, column, 0), 0)

    public = frame[(frame["is_public_plan"] == 1) & (frame["is_hidden_plan"] == 0)].copy()
    public["price_w"] = winsor(public["plan_monthly_price"]).fillna(0)
    public["quota_w"] = winsor(public["max_quota_amount"]).fillna(0)
    public["has_overage_plan"] = (public["max_overage_price"] > 0).astype(int)
    public["endpoint_limited_plan"] = (
        (public["plan_mapped_endpoints_count"] > 0) | (public["plan_all_endpoint_items_count"] > 0)
    ).astype(int)
    public["has_rate_limit_plan"] = (
        (public["rateLimit_enabled"] == 1) | (public["rate_limit_amount"] > 0)
    ).astype(int)

    rows: list[dict[str, object]] = []
    for api_id, group in public.groupby("api_id", sort=False):
        free = group[group["is_free_plan"] == 1]
        paid = group[(group["is_paid_plan"] == 1) & (group["price_w"] > 0)]
        min_price = float(paid["price_w"].min()) if not paid.empty else np.nan
        max_price = float(paid["price_w"].max()) if not paid.empty else np.nan
        min_quota = float(paid["quota_w"].replace(0, np.nan).min()) if not paid.empty else np.nan
        max_quota = float(paid["quota_w"].max()) if not paid.empty else 0.0
        free_quota = float(free["quota_w"].max()) if not free.empty else 0.0
        price_span = np.log1p(max_price) - np.log1p(min_price) if np.isfinite(min_price) else 0.0
        quota_span = np.log1p(max_quota) - np.log1p(min_quota) if np.isfinite(min_quota) else 0.0
        rows.append(
            {
                "api_id": api_id,
                "public_plan_count": int(len(group)),
                "paid_plan_count": int((group["is_paid_plan"] == 1).sum()),
                "free_plan_count": int((group["is_free_plan"] == 1).sum()),
                "has_free_plan": int((group["is_free_plan"] == 1).any()),
                "min_paid_price": min_price,
                "max_paid_price": max_price,
                "free_quota": free_quota,
                "max_paid_quota": max_quota,
                "price_ladder_span": max(0.0, float(price_span)),
                "quota_ladder_span": max(0.0, float(quota_span)),
                "approval_any": int((group["requires_approval"] == 1).any()),
                "menu_has_overage": int((group["has_overage_plan"] == 1).any()),
                "menu_has_hard_limit": int((group["hard_limits_n"] > 0).any()),
                "menu_has_soft_limit": int((group["soft_limits_n"] > 0).any()),
                "menu_has_rate_limit": int((group["has_rate_limit_plan"] == 1).any()),
                "menu_unlimited_share": float((group["has_unlimited_limit"] == 1).mean()),
                "menu_endpoint_limited_share": float(group["endpoint_limited_plan"].mean()),
                "menu_all_endpoint_limit_share": float((group["all_endpoint_limits_n"] > 0).mean()),
                "mean_limits_n": float(group["limits_n"].mean()),
                "mean_finite_limits_n": float(group["finite_limits_n"].mean()),
                "max_overage_price": float(group["max_overage_price"].max()),
            }
        )
    menu = pd.DataFrame(rows)
    if menu.empty:
        menu = pd.DataFrame(columns=["api_id"])
    numeric_menu = [column for column in menu.columns if column != "api_id"]
    for column in numeric_menu:
        menu[column] = numeric(menu[column], 0)
    for source_column, output_column in [
        ("public_plan_count", "ln_public_plan_count"),
        ("paid_plan_count", "ln_paid_plan_count"),
        ("free_quota", "ln_free_quota"),
        ("max_paid_quota", "ln_max_paid_quota"),
        ("min_paid_price", "ln_min_paid_price"),
        ("max_overage_price", "ln_max_overage_price"),
    ]:
        menu[output_column] = ln1p(series(menu, source_column, 0))
    menu["trial_generosity_index"] = zscore(series(menu, "has_free_plan", 0)) + 0.40 * zscore(menu["ln_free_quota"])
    menu["versioning_index"] = (
        zscore(menu["ln_paid_plan_count"])
        + 0.35 * zscore(series(menu, "price_ladder_span", 0))
        + 0.35 * zscore(series(menu, "quota_ladder_span", 0))
        + 0.25 * zscore(series(menu, "menu_has_overage", 0))
        + 0.25 * zscore(series(menu, "menu_endpoint_limited_share", 0))
    )
    menu["contract_metering_index"] = (
        zscore(series(menu, "menu_has_hard_limit", 0))
        + zscore(series(menu, "menu_has_soft_limit", 0))
        + 0.40 * zscore(series(menu, "menu_has_rate_limit", 0))
        + 0.35 * zscore(menu["ln_max_overage_price"])
        + 0.25 * zscore(series(menu, "mean_limits_n", 0))
    )
    return menu


def recompute_api_features(api: pd.DataFrame, plans: pd.DataFrame, snapshot_date: pd.Timestamp) -> pd.DataFrame:
    frame = api.drop(columns=[c for c in STALE_STRUCTURAL_COLUMNS if c in api.columns]).copy()
    frame["primary_type"] = frame.apply(classify_market, axis=1)
    frame["subscriptions_count"] = numeric(series(frame, "subscriptions_count", 0), 0).clip(lower=0)
    frame["q_obs"] = frame["subscriptions_count"] + 1
    frame["ln_subscriptions"] = ln1p(frame["subscriptions_count"])
    rating = numeric(series(frame, "rating"))
    frame["rating_clean"] = rating.where(rating.between(0, 5), np.nan).fillna(0)
    frame["ln_rating_votes"] = ln1p(series(frame, "rating_votes", 0))
    created = pd.to_datetime(numeric(series(frame, "created_at")), unit="ms", errors="coerce", utc=True)
    frame["ln_api_age"] = np.log1p((snapshot_date - created).dt.days.clip(lower=0)).fillna(0)
    frame["ln_owner_api_count"] = ln1p(series(frame, "published_apis_count", 0))

    endpoint_count = numeric(series(frame, "static_endpoints_observed"), 0)
    endpoint_count = endpoint_count.where(endpoint_count > 0, numeric(series(frame, "endpoints_count"), 0))
    frame["endpoint_count"] = endpoint_count.clip(lower=0)
    frame["ln_endpoints"] = ln1p(frame["endpoint_count"])
    frame["ln_params"] = ln1p(series(frame, "static_params_total", 0))
    frame["ln_required_params"] = ln1p(series(frame, "static_required_params_total", 0))
    frame["ln_payload_rows"] = ln1p(series(frame, "static_payload_rows", 0))
    frame["ln_payload_schema_rows"] = ln1p(series(frame, "static_payload_schema_rows", 0))
    frame["ln_readme"] = ln1p(series(frame, "readme_len", 0))
    frame["ln_terms_len"] = ln1p(series(frame, "terms_text_len", 0))
    frame["ln_spec_len"] = ln1p(series(frame, "spec_len", 0))
    endpoints = frame["endpoint_count"]
    params = numeric(series(frame, "static_params_total"), 0)
    frame["post_share"] = np.where(endpoints > 0, numeric(series(frame, "static_post_endpoints"), 0) / endpoints, 0)
    frame["required_param_share"] = np.where(params > 0, numeric(series(frame, "static_required_params_total"), 0) / params, 0)
    frame["schema_endpoint_share"] = np.where(endpoints > 0, numeric(series(frame, "static_schema_endpoints"), 0) / endpoints, 0)
    frame["external_docs_share"] = np.where(endpoints > 0, numeric(series(frame, "static_external_docs_endpoints"), 0) / endpoints, 0)
    frame["route_depth"] = numeric(series(frame, "static_endpoint_route_depth_mean"), 0)
    frame["endpoint_description_len"] = numeric(series(frame, "static_endpoint_description_mean_len"), 0)
    frame["param_description_len"] = numeric(series(frame, "static_param_description_mean_len"), 0)
    frame["has_openapi_spec"] = boolean(series(frame, "has_openapi_spec", 0))
    frame["has_terms_of_service"] = boolean(series(frame, "has_terms_of_service", 0))
    frame["has_auth_info"] = nonempty_text(series(frame, "auth_type")).notna().astype(int)
    frame["security_rules_count"] = numeric(series(frame, "security_rules_count"), 0)

    frame["data_scope_index"] = (
        zscore(frame["ln_endpoints"])
        + 0.50 * zscore(frame["ln_params"])
        + 0.35 * zscore(frame["ln_payload_rows"])
        + 0.25 * zscore(ln1p(series(frame, "endpoint_groups_count", 0)))
    )
    frame["data_complexity_index"] = (
        zscore(frame["route_depth"])
        + 0.50 * zscore(frame["required_param_share"])
        + 0.35 * zscore(frame["post_share"])
        + 0.25 * zscore(frame["ln_required_params"])
    )
    frame["disclosure_index"] = (
        zscore(frame["ln_readme"])
        + 0.35 * zscore(frame["schema_endpoint_share"])
        + 0.35 * zscore(frame["external_docs_share"])
        + 0.30 * zscore(frame["ln_terms_len"])
        + 0.30 * zscore(frame["ln_spec_len"])
    )
    frame["health_success_rate"] = numeric(series(frame, "health_success_rate"), 0)
    frame["reliability_index"] = (
        zscore(numeric(series(frame, "avg_success_rate"), 0) / 100)
        - 0.25 * zscore(ln1p(series(frame, "avg_latency", 0)))
        + 0.40 * zscore(frame["health_success_rate"])
    )

    for column in ["exposure_rows", "exposure_terms_count", "exposure_mean_inverse_rank", "exposure_top10_count"]:
        frame[column] = numeric(series(frame, column), 0)
    frame["exposure_index"] = (
        zscore(ln1p(frame["exposure_rows"]))
        + 0.50 * zscore(ln1p(frame["exposure_terms_count"]))
        + 0.50 * zscore(frame["exposure_mean_inverse_rank"])
        + 0.25 * zscore(ln1p(frame["exposure_top10_count"]))
    )
    spotlight_count = numeric(first_nonempty(frame, ["spotlights_count_y", "spotlight_rows", "detail_extra_spotlights_count", "spotlights_count_x"]), 0)
    frame["spotlights_count_y"] = spotlight_count
    frame["has_spotlight"] = np.maximum(boolean(series(frame, "has_spotlight", 0)), (spotlight_count > 0).astype(int))
    frame["spotlight_index"] = zscore(ln1p(spotlight_count)) + 0.50 * zscore(frame["has_spotlight"])
    frame["has_healthcheck_data"] = boolean(series(frame, "has_healthcheck_data", 0))
    frame["has_restricted_plan"] = boolean(series(frame, "has_restricted_plan", 0))
    frame["allowed_developers_total"] = numeric(series(frame, "allowed_developers_total"), 0)
    frame["restricted_plans_count"] = numeric(series(frame, "restricted_plans_count"), 0)

    menu = build_menu_features(plans)
    menu_columns = [column for column in menu.columns if column != "api_id"]
    frame = frame.drop(columns=[column for column in menu_columns if column in frame.columns])
    frame = frame.merge(menu, on="api_id", how="left", validate="one_to_one")
    for column in menu_columns:
        frame[column] = numeric(series(frame, column), 0)
    frame["has_positive_price"] = (frame["min_paid_price"] > 0).astype(int)
    frame["restricted_access_index"] = (
        zscore(frame["approval_any"])
        + 0.50 * zscore(frame["has_restricted_plan"])
        + 0.35 * zscore(ln1p(frame["restricted_plans_count"]))
        + 0.35 * zscore(ln1p(frame["allowed_developers_total"]))
        + 0.25 * zscore(frame["menu_endpoint_limited_share"])
    )
    frame["uncertainty_index"] = (
        zscore(frame["data_complexity_index"])
        - 0.50 * zscore(frame["disclosure_index"])
        - 0.30 * zscore(frame["reliability_index"])
        + 0.25 * zscore(1 - frame["has_healthcheck_data"])
    )
    frame["free_x_uncertainty"] = frame["has_free_plan"] * frame["uncertainty_index"]
    frame["free_x_complexity"] = frame["has_free_plan"] * frame["data_complexity_index"]
    frame["free_x_low_disclosure"] = frame["has_free_plan"] * (
        frame["disclosure_index"] < frame["disclosure_index"].median()
    ).astype(int)

    frame["market_observed_q"] = frame.groupby("primary_type")["q_obs"].transform("sum")
    frame["market_size"] = frame["market_observed_q"] / 0.20
    frame["share"] = frame["q_obs"] / frame["market_size"]
    frame["outside_share"] = 0.80
    frame["delta_all"] = np.log(frame["share"]) - np.log(frame["outside_share"])
    positive_price = frame["min_paid_price"].where(frame["min_paid_price"] > 0)
    price_cap = float(positive_price.quantile(0.99)) if positive_price.notna().any() else 0.0
    frame["price_cap"] = price_cap
    frame["price_usd"] = frame["min_paid_price"].clip(lower=0, upper=price_cap)
    frame["price_100"] = frame["price_usd"] / 100
    owner_slug = first_nonempty(frame, ["owner_slug", "owner_id", "api_id"])
    frame["owner_slug"] = owner_slug.fillna(frame["api_id"].astype(str))
    frame["owner_market_api_count"] = frame.groupby(["primary_type", "owner_slug"])["api_id"].transform("count")

    grouped = frame.groupby("primary_type")
    market_n = grouped["api_id"].transform("count")
    frame["rival_count"] = market_n - 1
    for raw, output in [
        ("has_free_plan", "z_rival_mean_free"),
        ("data_scope_index", "z_rival_mean_scope"),
        ("data_complexity_index", "z_rival_mean_complexity"),
        ("disclosure_index", "z_rival_mean_disclosure"),
        ("versioning_index", "z_rival_mean_versioning"),
        ("exposure_index", "z_rival_mean_exposure"),
        ("ln_max_paid_quota", "z_rival_mean_quota"),
        ("ln_public_plan_count", "z_rival_mean_plancount"),
    ]:
        total = grouped[raw].transform("sum")
        frame[output] = np.where(market_n > 1, (total - frame[raw]) / (market_n - 1), 0)

    owner_market = frame.groupby(["owner_slug", "primary_type"], as_index=False).agg(
        owner_market_mean_price=("price_100", "mean"),
        owner_market_mean_versioning=("versioning_index", "mean"),
        owner_market_n=("api_id", "count"),
    )
    owner_total = owner_market.groupby("owner_slug", as_index=False).agg(
        owner_all_price_num=("owner_market_mean_price", "sum"),
        owner_all_versioning_num=("owner_market_mean_versioning", "sum"),
        owner_market_cells=("primary_type", "count"),
    )
    owner_columns = list(owner_market.columns[2:]) + list(owner_total.columns[1:])
    frame = frame.drop(columns=[column for column in owner_columns if column in frame.columns])
    frame = frame.merge(owner_market, on=["owner_slug", "primary_type"], how="left", validate="many_to_one")
    frame = frame.merge(owner_total, on="owner_slug", how="left", validate="many_to_one")
    denominator = (frame["owner_market_cells"] - 1).replace(0, np.nan)
    frame["z_owner_other_market_price"] = (
        (frame["owner_all_price_num"] - frame["owner_market_mean_price"]) / denominator
    ).fillna(0)
    frame["z_owner_other_market_versioning"] = (
        (frame["owner_all_versioning_num"] - frame["owner_market_mean_versioning"]) / denominator
    ).fillna(0)
    frame["z_contract_metering"] = frame["contract_metering_index"]
    frame["z_contract_access_control"] = frame["restricted_access_index"]
    frame["in_structural_sample"] = (
        (frame["has_positive_price"] == 1) & np.isfinite(frame["delta_all"])
    ).astype(int)
    return frame


def refresh_embedded_api_features(table: pd.DataFrame, api: pd.DataFrame) -> pd.DataFrame:
    if table.empty or "api_id" not in table.columns:
        return table
    raw_features = [
        "primary_type",
        "q_obs",
        "ln_subscriptions",
        "rating_clean",
        "data_scope_index",
        "data_complexity_index",
        "disclosure_index",
        "reliability_index",
        "has_free_plan",
        "min_paid_price",
        "max_paid_quota",
        "restricted_access_index",
        "uncertainty_index",
        "exposure_index",
    ]
    mapping = {column: f"api_{column}" for column in raw_features if column in api.columns}
    embedded = api[["api_id", *mapping]].rename(columns=mapping)
    out = table.drop(columns=[column for column in mapping.values() if column in table.columns])
    return out.merge(embedded, on="api_id", how="left", validate="many_to_one")


def prepare_core_tables(merged_dir: Path, run_dir: Path, snapshot_date: pd.Timestamp) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, int]], set[str]]:
    strict_dir = run_dir / "_strict_consolidated_tables"
    output: dict[str, pd.DataFrame] = {}
    metrics: dict[str, dict[str, int]] = {}
    base_ids = set(nonempty_text(read_csv(merged_dir / "rapidapi_merged_api_master.csv")["api_id"]).dropna())
    for name, keys in CORE_TABLES.items():
        base = read_csv(merged_dir / name)
        source = strict_dir / name if name in STRICT_SOURCE_TABLES and (strict_dir / name).exists() else run_dir / name
        delta = read_csv(source)
        combined, info = merge_new_rows(base, delta, keys, CORE_REQUIRED_KEYS[name])
        info["source_is_strict"] = int(source.parent == strict_dir)
        output[name] = combined
        metrics[name] = info

    output["rapidapi_merged_api_master.csv"] = recompute_api_features(
        output["rapidapi_merged_api_master.csv"],
        output["rapidapi_merged_plan_contracts.csv"],
        snapshot_date,
    )
    api = output["rapidapi_merged_api_master.csv"]
    output["rapidapi_merged_search_exposure.csv"] = refresh_embedded_api_features(
        output["rapidapi_merged_search_exposure.csv"], api
    )
    output["rapidapi_merged_marketplace_listings.csv"] = refresh_embedded_api_features(
        output["rapidapi_merged_marketplace_listings.csv"], api
    )
    new_ids = set(nonempty_text(api["api_id"]).dropna()) - base_ids
    return output, metrics, new_ids


def normalize_external_delta(name: str, delta: pd.DataFrame, api: pd.DataFrame, baseline_columns: list[str]) -> pd.DataFrame:
    out = delta.copy()
    if name == "schema_overlap_pairs.csv":
        out = out.rename(columns={"api_id": "api_id_left", "rival_api_id": "api_id_right"})
    if name == "rapidapi_external_enriched_panel.csv":
        delta_ids = set(nonempty_text(out["api_id"]).dropna().astype(str))
        identity = api.loc[
            api["api_id"].astype(str).isin(delta_ids),
            [c for c in ["api_id", "api_slug", "api_title", "owner_id", "owner_slug", "primary_type", "subscriptions_count", "website_url"] if c in api.columns],
        ]
        external_columns = [column for column in baseline_columns if column not in identity.columns or column == "api_id"]
        available = [column for column in external_columns if column in out.columns]
        out = identity.merge(out[available].drop_duplicates("api_id", keep="last"), on="api_id", how="left", validate="one_to_one")
        if "schema_overlap_definition" in baseline_columns:
            out["schema_overlap_definition"] = series(out, "schema_overlap_definition").fillna(
                "Jaccard overlap of normalized endpoint, parameter, and schema tokens within the use-case market"
            )
        for column in ["competitor_match_count", "competitor_platform_count"]:
            if column in baseline_columns:
                out[column] = numeric(series(out, column), 0)
    if name == "external_api_adoption.csv" and "github_source" in baseline_columns:
        out["github_source"] = series(out, "github_source").fillna("public code search")
    for column in baseline_columns:
        if column not in out.columns:
            out[column] = np.nan
    extra = [column for column in out.columns if column not in baseline_columns]
    return out[[*baseline_columns, *extra]]


def add_open_candidate_key(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    candidate_id = nonempty_text(series(out, "candidate_id"))
    candidate_url = nonempty_text(series(out, "candidate_url"))
    candidate_title = nonempty_text(series(out, "candidate_title"))
    candidate_rank = nonempty_text(series(out, "candidate_rank"))
    fallback_title = (
        nonempty_text(series(out, "open_source")).fillna("unknown")
        + "|title|"
        + candidate_title.fillna("")
    ).where(candidate_title.notna())
    fallback_rank = (
        nonempty_text(series(out, "open_source")).fillna("unknown")
        + "|rank|"
        + candidate_rank.fillna("")
    ).where(candidate_rank.notna())
    out["candidate_key"] = candidate_id.fillna(candidate_url).fillna(fallback_title).fillna(fallback_rank)
    return out


def prepare_external_tables(external_dir: Path, run_dir: Path, api: pd.DataFrame) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, int]]]:
    incremental_dir = run_dir / "external_incremental"
    output: dict[str, pd.DataFrame] = {}
    metrics: dict[str, dict[str, int]] = {}
    for name, (delta_name, keys) in EXTERNAL_TABLES.items():
        base = read_csv(external_dir / name)
        delta = read_csv(incremental_dir / delta_name)
        if base.empty and delta.empty:
            continue
        if name == "open_data_candidates.csv":
            base = add_open_candidate_key(base)
            delta = add_open_candidate_key(delta)
        delta = normalize_external_delta(name, delta, api, list(base.columns)) if not delta.empty else delta
        combined, info = merge_new_rows(base, delta, keys, EXTERNAL_REQUIRED_KEYS[name])
        output[name] = combined
        metrics[name] = info
    return output, metrics


def validate_bundle(core: dict[str, pd.DataFrame], external: dict[str, pd.DataFrame], new_ids: set[str]) -> dict[str, object]:
    checks: dict[str, object] = {}
    for name, frame in {**core, **external}.items():
        keys = CORE_TABLES[name] if name in CORE_TABLES else EXTERNAL_TABLES[name][1]
        required_keys = CORE_REQUIRED_KEYS[name] if name in CORE_TABLES else EXTERNAL_REQUIRED_KEYS[name]
        missing_key_columns = [key for key in keys if key not in frame.columns]
        duplicate_rows = int(frame.duplicated(keys, keep=False).sum()) if not missing_key_columns and not frame.empty else 0
        invalid_key_rows = int((~valid_key_rows(frame, required_keys)).sum()) if not missing_key_columns and not frame.empty else len(frame)
        checks[name] = {
            "rows": int(len(frame)),
            "columns": int(len(frame.columns)),
            "key": keys,
            "missing_key_columns": missing_key_columns,
            "invalid_key_rows": invalid_key_rows,
            "duplicate_rows_on_key": duplicate_rows,
        }
    api = core["rapidapi_merged_api_master.csv"]
    feature_checks = {}
    for column in CRITICAL_FEATURES:
        values = numeric(series(api, column)) if column != "primary_type" else nonempty_text(series(api, column))
        feature_checks[column] = {
            "nonmissing": int(values.notna().sum()),
            "nonmissing_share": float(values.notna().mean()),
            "all_empty": bool(values.notna().sum() == 0),
        }
    new = api[api["api_id"].astype(str).isin(new_ids)]
    new_feature_empty = [] if not new_ids else [
        column for column in CRITICAL_FEATURES
        if column not in new.columns or nonempty_text(new[column]).notna().sum() == 0
    ]
    failures = []
    for name, check in checks.items():
        if check["missing_key_columns"] or check["invalid_key_rows"] or check["duplicate_rows_on_key"]:
            failures.append(name)
    if any(item["all_empty"] for item in feature_checks.values()):
        failures.append("critical_features")
    if new_feature_empty:
        failures.append("new_api_critical_features")
    return {
        "checks": checks,
        "critical_feature_coverage": feature_checks,
        "new_api_count": len(new_ids),
        "new_api_critical_all_empty_columns": new_feature_empty,
        "validation_failures": failures,
        "valid": not failures,
    }


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def update_manifest(merged_dir: Path, core: dict[str, pd.DataFrame], temp_merged: Path) -> None:
    manifest = read_csv(merged_dir / "rapidapi_merged_table_manifest.csv")
    if manifest.empty:
        manifest = pd.DataFrame({"table": list(core)})
    for name, frame in core.items():
        if name not in set(manifest.get("table", pd.Series(dtype=str)).astype(str)):
            manifest = pd.concat([manifest, pd.DataFrame({"table": [name]})], ignore_index=True)
        mask = manifest["table"].astype(str).eq(name)
        manifest.loc[mask, "rows"] = len(frame)
        manifest.loc[mask, "columns"] = len(frame.columns)
    write_csv(manifest, temp_merged / "rapidapi_merged_table_manifest.csv")


def replace_bundle(files: dict[Path, Path]) -> None:
    if not files:
        return
    common_parent = Path(os.path.commonpath([str(target.parent) for target in files]))
    backup_dir = Path(tempfile.mkdtemp(prefix="promotion_backup_", dir=common_parent))
    replaced: list[Path] = []
    try:
        for target in files:
            if target.exists():
                backup = backup_dir / str(len(replaced))
                shutil.copy2(target, backup)
            else:
                backup = None
            os.replace(files[target], target)
            replaced.append(target)
            if backup is not None:
                backup.with_suffix(".target").write_text(str(target), encoding="utf-8")
    except Exception:
        for index, target in enumerate(replaced):
            backup = backup_dir / str(index)
            if backup.exists():
                os.replace(backup, target)
            elif target.exists():
                target.unlink()
        raise
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)


def update_registry(history_dir: Path, run_id: str, manifest_path: Path) -> None:
    registry_path = history_dir / "promoted_runs.json"
    if registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    else:
        registry = {"runs": []}
    registry["runs"] = [run for run in registry.get("runs", []) if run.get("run_id") != run_id]
    registry["runs"].append(
        {
            "run_id": run_id,
            "promoted_at_utc": datetime.now(timezone.utc).isoformat(),
            "manifest": str(manifest_path),
        }
    )
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="rapidapi_crawl")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--snapshot-date", default=None, help="ISO date; defaults to current UTC date")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    run_dir = Path(args.run_dir).resolve()
    merged_dir = root / "data_merged"
    external_dir = root / "data_external"
    history_dir = root / "data_incremental"
    run_id = run_dir.name
    snapshot_date = pd.Timestamp(args.snapshot_date or datetime.now(timezone.utc).date(), tz="UTC")

    validation_manifests = [
        run_dir / "rapidapi_weekly_incremental_validation.json",
        run_dir / "rapidapi_weekly_strict_recrawl_validation.json",
    ]
    source_validation = next((path for path in validation_manifests if path.exists()), None)
    if source_validation is None:
        expected = ", ".join(str(path) for path in validation_manifests)
        raise SystemExit(f"Incremental validation is missing; expected one of: {expected}")

    core, core_metrics, new_ids = prepare_core_tables(merged_dir, run_dir, snapshot_date)
    external, external_metrics = prepare_external_tables(
        external_dir, run_dir, core["rapidapi_merged_api_master.csv"]
    )
    validation = validate_bundle(core, external, new_ids)
    manifest = {
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_date": snapshot_date.isoformat(),
        "dry_run": args.dry_run,
        "source_validation_manifest": str(source_validation),
        "core_merge": core_metrics,
        "external_merge": external_metrics,
        **validation,
    }
    manifest_name = "rapidapi_promotion_dry_run_manifest.json" if args.dry_run else "rapidapi_promotion_manifest.json"
    manifest_path = run_dir / manifest_name
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if not validation["valid"]:
        raise SystemExit(f"Promotion validation failed; inspect {manifest_path}")
    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return

    temp_root = Path(tempfile.mkdtemp(prefix=f"promotion_{run_id}_", dir=root))
    temp_merged = temp_root / "data_merged"
    temp_external = temp_root / "data_external"
    try:
        replacements: dict[Path, Path] = {}
        for name, frame in core.items():
            source = temp_merged / name
            write_csv(frame, source)
            replacements[merged_dir / name] = source
        for name, frame in external.items():
            source = temp_external / name
            write_csv(frame, source)
            replacements[external_dir / name] = source

        validation_json = temp_merged / "rapidapi_merged_validation.json"
        validation_json.parent.mkdir(parents=True, exist_ok=True)
        validation_json.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
        replacements[merged_dir / "rapidapi_merged_validation.json"] = validation_json
        validation_rows = []
        for name, check in validation["checks"].items():
            validation_rows.append({"table": name, **check, "key": " + ".join(check["key"]), "missing_key_columns": "|".join(check["missing_key_columns"])})
        validation_csv = temp_merged / "rapidapi_merged_validation.csv"
        write_csv(pd.DataFrame(validation_rows), validation_csv)
        replacements[merged_dir / "rapidapi_merged_validation.csv"] = validation_csv
        update_manifest(merged_dir, core, temp_merged)
        replacements[merged_dir / "rapidapi_merged_table_manifest.csv"] = temp_merged / "rapidapi_merged_table_manifest.csv"
        replace_bundle(replacements)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    update_registry(history_dir, run_id, manifest_path)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

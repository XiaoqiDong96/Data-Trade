#!/usr/bin/env python3
"""Build a small set of consolidated RapidAPI research tables.

The raw/normalized crawl contains many tables because they preserve the
platform's natural hierarchy.  This script creates a collaborator-facing
version with fewer tables while keeping the main empirical levels separate:
API, plan, endpoint/schema, search exposure, and marketplace listings.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd


csv.field_size_limit(sys.maxsize)


def read_csv(path: Path, usecols: list[str] | None = None) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str, low_memory=False, usecols=usecols)


def to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def first_nonempty(s: pd.Series) -> str:
    for value in s.dropna().astype(str):
        if value and value.lower() != "nan":
            return value
    return ""


def join_unique(values: Iterable[object], max_items: int = 80) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if pd.isna(value):
            continue
        text = str(value).strip()
        if not text or text.lower() == "nan" or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= max_items:
            break
    return "|".join(out)


def json_unique(values: Iterable[object], max_items: int = 120) -> str:
    items = [x for x in join_unique(values, max_items=max_items).split("|") if x]
    return json.dumps(items, ensure_ascii=False)


def count_true(s: pd.Series) -> int:
    if s.empty:
        return 0
    vals = s.astype(str).str.lower()
    return int(vals.isin(["1", "true", "yes", "y"]).sum())


def add_prefix_except(df: pd.DataFrame, keys: list[str], prefix: str) -> pd.DataFrame:
    rename = {c: f"{prefix}{c}" for c in df.columns if c not in keys}
    return df.rename(columns=rename)


def safe_left_merge(
    base: pd.DataFrame,
    add: pd.DataFrame,
    keys: list[str],
    prefix: str,
    validate: str | None = None,
) -> pd.DataFrame:
    if add.empty:
        return base
    usable_keys = [k for k in keys if k in base.columns and k in add.columns]
    if not usable_keys:
        return base
    add = add.copy()
    keep = usable_keys + [c for c in add.columns if c not in usable_keys]
    add = add[keep]
    rename = {}
    for col in add.columns:
        if col in usable_keys:
            continue
        if col in base.columns:
            rename[col] = f"{prefix}{col}"
    add = add.rename(columns=rename)
    return base.merge(add, on=usable_keys, how="left", validate=validate)


def group_numeric(
    df: pd.DataFrame,
    keys: list[str],
    source: str,
    out_prefix: str,
    funcs: tuple[str, ...] = ("mean", "max", "sum"),
) -> pd.DataFrame:
    if df.empty or source not in df.columns:
        return pd.DataFrame(columns=keys)
    tmp = df[keys + [source]].copy()
    tmp[source] = to_num(tmp[source])
    agg = tmp.groupby(keys, dropna=False)[source].agg(list(funcs)).reset_index()
    agg.columns = keys + [f"{out_prefix}{source}_{f}" for f in funcs]
    return agg


def unique_count(df: pd.DataFrame, keys: list[str], col: str, out: str) -> pd.DataFrame:
    if df.empty or col not in df.columns:
        return pd.DataFrame(columns=keys)
    return df.groupby(keys, dropna=False)[col].nunique(dropna=True).reset_index(name=out)


def build_api_master(root: Path, out: Path) -> tuple[Path, dict]:
    crawl = root / "rapidapi_crawl" / "data"
    model = root / "rapidapi_io_static" / "data"

    base = read_csv(model / "commodity_api_static_features.csv")
    if base.empty:
        base = read_csv(crawl / "rapidapi_static_Data_api_model_panel_plus.csv")

    supply = read_csv(model / "commodity_static_supply.csv")
    if not supply.empty:
        supply_only = [c for c in supply.columns if c not in base.columns]
        supply_add = supply[["api_id"] + supply_only].copy()
        supply_add["in_structural_sample"] = "1"
        base = base.merge(supply_add, on="api_id", how="left", validate="1:1")
        base["in_structural_sample"] = base["in_structural_sample"].fillna("0")

    owners = read_csv(crawl / "rapidapi_static_Data_owners.csv")
    if not owners.empty:
        owner_add = owners.drop(columns=[c for c in ["raw_file"] if c in owners.columns])
        owner_add = add_prefix_except(owner_add, ["owner_id"], "owner_profile_")
        base = safe_left_merge(base, owner_add, ["owner_id"], "owner_profile_", validate="m:1")

    categories = read_csv(crawl / "rapidapi_categories.csv")
    if not categories.empty and "category_id" in base.columns:
        categories = categories.rename(columns={"id": "category_id"})
        categories = add_prefix_except(categories, ["category_id"], "category_")
        base = safe_left_merge(base, categories, ["category_id"], "category_", validate="m:1")

    detail_extra = read_csv(crawl / "rapidapi_static_Data_detail_extra_summary.csv")
    if not detail_extra.empty:
        detail_extra = detail_extra.drop(columns=[c for c in ["raw_file"] if c in detail_extra.columns])
        detail_extra = add_prefix_except(detail_extra, ["api_id"], "detail_extra_")
        base = safe_left_merge(base, detail_extra, ["api_id"], "detail_extra_", validate="1:1")

    versions = read_csv(crawl / "rapidapi_static_Data_api_versions.csv")
    if not versions.empty:
        agg = versions.groupby("api_id", dropna=False).agg(
            api_versions_rows=("version_id", "count"),
            api_versions_distinct=("version_id", "nunique"),
            api_version_ids_json=("version_id", json_unique),
            api_version_names=("version_name", join_unique)
            if "version_name" in versions.columns
            else ("version_id", join_unique),
        ).reset_index()
        base = safe_left_merge(base, agg, ["api_id"], "version_", validate="1:1")

    endpoints = read_csv(crawl / "rapidapi_static_Data_endpoints.csv")
    if not endpoints.empty:
        endpoints["route_depth_num"] = to_num(endpoints.get("route_depth", pd.Series(dtype=str)))
        endpoints["description_len_num"] = to_num(
            endpoints.get("endpoint_description_len", pd.Series(dtype=str))
        )
        ep = endpoints.groupby("api_id", dropna=False).agg(
            endpoint_rows=("endpoint_id", "count"),
            endpoint_distinct=("endpoint_id", "nunique"),
            endpoint_methods=("method", join_unique),
            endpoint_routes_json=("route", json_unique),
            endpoint_route_depth_mean=("route_depth_num", "mean"),
            endpoint_description_len_mean=("description_len_num", "mean"),
        ).reset_index()
        base = safe_left_merge(base, ep, ["api_id"], "endpoint_", validate="1:1")

    params = read_csv(
        crawl / "rapidapi_static_Data_endpoint_params.csv",
        usecols=[
            "api_id",
            "endpoint_id",
            "param_id",
            "param_name",
            "param_type",
            "param_status",
            "is_querystring",
            "description_len",
            "schema_json",
        ],
    )
    if not params.empty:
        params["description_len_num"] = to_num(params["description_len"])
        params["has_schema_num"] = params["schema_json"].notna().astype(int)
        pa = params.groupby("api_id", dropna=False).agg(
            param_rows=("param_id", "count"),
            param_distinct=("param_id", "nunique"),
            param_endpoint_distinct=("endpoint_id", "nunique"),
            param_names=("param_name", join_unique),
            param_types=("param_type", join_unique),
            param_statuses=("param_status", join_unique),
            querystring_param_rows=("is_querystring", count_true),
            param_schema_rows=("has_schema_num", "sum"),
            param_description_len_mean=("description_len_num", "mean"),
        ).reset_index()
        base = safe_left_merge(base, pa, ["api_id"], "param_", validate="1:1")

    payloads = read_csv(
        crawl / "rapidapi_static_Data_payloads.csv",
        usecols=[
            "api_id",
            "endpoint_id",
            "payload_id",
            "payload_kind",
            "payload_scope",
            "payload_name",
            "format",
            "payload_type",
            "status_code",
            "description_len",
            "body_len",
            "has_schema",
        ],
    )
    if not payloads.empty:
        payloads["description_len_num"] = to_num(payloads["description_len"])
        payloads["body_len_num"] = to_num(payloads["body_len"])
        py = payloads.groupby("api_id", dropna=False).agg(
            payload_rows=("payload_id", "count"),
            payload_endpoint_distinct=("endpoint_id", "nunique"),
            payload_kinds=("payload_kind", join_unique),
            payload_scopes=("payload_scope", join_unique),
            payload_formats=("format", join_unique),
            payload_types=("payload_type", join_unique),
            payload_status_codes=("status_code", join_unique),
            payload_schema_rows=("has_schema", count_true),
            payload_body_len_mean=("body_len_num", "mean"),
            payload_body_len_max=("body_len_num", "max"),
            payload_description_len_mean=("description_len_num", "mean"),
        ).reset_index()
        base = safe_left_merge(base, py, ["api_id"], "payload_", validate="1:1")

    for fname, prefix, specs in [
        (
            "rapidapi_static_Data_groups.csv",
            "group_",
            {
                "group_rows": ("group_id", "count"),
                "group_distinct": ("group_id", "nunique"),
                "group_names": ("group_name", join_unique),
            },
        ),
        (
            "rapidapi_static_Data_auth.csv",
            "auth_",
            {
                "auth_rows": ("auth_id", "count"),
                "auth_types": ("auth_type", join_unique),
                "security_types": ("security_type", join_unique),
            },
        ),
        (
            "rapidapi_static_Data_public_dns.csv",
            "dns_",
            {
                "dns_rows": ("public_dns_id", "count"),
                "dns_addresses_json": ("address", json_unique),
                "dns_proxy_modes": ("proxy_mode", join_unique),
            },
        ),
        (
            "rapidapi_static_Data_spotlights.csv",
            "spotlight_",
            {
                "spotlight_rows": ("spotlight_id", "count"),
                "spotlight_types": ("spotlight_type", join_unique),
                "spotlight_titles": ("spotlight_title", join_unique),
            },
        ),
    ]:
        df = read_csv(crawl / fname)
        if not df.empty:
            available = {
                out_col: (src_col, func)
                for out_col, (src_col, func) in specs.items()
                if src_col in df.columns
            }
            if available:
                agg = df.groupby("api_id", dropna=False).agg(**available).reset_index()
                base = safe_left_merge(base, agg, ["api_id"], prefix, validate="1:1")

    features = read_csv(crawl / "rapidapi_details_Data_billing_features.csv")
    if not features.empty:
        feat = features.groupby("api_id", dropna=False).agg(
            billing_feature_rows=("feature_id", "count"),
            billing_feature_distinct=("feature_id", "nunique"),
            billing_feature_names=("feature_name", join_unique),
            billing_feature_statuses=("status", join_unique),
        ).reset_index()
        base = safe_left_merge(base, feat, ["api_id"], "billing_feature_", validate="1:1")

    discovery = read_csv(crawl / "rapidapi_discovery_Data_apis.csv")
    search = read_csv(crawl / "rapidapi_search_Data_apis.csv")
    listing_frames = []
    if not discovery.empty:
        listing_frames.append(discovery.assign(listing_source="discovery"))
    if not search.empty:
        listing_frames.append(search.assign(listing_source="search"))
    if listing_frames:
        listings = pd.concat(listing_frames, ignore_index=True, sort=False)
        lst = listings.groupby("api_id", dropna=False).agg(
            listing_rows=("api_id", "count"),
            listing_sources=("listing_source", join_unique),
            listing_best_rank=("rank", lambda s: to_num(s).min()),
            listing_terms=("discovery_term", join_unique)
            if "discovery_term" in listings.columns
            else ("api_id", join_unique),
            listing_sorts=("discovery_sort", join_unique)
            if "discovery_sort" in listings.columns
            else ("api_id", join_unique),
        ).reset_index()
        base = safe_left_merge(base, lst, ["api_id"], "listing_", validate="1:1")

    out_path = out / "rapidapi_merged_api_master.csv"
    base.to_csv(out_path, index=False)
    return out_path, {
        "table": out_path.name,
        "level": "API",
        "rows": len(base),
        "columns": len(base.columns),
        "primary_key": "api_id",
        "source_tables": "commodity_api_static_features; commodity_static_supply; owners; categories; detail_extra; versions; endpoints; endpoint_params; payloads; groups; auth; public_dns; spotlights; billing_features; discovery/search listings",
        "notes": "One row per API. Includes structural sample indicators and compact API-level aggregates from lower-level technical tables.",
    }


def build_plan_contracts(root: Path, out: Path) -> tuple[Path, dict]:
    crawl = root / "rapidapi_crawl" / "data"
    base = read_csv(crawl / "rapidapi_static_Data_plan_enriched.csv")

    access = read_csv(crawl / "rapidapi_static_Data_plan_access_restrictions.csv")
    if not access.empty:
        access = access.rename(columns={"plan_version_id": "version_id"})
        access = access.drop(columns=[c for c in ["raw_file", "api_slug", "api_name"] if c in access.columns])
        access = add_prefix_except(access, ["api_id", "plan_id", "version_id"], "access_")
        base = safe_left_merge(base, access, ["api_id", "plan_id", "version_id"], "access_")

    allowed = read_csv(crawl / "rapidapi_static_Data_allowed_plan_developers.csv")
    if not allowed.empty:
        allowed = allowed.groupby(["api_id", "plan_id"], dropna=False).agg(
            allowed_developer_rows=("allowed_developer_index", "count"),
            allowed_developer_user_ids=("allowed_developer_user_id", json_unique),
        ).reset_index()
        base = safe_left_merge(base, allowed, ["api_id", "plan_id"], "allowed_", validate="m:1")

    features = read_csv(crawl / "rapidapi_details_Data_billing_features.csv")
    if not features.empty:
        fagg = features.groupby(["api_id", "plan_id", "version_id"], dropna=False).agg(
            plan_feature_rows=("feature_id", "count"),
            plan_feature_ids=("feature_id", json_unique),
            plan_feature_names=("feature_name", join_unique),
            plan_feature_statuses=("status", join_unique),
        ).reset_index()
        base = safe_left_merge(base, fagg, ["api_id", "plan_id", "version_id"], "feature_", validate="m:1")

    limits = read_csv(crawl / "rapidapi_panel_Data_plan_limit.csv")
    if not limits.empty:
        for col in ["limit_amount_num", "limit_monthly_amount", "limit_overage_price_num"]:
            if col in limits.columns:
                limits[col] = to_num(limits[col])
        lagg = limits.groupby(["api_id", "plan_id", "version_id"], dropna=False).agg(
            limit_rows=("limit_id", "count"),
            limit_distinct=("limit_id", "nunique"),
            limit_ids_json=("limit_id", json_unique),
            limit_types=("limit_type", join_unique),
            limit_items=("billingitem_name", join_unique),
            limit_all_endpoint_rows=("limit_is_all_endpoints", count_true),
            limit_unlimited_rows=("limit_is_unlimited", count_true),
            limit_hard_rows=("limit_is_hard", count_true),
            limit_soft_rows=("limit_is_soft", count_true),
            limit_amount_max=("limit_amount_num", "max"),
            limit_monthly_amount_max=("limit_monthly_amount", "max"),
            limit_overage_price_max=("limit_overage_price_num", "max"),
        ).reset_index()
        base = safe_left_merge(base, lagg, ["api_id", "plan_id", "version_id"], "limit_", validate="m:1")

    ple = read_csv(crawl / "rapidapi_static_Data_plan_limit_endpoint_panel.csv")
    if not ple.empty:
        eagg = ple.groupby(["api_id", "plan_id", "version_id"], dropna=False).agg(
            plan_limit_endpoint_rows=("endpoint_id", "count"),
            plan_limited_endpoint_distinct=("endpoint_id", "nunique"),
            plan_limited_endpoint_methods=("endpoint_method", join_unique),
            plan_limited_endpoint_routes_json=("endpoint_route", json_unique),
            plan_all_endpoints_rows=("all_endpoints", count_true),
        ).reset_index()
        base = safe_left_merge(base, eagg, ["api_id", "plan_id", "version_id"], "limit_endpoint_", validate="m:1")

    out_path = out / "rapidapi_merged_plan_contracts.csv"
    base.to_csv(out_path, index=False)
    return out_path, {
        "table": out_path.name,
        "level": "Plan / pricing contract",
        "rows": len(base),
        "columns": len(base.columns),
        "primary_key": "api_id + plan_id + version_id",
        "source_tables": "rapidapi_static_Data_plan_enriched; plan_access_restrictions; allowed_plan_developers; billing_features; plan_limit; plan_limit_endpoint_panel",
        "notes": "One row per API plan. Limit, feature, access-control, allowed-developer, and endpoint-coverage details are compacted to plan-level columns.",
    }


def build_endpoint_schema(root: Path, out: Path) -> tuple[Path, dict]:
    crawl = root / "rapidapi_crawl" / "data"
    base = read_csv(crawl / "rapidapi_static_Data_endpoints.csv")
    keys = ["api_id", "endpoint_id"]

    params = read_csv(
        crawl / "rapidapi_static_Data_endpoint_params.csv",
        usecols=[
            "api_id",
            "endpoint_id",
            "param_id",
            "param_name",
            "param_type",
            "param_condition",
            "param_status",
            "is_querystring",
            "default_value",
            "description_len",
            "schema_json",
        ],
    )
    if not params.empty:
        params["description_len_num"] = to_num(params["description_len"])
        params["has_default_num"] = params["default_value"].notna().astype(int)
        params["has_schema_num"] = params["schema_json"].notna().astype(int)
        pagg = params.groupby(keys, dropna=False).agg(
            param_rows=("param_id", "count"),
            param_distinct=("param_id", "nunique"),
            param_names_json=("param_name", json_unique),
            param_types=("param_type", join_unique),
            param_conditions=("param_condition", join_unique),
            param_statuses=("param_status", join_unique),
            querystring_param_rows=("is_querystring", count_true),
            default_value_rows=("has_default_num", "sum"),
            param_schema_rows=("has_schema_num", "sum"),
            param_description_len_mean=("description_len_num", "mean"),
        ).reset_index()
        base = safe_left_merge(base, pagg, keys, "param_", validate="1:1")

    payloads = read_csv(
        crawl / "rapidapi_static_Data_payloads.csv",
        usecols=[
            "api_id",
            "endpoint_id",
            "payload_id",
            "payload_kind",
            "payload_scope",
            "payload_name",
            "format",
            "payload_type",
            "status_code",
            "description_len",
            "body_len",
            "has_schema",
        ],
    )
    if not payloads.empty:
        payloads["description_len_num"] = to_num(payloads["description_len"])
        payloads["body_len_num"] = to_num(payloads["body_len"])
        yagg = payloads.groupby(keys, dropna=False).agg(
            payload_rows=("payload_id", "count"),
            payload_names_json=("payload_name", json_unique),
            payload_kinds=("payload_kind", join_unique),
            payload_scopes=("payload_scope", join_unique),
            payload_formats=("format", join_unique),
            payload_types=("payload_type", join_unique),
            payload_status_codes=("status_code", join_unique),
            payload_schema_rows=("has_schema", count_true),
            payload_body_len_mean=("body_len_num", "mean"),
            payload_body_len_max=("body_len_num", "max"),
            payload_description_len_mean=("description_len_num", "mean"),
        ).reset_index()
        base = safe_left_merge(base, yagg, keys, "payload_", validate="1:1")

    billing = read_csv(crawl / "rapidapi_static_Data_billing_item_endpoints.csv")
    if not billing.empty:
        bagg = billing.groupby(keys, dropna=False).agg(
            billing_endpoint_rows=("billingitem_id", "count"),
            billingitem_distinct=("billingitem_id", "nunique"),
            billingitem_names=("billingitem_name", join_unique),
            billingitem_types=("billingitem_type", join_unique),
        ).reset_index()
        base = safe_left_merge(base, bagg, keys, "billing_", validate="1:1")

    ple = read_csv(crawl / "rapidapi_static_Data_plan_limit_endpoint_panel.csv")
    if not ple.empty:
        le = ple.groupby(keys, dropna=False).agg(
            limited_plan_rows=("plan_id", "count"),
            limited_plan_distinct=("plan_id", "nunique"),
            limited_limit_distinct=("limit_id", "nunique"),
            limited_plan_ids_json=("plan_id", json_unique),
            limited_limit_ids_json=("limit_id", json_unique),
        ).reset_index()
        base = safe_left_merge(base, le, keys, "limited_", validate="1:1")

    out_path = out / "rapidapi_merged_endpoint_schema.csv"
    base.to_csv(out_path, index=False)
    return out_path, {
        "table": out_path.name,
        "level": "Endpoint / schema",
        "rows": len(base),
        "columns": len(base.columns),
        "primary_key": "api_id + endpoint_id",
        "source_tables": "endpoints; endpoint_params; payloads; billing_item_endpoints; plan_limit_endpoint_panel",
        "notes": "One row per endpoint. Parameter, payload/schema, billing-item, and plan-limit mappings are compacted to endpoint-level columns. Large raw body/schema JSON fields are summarized rather than embedded.",
    }


def build_search_exposure(root: Path, out: Path) -> tuple[Path, dict]:
    crawl = root / "rapidapi_crawl" / "data"
    model = root / "rapidapi_io_static" / "data"
    base = read_csv(crawl / "rapidapi_search_Data_exposure_panel.csv")

    facets = read_csv(crawl / "rapidapi_search_Data_exposure_facets.csv")
    facet_keys = ["search_term", "search_sort", "search_page", "query_id", "replica_index"]
    if not facets.empty:
        fagg = facets.groupby(facet_keys, dropna=False).agg(
            facet_rows=("facet", "count"),
            facet_names=("facet", join_unique),
            facet_keys=("facet_key", join_unique),
            facet_counts_sum=("facet_count", lambda s: to_num(s).sum()),
        ).reset_index()
        base = safe_left_merge(base, fagg, facet_keys, "facet_", validate="m:1")

    combos = read_csv(crawl / "rapidapi_search_Data_exposure_combos.csv")
    if not combos.empty:
        combos = combos.rename(columns={"term": "search_term", "sort": "search_sort"})
        combos = add_prefix_except(combos, ["search_term", "search_sort"], "combo_")
        base = safe_left_merge(base, combos, ["search_term", "search_sort"], "combo_", validate="m:1")

    api = read_csv(model / "commodity_api_static_features.csv")
    api_cols = [
        "api_id",
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
    ]
    api_cols = [c for c in api_cols if c in api.columns]
    if api_cols:
        api_add = add_prefix_except(api[api_cols], ["api_id"], "api_")
        base = safe_left_merge(base, api_add, ["api_id"], "api_", validate="m:1")

    out_path = out / "rapidapi_merged_search_exposure.csv"
    base.to_csv(out_path, index=False)
    return out_path, {
        "table": out_path.name,
        "level": "Search exposure",
        "rows": len(base),
        "columns": len(base.columns),
        "primary_key": "query_id + replica_index + search_rank + api_id",
        "source_tables": "search_exposure_panel; search_exposure_facets; search_exposure_combos; commodity_api_static_features",
        "notes": "Long table at the search-result level. Keeps ranking variation and adds compact query/facet/API feature columns.",
    }


def build_marketplace_listings(root: Path, out: Path) -> tuple[Path, dict]:
    crawl = root / "rapidapi_crawl" / "data"
    model = root / "rapidapi_io_static" / "data"
    frames = []
    discovery = read_csv(crawl / "rapidapi_discovery_Data_apis.csv")
    if not discovery.empty:
        frames.append(discovery.assign(listing_source="discovery"))
    search = read_csv(crawl / "rapidapi_search_Data_apis.csv")
    if not search.empty:
        frames.append(search.assign(listing_source="search"))
    if frames:
        base = pd.concat(frames, ignore_index=True, sort=False)
    else:
        base = pd.DataFrame()

    api = read_csv(model / "commodity_api_static_features.csv")
    api_cols = [
        "api_id",
        "primary_type",
        "q_obs",
        "has_free_plan",
        "min_paid_price",
        "data_scope_index",
        "data_complexity_index",
        "disclosure_index",
        "reliability_index",
        "exposure_index",
    ]
    api_cols = [c for c in api_cols if c in api.columns]
    if not base.empty and api_cols:
        api_add = add_prefix_except(api[api_cols], ["api_id"], "api_")
        base = safe_left_merge(base, api_add, ["api_id"], "api_", validate="m:1")

    out_path = out / "rapidapi_merged_marketplace_listings.csv"
    base.to_csv(out_path, index=False)
    return out_path, {
        "table": out_path.name,
        "level": "Marketplace listing",
        "rows": len(base),
        "columns": len(base.columns),
        "primary_key": "listing_source + rank/page + api_id",
        "source_tables": "discovery_Data_apis; search_Data_apis; commodity_api_static_features",
        "notes": "Unified listing table for discovery and search-list API rows. Use search_exposure for the richer repeated ranking panel.",
    }


def build_dictionary(out: Path, table_paths: list[Path], root: Path) -> Path:
    existing = read_csv(root / "rapidapi_crawl" / "data" / "rapidapi_raw_variable_dictionary_full.csv")
    meaning_by_var = {}
    role_by_var = {}
    if not existing.empty:
        for _, row in existing.iterrows():
            var = row.get("variable")
            if isinstance(var, str) and var not in meaning_by_var:
                meaning_by_var[var] = row.get("meaning_cn", "")
                role_by_var[var] = row.get("role", "")

    custom = {
        "in_structural_sample": "是否进入当前静态结构模型样本。",
        "delta0_calibrated": "校准价格系数下的平均效用基准项。",
        "markup_usd": "结构供给侧推回的美元 markup。",
        "mc_usd": "结构供给侧推回的美元边际成本。",
        "own_elasticity": "校准模型下的自身价格弹性。",
        "lerner_index": "Lerner 指数，衡量价格超过边际成本的比例。",
    }

    rows = []
    for path in table_paths:
        with path.open(newline="", encoding="utf-8") as f:
            header = next(csv.reader(f))
        for col in header:
            rows.append(
                {
                    "table": path.name,
                    "variable": col,
                    "meaning_cn": custom.get(col, meaning_by_var.get(col, "")),
                    "role": role_by_var.get(col, ""),
                    "note": "合并表字段；空白含义可回查原始变量字典或源表。"
                    if not custom.get(col, meaning_by_var.get(col, ""))
                    else "",
                }
            )
    dictionary = pd.DataFrame(rows)
    out_path = out / "rapidapi_merged_variable_dictionary.csv"
    dictionary.to_csv(out_path, index=False)
    return out_path


def write_readme(out: Path, manifest: pd.DataFrame) -> Path:
    lines = [
        "# RapidAPI Data 合并表说明",
        "",
        "本目录把当前 Data 类目静态截面的多张 CSV 合并为较少的研究交付表。",
        "合并时保留了主要实证层级：API、plan、endpoint/schema、search exposure、marketplace listing。",
        "大型原始 schema/body JSON 没有嵌入合并表，而是转成计数、类型、状态码、长度和名称列表等变量；需要逐条复核时回到 `rapidapi_crawl/raw/graphql/`。",
        "",
        "## 表清单",
        "",
        "| 表 | 层级 | 行数 | 列数 | 主键 | 说明 |",
        "|---|---|---:|---:|---|---|",
    ]
    for _, row in manifest.iterrows():
        lines.append(
            f"| `{row['table']}` | {row['level']} | {row['rows']} | {row['columns']} | {row['primary_key']} | {row['notes']} |"
        )
    lines.extend(
        [
            "",
            "## 使用建议",
            "",
            "1. 描述统计、reduced form 和结构模型优先使用 `rapidapi_merged_api_master.csv` 与 `rapidapi_merged_plan_contracts.csv`。",
            "2. 技术复杂度、接口范围和 schema 机制使用 `rapidapi_merged_endpoint_schema.csv`。",
            "3. 搜索排序、曝光和平台可见性机制使用 `rapidapi_merged_search_exposure.csv`。",
            "4. `rapidapi_merged_marketplace_listings.csv` 用于补充 discovery/search listing 入口的覆盖情况。",
        ]
    )
    path = out / "README.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="Project root")
    parser.add_argument("--out-dir", default="rapidapi_crawl/data_merged")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    out = (root / args.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    table_paths: list[Path] = []
    manifest_rows: list[dict] = []
    for builder in [
        build_api_master,
        build_plan_contracts,
        build_endpoint_schema,
        build_search_exposure,
        build_marketplace_listings,
    ]:
        path, info = builder(root, out)
        table_paths.append(path)
        manifest_rows.append(info)

    manifest = pd.DataFrame(manifest_rows)
    manifest_path = out / "rapidapi_merged_table_manifest.csv"
    manifest.to_csv(manifest_path, index=False)
    dictionary_path = build_dictionary(out, table_paths, root)
    readme_path = write_readme(out, manifest)

    print(
        json.dumps(
            {
                "out_dir": str(out),
                "tables": manifest_rows,
                "manifest": str(manifest_path),
                "dictionary": str(dictionary_path),
                "readme": str(readme_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Merge static enrichment tables back into RapidAPI empirical panels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, low_memory=False)


def first_existing(paths: list[Path]) -> Path:
    for path in paths:
        if path.exists():
            return path
    raise FileNotFoundError("No candidate path exists: " + ", ".join(str(p) for p in paths))


def safe_to_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = df.copy()
    for col in clean.select_dtypes(include=["object", "string"]).columns:
        clean[col] = clean[col].astype("string").str.replace(r"[\r\n\t]+", " ", regex=True).str.strip()
    clean.to_csv(path, index=False)


def bool_num(series: pd.Series) -> pd.Series:
    return series.astype("string").str.lower().map({"true": 1, "false": 0, "1": 1, "0": 0}).fillna(0).astype(int)


def build(root: Path, category: str) -> dict[str, object]:
    suffix = category
    packaged = root / "data" / "api平台数据"
    plan_path = first_existing([
        root / "data" / f"rapidapi_panel_{suffix}_plan.csv",
        packaged / f"rapidapi_panel_{suffix}_plan.csv",
    ])
    plan_limit_path = first_existing([
        root / "data" / f"rapidapi_panel_{suffix}_plan_limit.csv",
        packaged / f"rapidapi_panel_{suffix}_plan_limit.csv",
    ])

    plan = load_csv(plan_path)
    plan_limit = load_csv(plan_limit_path)
    api = load_csv(root / "data" / f"rapidapi_static_{suffix}_api_enriched.csv")
    versions = load_csv(root / "data" / f"rapidapi_static_{suffix}_playground_versions.csv")
    endpoints = load_csv(root / "data" / f"rapidapi_static_{suffix}_endpoints.csv")
    params = load_csv(root / "data" / f"rapidapi_static_{suffix}_endpoint_params.csv")
    payloads = load_csv(root / "data" / f"rapidapi_static_{suffix}_payloads.csv")
    owners = load_csv(root / "data" / f"rapidapi_static_{suffix}_owners.csv")
    billing_map = load_csv(root / "data" / f"rapidapi_static_{suffix}_billing_item_endpoints.csv")

    api_cols = [
        "api_id",
        "current_version_id",
        "api_subtype",
        "gateway_ids_count",
        "allowed_context_count",
        "has_terms_of_service",
        "terms_text_len",
        "has_readme",
        "has_long_description",
        "spotlights_count",
    ]
    api_small = api[[c for c in api_cols if c in api.columns]].drop_duplicates("api_id")

    version_cols = [
        "api_id",
        "version_id",
        "api_version_type",
        "has_openapi_spec",
        "spec_len",
        "endpoints_count",
        "get_endpoints_count",
        "post_endpoints_count",
        "put_endpoints_count",
        "delete_endpoints_count",
        "graphql_endpoints_count",
        "endpoint_groups_count",
        "assets_count",
        "version_payloads_count",
        "public_dns_count",
        "target_urls_count",
        "auth_type",
        "security_rules_count",
    ]
    version_small = versions[[c for c in version_cols if c in versions.columns]].drop_duplicates(["api_id", "version_id"])
    version_for_merge = version_small.rename(columns={"version_id": "api_version_id"})

    endpoint_agg = endpoints.groupby("api_id", dropna=False).agg(
        static_endpoints_observed=("endpoint_id", "nunique"),
        static_get_endpoints=("method", lambda x: int((x.astype("string").str.upper() == "GET").sum())),
        static_post_endpoints=("method", lambda x: int((x.astype("string").str.upper() == "POST").sum())),
        static_endpoint_description_mean_len=("endpoint_description_len", "mean"),
        static_endpoint_route_depth_mean=("route_depth", "mean"),
        static_required_params_total=("required_params_count", "sum"),
        static_params_total=("params_count", "sum"),
        static_request_payloads_total=("request_payloads_count", "sum"),
        static_response_payloads_total=("response_payloads_count", "sum"),
        static_external_docs_endpoints=("has_external_docs", "sum"),
        static_schema_endpoints=("has_schema", "sum"),
    ).reset_index()

    params_agg = params.groupby("api_id", dropna=False).agg(
        static_param_rows=("param_order", "count"),
        static_required_param_rows=("param_condition", lambda x: int((x.astype("string").str.upper() == "REQUIRED").sum())),
        static_query_param_rows=("is_querystring", lambda x: int(bool_num(x).sum())),
        static_param_description_mean_len=("description_len", "mean"),
    ).reset_index()

    payloads_agg = payloads.groupby("api_id", dropna=False).agg(
        static_payload_rows=("payload_id", "count"),
        static_payload_schema_rows=("has_schema", "sum"),
        static_payload_body_mean_len=("body_len", "mean"),
    ).reset_index()

    owner_cols = [
        "owner_id",
        "published_apis_count",
        "published_data_apis_count",
        "published_public_apis_count",
        "published_freemium_apis_count",
        "published_free_apis_count",
        "published_paid_apis_count",
        "published_categories_count",
        "has_description",
        "has_bio",
        "description_len",
        "bio_len",
    ]
    owner_small = owners[[c for c in owner_cols if c in owners.columns]].drop_duplicates("owner_id")

    api_model = api.merge(version_for_merge, left_on=["api_id", "current_version_id"], right_on=["api_id", "api_version_id"], how="left")
    api_model = api_model.merge(endpoint_agg, on="api_id", how="left")
    api_model = api_model.merge(params_agg, on="api_id", how="left")
    api_model = api_model.merge(payloads_agg, on="api_id", how="left")
    api_model = api_model.merge(owner_small, on="owner_id", how="left")

    panel = plan.merge(api_small, on="api_id", how="left", suffixes=("", "_static"))
    panel = panel.merge(version_for_merge, left_on=["api_id", "current_version_id"], right_on=["api_id", "api_version_id"], how="left")
    panel = panel.merge(endpoint_agg, on="api_id", how="left")
    panel = panel.merge(params_agg, on="api_id", how="left")
    panel = panel.merge(payloads_agg, on="api_id", how="left")
    if "owner_id" in panel.columns and not owners.empty:
        panel = panel.merge(owner_small, on="owner_id", how="left")

    bm = billing_map.copy()
    bm["all_endpoints_num"] = bool_num(bm.get("all_endpoints", pd.Series(dtype=str)))
    bm_plan = (
        plan_limit[["api_id", "plan_id", "version_id", "limit_id", "billingitem_id"]]
        .merge(
            bm[
                [
                    "api_id",
                    "billingitem_id",
                    "all_endpoints",
                    "all_endpoints_num",
                    "billingitemendpoint_id",
                    "endpoint_id",
                    "endpoint_method",
                    "endpoint_route",
                    "endpoint_name",
                ]
            ],
            on=["api_id", "billingitem_id"],
            how="left",
        )
    )

    plan_endpoint_agg = bm_plan.groupby(["api_id", "plan_id", "version_id"], dropna=False).agg(
        plan_billingitem_endpoint_rows=("billingitemendpoint_id", "count"),
        plan_mapped_endpoints_count=("endpoint_id", "nunique"),
        plan_all_endpoint_items_count=("all_endpoints_num", "sum"),
        plan_endpoint_methods=("endpoint_method", lambda x: "|".join(sorted(set(v for v in x.dropna().astype(str) if v)))),
    ).reset_index()
    panel = panel.merge(plan_endpoint_agg, on=["api_id", "plan_id", "version_id"], how="left")

    out_api = root / "data" / f"rapidapi_static_{suffix}_api_model_panel.csv"
    out_plan = root / "data" / f"rapidapi_static_{suffix}_plan_enriched.csv"
    out_plan_limit_endpoint = root / "data" / f"rapidapi_static_{suffix}_plan_limit_endpoint_panel.csv"
    safe_to_csv(api_model, out_api)
    safe_to_csv(panel, out_plan)
    safe_to_csv(bm_plan, out_plan_limit_endpoint)

    summary = {
        "api_model_rows": int(len(api_model)),
        "plan_enriched_rows": int(len(panel)),
        "plan_limit_endpoint_rows": int(len(bm_plan)),
        "endpoint_api_coverage": int(endpoint_agg["api_id"].nunique()) if not endpoint_agg.empty else 0,
        "owner_coverage": int(owners["owner_id"].nunique()) if not owners.empty else 0,
        "outputs": {
            "api_model_panel": str(out_api),
            "plan_enriched": str(out_plan),
            "plan_limit_endpoint_panel": str(out_plan_limit_endpoint),
        },
    }
    (root / "data" / f"rapidapi_static_{suffix}_panel_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="rapidapi_crawl")
    parser.add_argument("--category", default="Data")
    args = parser.parse_args()
    print(json.dumps(build(Path(args.root), args.category), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

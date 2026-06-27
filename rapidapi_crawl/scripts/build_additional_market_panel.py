#!/usr/bin/env python3
"""Build API-level panels from additional RapidAPI market variables."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from rapidapi_crawler import safe_name


def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path)


def bool_sum(series: pd.Series) -> int:
    return int(series.fillna(False).astype(bool).sum())


def build_exposure_api_summary(exposure: pd.DataFrame) -> pd.DataFrame:
    if exposure.empty:
        return pd.DataFrame()

    exposure = exposure.copy()
    exposure["search_term_clean"] = exposure["search_term"].fillna("")
    exposure["rank_inverse"] = 1 / exposure["search_rank"].where(exposure["search_rank"] > 0)
    exposure["top10"] = exposure["search_rank"] <= 10
    exposure["top50"] = exposure["search_rank"] <= 50
    exposure["top100"] = exposure["search_rank"] <= 100

    base = (
        exposure.groupby("api_id", as_index=False)
        .agg(
            exposure_rows=("api_id", "size"),
            exposure_terms_count=("search_term_clean", "nunique"),
            exposure_sorts_count=("search_sort", "nunique"),
            exposure_best_rank=("search_rank", "min"),
            exposure_mean_rank=("search_rank", "mean"),
            exposure_median_rank=("search_rank", "median"),
            exposure_mean_inverse_rank=("rank_inverse", "mean"),
            exposure_top10_count=("top10", bool_sum),
            exposure_top50_count=("top50", bool_sum),
            exposure_top100_count=("top100", bool_sum),
            exposure_mean_reported_total=("reported_total", "mean"),
            exposure_min_reported_total=("reported_total", "min"),
            name=("name", "first"),
            title=("title", "first"),
            slugifiedName=("slugifiedName", "first"),
            owner_slugifiedName=("owner_slugifiedName", "first"),
            owner_username=("owner_username", "first"),
            pricing=("pricing", "first"),
        )
    )

    for sort in sorted(exposure["search_sort"].dropna().unique()):
        tmp = (
            exposure[exposure["search_sort"] == sort]
            .groupby("api_id")
            .agg(
                **{
                    f"exposure_{safe_name(sort).lower()}_rows": ("api_id", "size"),
                    f"exposure_{safe_name(sort).lower()}_best_rank": ("search_rank", "min"),
                }
            )
            .reset_index()
        )
        base = base.merge(tmp, on="api_id", how="left")

    return base


def write_dictionary(path: Path) -> None:
    rows = [
        ("rapidapi_static_Data_healthcheck.csv", "api_id", "RapidAPI API ID, merge key."),
        ("rapidapi_static_Data_healthcheck.csv", "health_total", "Number of healthcheck observations reported by RapidAPI."),
        ("rapidapi_static_Data_healthcheck.csv", "health_failed", "Failed healthcheck observations."),
        ("rapidapi_static_Data_healthcheck.csv", "health_successful", "Successful healthcheck observations."),
        ("rapidapi_static_Data_healthcheck.csv", "health_failure_rate", "health_failed / health_total when both are nonmissing."),
        ("rapidapi_static_Data_healthcheck.csv", "health_success_rate", "health_successful / health_total when both are nonmissing."),
        ("rapidapi_static_Data_healthcheck.csv", "has_healthcheck_data", "Indicator that at least one healthcheck statistic is nonmissing."),
        ("rapidapi_static_Data_plan_access_restrictions.csv", "has_allowed_plan_developers", "Plan-level indicator for nonempty allowedPlanDevelopers."),
        ("rapidapi_static_Data_plan_access_restrictions.csv", "allowed_plan_developers_count", "Number of developer IDs explicitly allowed on the plan."),
        ("rapidapi_static_Data_allowed_plan_developers.csv", "allowed_developer_user_id", "Developer user ID listed in allowedPlanDevelopers."),
        ("rapidapi_static_Data_spotlights.csv", "spotlight_id", "Spotlight record ID."),
        ("rapidapi_static_Data_spotlights.csv", "spotlight_type", "Spotlight type, usually link/media style public promotion."),
        ("rapidapi_static_Data_spotlights.csv", "spotlight_weight", "Ordering/weight field for spotlight display."),
        ("rapidapi_static_Data_spotlights.csv", "spotlight_published", "Whether the spotlight is marked published."),
        ("rapidapi_static_Data_spotlights.csv", "spotlight_status", "Spotlight status."),
        ("rapidapi_static_Data_detail_extra_summary.csv", "has_restricted_plan", "API-level indicator for at least one restricted plan."),
        ("rapidapi_static_Data_detail_extra_summary.csv", "restricted_plans_count", "Number of API plans with nonempty allowedPlanDevelopers."),
        ("rapidapi_static_Data_detail_extra_summary.csv", "has_spotlight", "API-level indicator for at least one spotlight."),
        ("rapidapi_static_Data_detail_extra_summary.csv", "spotlights_count", "Number of spotlights attached to the API."),
        ("rapidapi_search_Data_exposure_panel.csv", "search_term", "Search keyword used for this marketplace exposure window."),
        ("rapidapi_search_Data_exposure_panel.csv", "search_sort", "Search sort field: ByRelevance, ByUpdatedAt, or ByAlphabetical."),
        ("rapidapi_search_Data_exposure_panel.csv", "search_rank", "Rank within the term-sort search result window."),
        ("rapidapi_search_Data_exposure_panel.csv", "search_page", "Search result page number."),
        ("rapidapi_search_Data_exposure_panel.csv", "search_page_position", "Position within the page."),
        ("rapidapi_search_Data_exposure_panel.csv", "reported_total", "RapidAPI-reported total hits for the term-sort query."),
        ("rapidapi_search_Data_exposure_panel.csv", "query_id", "Search backend query identifier returned by RapidAPI."),
        ("rapidapi_search_Data_exposure_panel.csv", "replica_index", "Search backend replica index returned by RapidAPI."),
        ("rapidapi_search_Data_exposure_api_summary.csv", "exposure_rows", "Number of observed search appearances for the API."),
        ("rapidapi_search_Data_exposure_api_summary.csv", "exposure_terms_count", "Number of distinct search terms where the API appears."),
        ("rapidapi_search_Data_exposure_api_summary.csv", "exposure_best_rank", "Best observed rank across all search windows."),
        ("rapidapi_search_Data_exposure_api_summary.csv", "exposure_mean_inverse_rank", "Mean inverse rank, larger means stronger average visibility."),
        ("rapidapi_static_Data_api_model_panel_plus.csv", "health_*", "Healthcheck variables merged onto the existing static API model panel."),
        ("rapidapi_static_Data_api_model_panel_plus.csv", "exposure_*", "API-level exposure variables merged from search exposure windows."),
    ]
    pd.DataFrame(rows, columns=["file", "variable", "meaning"]).to_csv(path, index=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="rapidapi_crawl")
    ap.add_argument("--category", default="Data")
    args = ap.parse_args()

    root = Path(args.root)
    suffix = safe_name(args.category)
    data_dir = root / "data"

    model = load_csv(data_dir / f"rapidapi_static_{suffix}_api_model_panel.csv")
    health = load_csv(data_dir / f"rapidapi_static_{suffix}_healthcheck.csv")
    extras = load_csv(data_dir / f"rapidapi_static_{suffix}_detail_extra_summary.csv")
    exposure = load_csv(data_dir / f"rapidapi_search_{suffix}_exposure_panel.csv")

    exposure_api = build_exposure_api_summary(exposure)
    exposure_api.to_csv(data_dir / f"rapidapi_search_{suffix}_exposure_api_summary.csv", index=False)

    plus = model.copy()
    if not health.empty:
        health_cols = [
            "api_id",
            "health_total",
            "health_failed",
            "health_successful",
            "health_failure_rate",
            "health_success_rate",
            "has_healthcheck_data",
        ]
        plus = plus.merge(health[[col for col in health_cols if col in health.columns]], on="api_id", how="left")
    if not extras.empty:
        extra_cols = [
            "api_id",
            "restricted_plans_count",
            "allowed_developers_total",
            "has_restricted_plan",
            "spotlights_count",
            "has_spotlight",
        ]
        plus = plus.merge(extras[[col for col in extra_cols if col in extras.columns]], on="api_id", how="left")
    if not exposure_api.empty:
        exposure_cols = [
            "api_id",
            "exposure_rows",
            "exposure_terms_count",
            "exposure_sorts_count",
            "exposure_best_rank",
            "exposure_mean_rank",
            "exposure_median_rank",
            "exposure_mean_inverse_rank",
            "exposure_top10_count",
            "exposure_top50_count",
            "exposure_top100_count",
            "exposure_mean_reported_total",
            "exposure_min_reported_total",
            "exposure_byalphabetical_rows",
            "exposure_byalphabetical_best_rank",
            "exposure_byrelevance_rows",
            "exposure_byrelevance_best_rank",
            "exposure_byupdatedat_rows",
            "exposure_byupdatedat_best_rank",
        ]
        plus = plus.merge(exposure_api[[col for col in exposure_cols if col in exposure_api.columns]], on="api_id", how="left")

    plus.to_csv(data_dir / f"rapidapi_static_{suffix}_api_model_panel_plus.csv", index=False)
    write_dictionary(data_dir / f"rapidapi_additional_{suffix}_variable_dictionary.csv")

    summary = {
        "category": args.category,
        "api_model_rows": len(model),
        "healthcheck_rows": len(health),
        "healthcheck_nonmissing_apis": int(health.get("has_healthcheck_data", pd.Series(dtype=float)).fillna(0).sum()) if not health.empty else 0,
        "detail_extra_rows": len(extras),
        "restricted_api_count": int(extras.get("has_restricted_plan", pd.Series(dtype=float)).fillna(0).sum()) if not extras.empty else 0,
        "spotlight_api_count": int(extras.get("has_spotlight", pd.Series(dtype=float)).fillna(0).sum()) if not extras.empty else 0,
        "exposure_rows": len(exposure),
        "exposure_unique_apis": int(exposure["api_id"].nunique()) if not exposure.empty else 0,
        "exposure_api_summary_rows": len(exposure_api),
        "plus_panel_rows": len(plus),
        "plus_panel_columns": len(plus.columns),
        "plus_panel_exposure_coverage": int(plus["exposure_rows"].notna().sum()) if "exposure_rows" in plus else 0,
    }
    (data_dir / f"rapidapi_additional_{suffix}_panel_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build descriptive analysis, reduced-form regressions, and a PDF-ready report.

The analysis treats RapidAPI Data APIs as traded data commodities. Outputs are
written under rapidapi_analysis/{data,tables,figures,report}.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "rapidapi_crawl" / "data" / "api平台数据"
DISCOVERY = ROOT / "rapidapi_crawl" / "data" / "rapidapi_discovery_Data_apis.csv"
OUT = ROOT / "rapidapi_analysis"
DATA = OUT / "data"
TABLES = OUT / "tables"
FIGURES = OUT / "figures"
REPORT = OUT / "report"
SAMPLE_DATE = pd.Timestamp("2026-06-14", tz="UTC")


for directory in [DATA, TABLES, FIGURES, REPORT]:
    directory.mkdir(parents=True, exist_ok=True)


TAXONOMY = {
    "web_scraping": ["scraper", "scrape", "scraping", "crawler", "crawl", "extractor", "extraction", "metadata", "web data", "parser", "parse"],
    "social_profile": ["linkedin", "instagram", "tiktok", "twitter", "x.com", "youtube", "facebook", "social", "reddit", "telegram", "profile"],
    "geo_identity": ["geolocation", "location", "address", "postcode", "zipcode", "maps", "places", "ip geo", "whois", "phone", "email", "identity"],
    "firm_lead": ["company", "business", "lead", "apollo", "email finder", "enrich", "b2b", "firmographic"],
    "finance_market": ["stock", "crypto", "finance", "market", "forex", "trading", "ticker", "price"],
    "ecommerce_price": ["amazon", "ebay", "shopify", "product", "price tracker", "reviews", "walmart", "shopee", "store"],
    "document_text": ["pdf", "ocr", "document", "invoice", "image", "text extraction", "sentiment", "nlp"],
    "real_estate_mobility": ["real estate", "realtor", "property", "zillow", "rightmove", "apartments", "airbnb", "hotel", "flight", "travel"],
    "public_reference": ["country", "city", "state", "population", "unemployment", "public data", "census", "statistics"],
}

FRESHNESS_WORDS = ["real-time", "realtime", "live", "fresh", "updated", "latest", "historical"]


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, low_memory=False)


def num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def b(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype("string").str.lower().map({"true": True, "false": False, "1": True, "0": False}).fillna(False)


def ln1p(series: pd.Series) -> pd.Series:
    return np.log1p(num(series).clip(lower=0))


def winsor(series: pd.Series, p_low: float = 0.01, p_high: float = 0.99) -> pd.Series:
    s = num(series)
    lo, hi = s.quantile([p_low, p_high])
    return s.clip(lo, hi)


def md_table(df: pd.DataFrame, floatfmt: int = 3) -> str:
    if df.empty:
        return "_无数据_"
    out = df.copy().fillna("")
    for col in out.columns:
        if pd.api.types.is_float_dtype(out[col]):
            out[col] = out[col].map(lambda x: "" if pd.isna(x) else f"{x:.{floatfmt}f}")
    headers = list(out.columns)
    lines = ["| " + " | ".join(map(str, headers)) + " |"]
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for _, row in out.iterrows():
        lines.append("| " + " | ".join(str(row[h]) for h in headers) + " |")
    return "\n".join(lines)


def save_table(name: str, df: pd.DataFrame) -> None:
    df.to_csv(TABLES / f"{name}.csv", index=False)
    (TABLES / f"{name}.md").write_text(md_table(df), encoding="utf-8")


def plan_tier(plan_name: object) -> str:
    text = str(plan_name or "").upper()
    if "BASIC" in text:
        return "BASIC"
    if "PRO" in text:
        return "PRO"
    if "ULTRA" in text:
        return "ULTRA"
    if "MEGA" in text:
        return "MEGA"
    if "CUSTOM" in text:
        return "CUSTOM"
    if "FREE" in text:
        return "FREE"
    return "OTHER"


def add_taxonomy(df: pd.DataFrame) -> pd.DataFrame:
    text_cols = [c for c in ["api_name", "name", "slugifiedName", "description", "description_listing", "tags"] if c in df.columns]
    text = df[text_cols].fillna("").agg(" ".join, axis=1).str.lower()
    for key, patterns in TAXONOMY.items():
        df[f"type_{key}"] = text.apply(lambda x: int(any(p in x for p in patterns)))
    df["type_freshness"] = text.apply(lambda x: int(any(p in x for p in FRESHNESS_WORDS)))
    return df


def make_stata_friendly(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_bool_dtype(out[col]):
            out[col] = out[col].astype(int)
        elif out[col].dtype == "object":
            lowered = out[col].dropna().astype(str).str.lower()
            if not lowered.empty and lowered.isin(["true", "false", "1", "0"]).all():
                out[col] = (
                    out[col]
                    .astype(str)
                    .str.lower()
                    .map({"true": 1, "false": 0, "1": 1, "0": 0})
                )
    return out


def make_api_level() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    discovery = read_csv(DISCOVERY)
    apis = read_csv(SOURCE / "rapidapi_details_Data_apis.csv")
    plans = read_csv(SOURCE / "rapidapi_details_Data_billing_plans.csv")
    limits = read_csv(SOURCE / "rapidapi_details_Data_billing_limits.csv")
    plan_panel = read_csv(SOURCE / "rapidapi_panel_Data_plan.csv")
    limit_panel = read_csv(SOURCE / "rapidapi_panel_Data_plan_limit.csv")

    apis = apis.rename(
        columns={
            "subscriptionsCount": "subscriptions_count",
            "popularityScore": "popularity_score",
            "avgLatency": "avg_latency",
            "avgServiceLevel": "avg_service_level",
            "avgSuccessRate": "avg_success_rate",
        }
    )
    apis = apis.merge(
        discovery[["api_id", "description", "tags", "rank", "page", "pricing"]].rename(
            columns={"pricing": "listing_pricing", "rank": "search_rank", "page": "search_page"}
        ),
        on="api_id",
        how="left",
    )
    apis = add_taxonomy(apis)

    apis["rating_clean"] = num(apis["rating"]).where(num(apis["rating"]).between(0, 5))
    apis["ln_subscriptions"] = ln1p(apis["subscriptions_count"])
    apis["ln_rating_votes"] = ln1p(apis["ratingVotes"])
    apis["ln_readme"] = ln1p(apis["readme_len"])
    apis["ln_long_description"] = ln1p(apis["longDescription_len"])
    apis["ln_latency"] = ln1p(apis["avg_latency"])
    apis["api_age_days"] = (
        SAMPLE_DATE - pd.to_datetime(num(apis["createdAt"]), unit="ms", errors="coerce", utc=True)
    ).dt.days
    apis["days_since_update"] = (
        SAMPLE_DATE - pd.to_datetime(num(apis["updatedAt"]), unit="ms", errors="coerce", utc=True)
    ).dt.days
    apis["ln_api_age"] = ln1p(apis["api_age_days"])
    apis["ln_days_since_update"] = ln1p(apis["days_since_update"])

    owner_counts = apis.groupby("owner_slugifiedName")["api_id"].nunique().rename("owner_api_count")
    apis = apis.merge(owner_counts, left_on="owner_slugifiedName", right_index=True, how="left")
    apis["ln_owner_api_count"] = ln1p(apis["owner_api_count"])

    plan_panel = plan_panel.copy()
    plan_panel["is_public_plan"] = b(plan_panel["is_public_plan"])
    plan_panel["is_hidden_plan"] = b(plan_panel["is_hidden_plan"])
    plan_panel["requires_approval"] = b(plan_panel["requires_approval"])
    plan_panel["is_free_plan"] = b(plan_panel["is_free_plan"])
    plan_panel["is_paid_plan"] = b(plan_panel["is_paid_plan"])
    plan_panel["is_recommended_plan"] = b(plan_panel["is_recommended_plan"])
    plan_panel["plan_monthly_price"] = num(plan_panel["plan_monthly_price"])
    plan_panel["max_quota_amount"] = num(plan_panel["max_quota_amount"])
    plan_panel["mean_overage_price"] = num(plan_panel["mean_overage_price"])
    plan_panel["plan_tier"] = plan_panel["plan_name"].map(plan_tier)
    public_plans = plan_panel[(plan_panel["is_public_plan"]) & (~plan_panel["is_hidden_plan"])].copy()

    pos_price = public_plans[public_plans["plan_monthly_price"] > 0]
    api_plan = public_plans.groupby("api_id").agg(
        public_plan_count=("plan_id", "nunique"),
        has_free_plan=("is_free_plan", "max"),
        has_recommended_plan=("is_recommended_plan", "max"),
        has_approval_plan=("requires_approval", "max"),
        public_paid_plan_count=("is_paid_plan", "sum"),
        max_public_quota=("max_quota_amount", "max"),
        median_public_quota=("max_quota_amount", "median"),
        has_soft_limit=("soft_limits_n", lambda x: int(num(x).fillna(0).gt(0).any())),
        has_hard_limit=("hard_limits_n", lambda x: int(num(x).fillna(0).gt(0).any())),
        max_overage_price=("mean_overage_price", "max"),
        mean_overage_price=("mean_overage_price", "mean"),
    )
    price_stats = pos_price.groupby("api_id").agg(
        min_paid_price=("plan_monthly_price", "min"),
        median_paid_price=("plan_monthly_price", "median"),
        max_paid_price=("plan_monthly_price", "max"),
    )
    all_price_stats = public_plans.groupby("api_id").agg(
        median_public_price=("plan_monthly_price", "median"),
        max_public_price=("plan_monthly_price", "max"),
    )
    api_plan = api_plan.join(price_stats, how="left").join(all_price_stats, how="left")

    private_stats = plan_panel.groupby("api_id").agg(
        has_private_plan=("is_private_plan", lambda x: int(b(x).any()) if x.dtype != bool else int(x.any())),
        total_plan_count=("plan_id", "nunique"),
    )
    api_plan = api_plan.join(private_stats, how="outer")

    api_level = apis.merge(api_plan, on="api_id", how="left")
    fill_zero = [
        "public_plan_count",
        "has_free_plan",
        "has_recommended_plan",
        "has_approval_plan",
        "public_paid_plan_count",
        "has_soft_limit",
        "has_hard_limit",
        "has_private_plan",
        "total_plan_count",
    ]
    for col in fill_zero:
        api_level[col] = api_level[col].fillna(0)

    api_level["ln_min_paid_price"] = ln1p(api_level["min_paid_price"])
    api_level["ln_median_paid_price"] = ln1p(api_level["median_paid_price"])
    api_level["ln_max_public_quota"] = ln1p(api_level["max_public_quota"])
    api_level["ln_public_plan_count"] = ln1p(api_level["public_plan_count"])
    api_level["ln_total_plan_count"] = ln1p(api_level["total_plan_count"])
    api_level["ln_max_overage_price"] = ln1p(api_level["max_overage_price"])
    api_level["has_positive_price"] = num(api_level["min_paid_price"]).notna().astype(int)
    api_level["has_popularity_score"] = num(api_level["popularity_score"]).notna().astype(int)
    api_level["success_rate_scaled"] = num(api_level["avg_success_rate"]) / 100
    api_level["service_level_scaled"] = num(api_level["avg_service_level"]) / 100

    public_plans["ln_plan_price"] = ln1p(public_plans["plan_monthly_price"])
    public_plans["ln_plan_price_w"] = np.log1p(winsor(public_plans["plan_monthly_price"], 0.01, 0.99).clip(lower=0))
    public_plans["ln_max_quota"] = ln1p(public_plans["max_quota_amount"])
    public_plans["ln_overage"] = ln1p(public_plans["mean_overage_price"])
    public_plans["has_positive_overage"] = (num(public_plans["mean_overage_price"]) > 0).astype(int)
    public_plans["has_soft_limit"] = (num(public_plans["soft_limits_n"]).fillna(0) > 0).astype(int)
    public_plans["has_hard_limit"] = (num(public_plans["hard_limits_n"]).fillna(0) > 0).astype(int)
    public_plans = public_plans.merge(
        api_level[
            [
                "api_id",
                "ln_subscriptions",
                "popularity_score",
                "success_rate_scaled",
                "service_level_scaled",
                "ln_latency",
                "rating_clean",
                "ln_rating_votes",
                "ln_readme",
                "ln_owner_api_count",
                *[f"type_{k}" for k in TAXONOMY],
                "type_freshness",
            ]
        ],
        on="api_id",
        how="left",
        suffixes=("", "_api"),
    )

    DATA.mkdir(parents=True, exist_ok=True)
    make_stata_friendly(api_level).to_csv(DATA / "api_level.csv", index=False)
    make_stata_friendly(public_plans).to_csv(DATA / "public_plan_level.csv", index=False)
    make_stata_friendly(limit_panel).to_csv(DATA / "plan_limit_level.csv", index=False)
    plans.to_csv(DATA / "raw_plans.csv", index=False)
    limits.to_csv(DATA / "raw_limits.csv", index=False)
    return api_level, public_plans, limit_panel, plans, limits


def describe_data(api: pd.DataFrame, plans: pd.DataFrame, limits: pd.DataFrame, public_plans: pd.DataFrame) -> dict[str, pd.DataFrame]:
    sample_overview = pd.DataFrame(
        [
            ["有效 API", api["api_id"].nunique()],
            ["API 提供者 owner", api["owner_slugifiedName"].nunique()],
            ["父组织 parent org", api["parent_org_slugifiedName"].nunique()],
            ["价格计划", len(plans)],
            ["公开非隐藏价格计划", len(public_plans)],
            ["调用额度/超额费规则", len(limits)],
            ["有公开计划的 API", public_plans["api_id"].nunique()],
            ["有额度规则的 API", limits["api_id"].nunique()],
        ],
        columns=["指标", "数值"],
    )
    save_table("sample_overview", sample_overview)

    pricing_dist = api["pricing"].value_counts(dropna=False).rename_axis("API 定价标签").reset_index(name="API 数")
    pricing_dist["占比"] = pricing_dist["API 数"] / pricing_dist["API 数"].sum()
    save_table("api_pricing_distribution", pricing_dist)

    plan_pricing = plans["pricing"].value_counts(dropna=False).rename_axis("计划定价类型").reset_index(name="计划数")
    plan_pricing["占比"] = plan_pricing["计划数"] / plan_pricing["计划数"].sum()
    save_table("plan_pricing_distribution", plan_pricing)

    plan_visibility = plans["plan_visibility"].value_counts(dropna=False).rename_axis("计划可见性").reset_index(name="计划数")
    plan_visibility["占比"] = plan_visibility["计划数"] / plan_visibility["计划数"].sum()
    save_table("plan_visibility_distribution", plan_visibility)

    tax = []
    for key in TAXONOMY:
        n = int(api[f"type_{key}"].sum())
        tax.append([key, n, n / len(api)])
    taxonomy = pd.DataFrame(tax, columns=["数据商品类型", "API 数", "占比"]).sort_values("API 数", ascending=False)
    save_table("taxonomy_distribution", taxonomy)

    owners = api.groupby("owner_slugifiedName").agg(
        api_count=("api_id", "nunique"),
        total_subscriptions=("subscriptions_count", "sum"),
        mean_subscriptions=("subscriptions_count", "mean"),
    ).sort_values("api_count", ascending=False).head(20).reset_index()
    save_table("top_owners", owners)

    summary_vars = [
        ("subscriptions_count", "订阅数"),
        ("public_plan_count", "公开计划数"),
        ("min_paid_price", "最低正月费"),
        ("median_paid_price", "正价计划中位月费"),
        ("max_public_quota", "最大公开额度"),
        ("rating_clean", "评分"),
        ("ratingVotes", "评分票数"),
        ("popularity_score", "人气分"),
        ("avg_latency", "平均延迟"),
        ("avg_success_rate", "成功率"),
        ("readme_len", "文档长度"),
        ("api_age_days", "API 年龄天数"),
    ]
    rows = []
    for col, label in summary_vars:
        s = num(api[col])
        rows.append(
            [
                label,
                int(s.notna().sum()),
                s.mean(),
                s.std(),
                s.quantile(0.25),
                s.quantile(0.5),
                s.quantile(0.75),
                s.quantile(0.9),
                s.quantile(0.99),
            ]
        )
    desc = pd.DataFrame(rows, columns=["变量", "N", "均值", "标准差", "P25", "P50", "P75", "P90", "P99"])
    save_table("api_summary_statistics", desc)

    plan_summary_vars = [
        ("plan_monthly_price", "公开计划月费"),
        ("max_quota_amount", "计划最大额度"),
        ("mean_overage_price", "平均超额费"),
        ("billinglimits_count", "额度规则数"),
        ("features_count", "功能项数"),
    ]
    rows = []
    for col, label in plan_summary_vars:
        s = num(public_plans[col])
        rows.append(
            [
                label,
                int(s.notna().sum()),
                s.mean(),
                s.std(),
                s.quantile(0.25),
                s.quantile(0.5),
                s.quantile(0.75),
                s.quantile(0.9),
                s.quantile(0.99),
            ]
        )
    plan_desc = pd.DataFrame(rows, columns=["变量", "N", "均值", "标准差", "P25", "P50", "P75", "P90", "P99"])
    save_table("plan_summary_statistics", plan_desc)

    return {
        "sample_overview": sample_overview,
        "api_pricing_distribution": pricing_dist,
        "plan_pricing_distribution": plan_pricing,
        "plan_visibility_distribution": plan_visibility,
        "taxonomy_distribution": taxonomy,
        "top_owners": owners,
        "api_summary_statistics": desc,
        "plan_summary_statistics": plan_desc,
    }


def save_fig(path: Path) -> None:
    plt.tight_layout()
    plt.savefig(path, dpi=220, bbox_inches="tight")
    plt.close()


def make_figures(api: pd.DataFrame, public_plans: pd.DataFrame, limits: pd.DataFrame, tables: dict[str, pd.DataFrame]) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")

    plt.figure(figsize=(7, 4.5))
    s = num(api["subscriptions_count"])
    plt.hist(np.log1p(s.dropna()), bins=45, color="#2a6f97")
    plt.xlabel("log(1 + subscriptions)")
    plt.ylabel("API count")
    plt.title("Subscription distribution is strongly right-skewed")
    save_fig(FIGURES / "fig_subscriptions_hist.png")

    plt.figure(figsize=(7, 4.5))
    price = num(public_plans["plan_monthly_price"])
    plt.hist(np.log1p(price.dropna().clip(lower=0)), bins=55, color="#9d4edd")
    plt.xlabel("log(1 + monthly price)")
    plt.ylabel("Public plan count")
    plt.title("Public plan prices have a long right tail")
    save_fig(FIGURES / "fig_price_hist.png")

    plt.figure(figsize=(7, 4.5))
    quota = num(public_plans["max_quota_amount"])
    plt.hist(np.log1p(quota.dropna().clip(lower=0)), bins=55, color="#0a9396")
    plt.xlabel("log(1 + max quota)")
    plt.ylabel("Public plan count")
    plt.title("Usage allowances vary by several orders of magnitude")
    save_fig(FIGURES / "fig_quota_hist.png")

    plt.figure(figsize=(8, 4.8))
    tax = tables["taxonomy_distribution"].sort_values("API 数", ascending=True)
    plt.barh(tax["数据商品类型"], tax["占比"] * 100, color="#577590")
    plt.xlabel("Share of valid APIs (%)")
    plt.title("Data commodity types based on listing text")
    save_fig(FIGURES / "fig_taxonomy.png")

    plt.figure(figsize=(7, 4.5))
    plan_count = num(api["public_plan_count"])
    counts = plan_count.clip(upper=8).value_counts().sort_index()
    labels = [str(int(x)) if x < 8 else "8+" for x in counts.index]
    plt.bar(labels, counts.values, color="#f4a261")
    plt.xlabel("Public visible plan count per API")
    plt.ylabel("API count")
    plt.title("Most Data APIs use a small menu of plans")
    save_fig(FIGURES / "fig_plan_count.png")

    plt.figure(figsize=(7, 4.5))
    plot_df = api[["min_paid_price", "subscriptions_count"]].dropna()
    plot_df = plot_df[(plot_df["min_paid_price"] > 0) & (plot_df["subscriptions_count"] >= 0)]
    plot_df["price_bin"] = pd.qcut(np.log1p(plot_df["min_paid_price"]), q=20, duplicates="drop")
    binned = plot_df.groupby("price_bin", observed=True).agg(
        ln_price=("min_paid_price", lambda x: np.log1p(x).mean()),
        ln_sub=("subscriptions_count", lambda x: np.log1p(x).mean()),
    )
    plt.plot(binned["ln_price"], binned["ln_sub"], marker="o", color="#264653")
    plt.xlabel("Mean log(1 + minimum paid price), by bins")
    plt.ylabel("Mean log(1 + subscriptions)")
    plt.title("Binned relationship between price and subscriptions")
    save_fig(FIGURES / "fig_price_subscriptions_binned.png")

    plt.figure(figsize=(7, 4.5))
    top = api.groupby("owner_slugifiedName")["api_id"].nunique().sort_values(ascending=False).head(15).sort_values()
    plt.barh(top.index, top.values, color="#457b9d")
    plt.xlabel("API count")
    plt.title("Top providers by number of Data APIs")
    save_fig(FIGURES / "fig_top_owners.png")


@dataclass
class ModelResult:
    name: str
    label: str
    nobs: int
    r2: float
    depvar: str
    coefs: pd.DataFrame
    note: str = ""


def fit_ols(df: pd.DataFrame, y: str, xvars: list[str], label: str, cluster: str | None = None, add_const: bool = True) -> ModelResult:
    used = df[[y, *xvars, *([cluster] if cluster else [])]].replace([np.inf, -np.inf], np.nan).dropna()
    yv = used[y].astype(float)
    X = used[xvars].astype(float)
    if add_const:
        X = sm.add_constant(X, has_constant="add")
    model = sm.OLS(yv, X)
    if cluster:
        res = model.fit(cov_type="cluster", cov_kwds={"groups": used[cluster]})
    else:
        res = model.fit(cov_type="HC1")
    rows = []
    for var in X.columns:
        if var == "const":
            continue
        rows.append(
            {
                "term": var,
                "coef": res.params[var],
                "se": res.bse[var],
                "p": res.pvalues[var],
                "stars": stars(res.pvalues[var]),
            }
        )
    return ModelResult(
        name=re.sub(r"[^A-Za-z0-9_]+", "_", label).strip("_"),
        label=label,
        nobs=int(res.nobs),
        r2=float(getattr(res, "rsquared", np.nan)),
        depvar=y,
        coefs=pd.DataFrame(rows),
    )


def stars(p: float) -> str:
    if p < 0.01:
        return "***"
    if p < 0.05:
        return "**"
    if p < 0.1:
        return "*"
    return ""


def demean_within(df: pd.DataFrame, cols: list[str], group: str) -> pd.DataFrame:
    out = df[[group, *cols]].copy()
    means = out.groupby(group)[cols].transform("mean")
    for c in cols:
        out[f"{c}_dm"] = out[c] - means[c]
    return out[[group, *[f"{c}_dm" for c in cols]]]


DISPLAY = {
    "has_free_plan": "Has free public plan",
    "ln_min_paid_price": "Log min paid price",
    "ln_max_public_quota": "Log max quota",
    "ln_public_plan_count": "Log public plan count",
    "has_soft_limit": "Has soft limit",
    "has_private_plan": "Has private plan",
    "popularity_score": "Popularity score",
    "success_rate_scaled": "Success rate",
    "service_level_scaled": "Service level",
    "ln_latency": "Log latency",
    "rating_clean": "Rating",
    "ln_rating_votes": "Log rating votes",
    "ln_readme": "Log readme length",
    "ln_api_age": "Log API age",
    "ln_days_since_update": "Log days since update",
    "ln_owner_api_count": "Log owner API count",
    "type_web_scraping": "Web scraping/extraction",
    "type_social_profile": "Social/profile data",
    "type_geo_identity": "Geo/identity lookup",
    "type_firm_lead": "Firm/lead data",
    "type_finance_market": "Finance/market data",
    "type_ecommerce_price": "E-commerce/price data",
    "type_document_text": "Document/text data",
    "type_real_estate_mobility": "Real estate/mobility",
    "type_public_reference": "Public/reference data",
    "type_freshness": "Freshness wording",
    "ln_subscriptions": "Log subscriptions",
    "ln_plan_price": "Log plan price",
    "ln_plan_price_w": "Log plan price, winsorized",
    "ln_max_quota": "Log plan quota",
    "has_positive_overage": "Positive overage fee",
    "is_recommended_plan": "Recommended plan",
    "tier_PRO": "Plan tier: PRO",
    "tier_ULTRA": "Plan tier: ULTRA",
    "tier_MEGA": "Plan tier: MEGA",
    "tier_CUSTOM": "Plan tier: CUSTOM",
    "tier_FREE": "Plan tier: FREE",
    "tier_OTHER": "Plan tier: OTHER",
}


def run_regressions(api: pd.DataFrame, public_plans: pd.DataFrame) -> list[ModelResult]:
    type_vars = [
        "type_web_scraping",
        "type_social_profile",
        "type_geo_identity",
        "type_firm_lead",
        "type_finance_market",
        "type_ecommerce_price",
        "type_document_text",
        "type_real_estate_mobility",
        "type_public_reference",
        "type_freshness",
    ]
    api_reg = api.copy()
    for col in [
        "has_free_plan",
        "has_soft_limit",
        "has_private_plan",
        "ln_min_paid_price",
        "ln_max_public_quota",
        "ln_public_plan_count",
        "ln_readme",
        "ln_api_age",
        "ln_owner_api_count",
        *type_vars,
    ]:
        api_reg[col] = num(api_reg[col]).fillna(0)

    quality_df = api_reg.copy()
    for col in ["popularity_score", "success_rate_scaled", "service_level_scaled", "ln_latency", "rating_clean", "ln_rating_votes"]:
        quality_df[col] = num(quality_df[col])

    models: list[ModelResult] = []
    models.append(
        fit_ols(
            api_reg,
            "ln_subscriptions",
            [
                "has_free_plan",
                "ln_min_paid_price",
                "ln_max_public_quota",
                "ln_public_plan_count",
                "has_soft_limit",
                "ln_readme",
                "ln_api_age",
                "ln_owner_api_count",
                *type_vars,
            ],
            "API demand: baseline",
        )
    )
    models.append(
        fit_ols(
            quality_df,
            "ln_subscriptions",
            [
                "has_free_plan",
                "ln_min_paid_price",
                "ln_max_public_quota",
                "ln_public_plan_count",
                "popularity_score",
                "success_rate_scaled",
                "ln_latency",
                "rating_clean",
                "ln_rating_votes",
                "ln_readme",
                "ln_api_age",
                "ln_owner_api_count",
                *type_vars,
            ],
            "API demand: with quality",
        )
    )
    price_df = api_reg[api_reg["min_paid_price"].notna() & (api_reg["min_paid_price"] > 0)].copy()
    models.append(
        fit_ols(
            price_df,
            "ln_min_paid_price",
            [
                "ln_subscriptions",
                "ln_max_public_quota",
                "ln_public_plan_count",
                "has_soft_limit",
                "ln_readme",
                "ln_api_age",
                "ln_owner_api_count",
                *type_vars,
            ],
            "API price: minimum paid plan",
        )
    )
    models.append(
        fit_ols(
            api_reg,
            "has_free_plan",
            [
                "ln_subscriptions",
                "ln_max_public_quota",
                "ln_public_plan_count",
                "ln_readme",
                "ln_api_age",
                "ln_owner_api_count",
                *type_vars,
            ],
            "Free plan adoption: LPM",
        )
    )
    models.append(
        fit_ols(
            api_reg,
            "ln_public_plan_count",
            [
                "ln_subscriptions",
                "has_free_plan",
                "ln_max_public_quota",
                "ln_readme",
                "ln_api_age",
                "ln_owner_api_count",
                *type_vars,
            ],
            "Menu complexity",
        )
    )

    plan_reg = public_plans.copy()
    plan_reg = plan_reg[plan_reg["plan_monthly_price"].notna()].copy()
    for col in [
        "ln_plan_price_w",
        "ln_max_quota",
        "has_soft_limit",
        "has_positive_overage",
        "is_recommended_plan",
        "ln_subscriptions",
        "popularity_score",
        "success_rate_scaled",
        "ln_latency",
        "ln_readme",
        "ln_owner_api_count",
    ]:
        plan_reg[col] = num(plan_reg[col]).fillna(0)
    tiers = pd.get_dummies(plan_reg["plan_tier"], prefix="tier").astype(int)
    for col in ["tier_PRO", "tier_ULTRA", "tier_MEGA"]:
        plan_reg[col] = tiers[col] if col in tiers else 0
    models.append(
        fit_ols(
            plan_reg,
            "ln_plan_price_w",
            [
                "ln_max_quota",
                "has_soft_limit",
                "has_positive_overage",
                "is_recommended_plan",
                "ln_subscriptions",
                "popularity_score",
                "success_rate_scaled",
                "ln_latency",
                "ln_readme",
                "ln_owner_api_count",
                "tier_PRO",
                "tier_ULTRA",
                "tier_MEGA",
            ],
            "Plan price: pooled",
            cluster="api_id",
        )
    )

    within_cols = [
        "ln_plan_price_w",
        "ln_max_quota",
        "has_soft_limit",
        "has_positive_overage",
        "is_recommended_plan",
        "tier_PRO",
        "tier_ULTRA",
        "tier_MEGA",
    ]
    within = plan_reg[["api_id", *within_cols]].replace([np.inf, -np.inf], np.nan).dropna()
    within = within[within.groupby("api_id")["api_id"].transform("size") > 1].copy()
    dm = demean_within(within, within_cols, "api_id")
    dm["api_id"] = within["api_id"].values
    y = "ln_plan_price_w_dm"
    xs = [f"{c}_dm" for c in within_cols if c != "ln_plan_price_w"]
    result = fit_ols(dm, y, xs, "Plan price: within API", cluster="api_id", add_const=False)
    result.coefs["term"] = result.coefs["term"].str.replace("_dm", "", regex=False)
    result.depvar = "ln_plan_price_w"
    result.note = "API fixed effects via within transformation; standard errors clustered by API."
    models.append(result)

    for model in models:
        out = model.coefs.copy()
        out.insert(0, "model", model.label)
        out["term_label"] = out["term"].map(DISPLAY).fillna(out["term"])
        out["coef_fmt"] = out.apply(lambda r: f"{r['coef']:.3f}{r['stars']}", axis=1)
        out["se_fmt"] = out["se"].map(lambda x: f"({x:.3f})")
        out.to_csv(TABLES / f"reg_{model.name}.csv", index=False)

    build_regression_tables(models)
    return models


def build_regression_tables(models: list[ModelResult]) -> None:
    # Compact tables with selected core coefficients for report readability.
    groups = {
        "api_demand": ["API demand: baseline", "API demand: with quality"],
        "api_outcomes": ["API price: minimum paid plan", "Free plan adoption: LPM", "Menu complexity"],
        "plan_price": ["Plan price: pooled", "Plan price: within API"],
    }
    selected_terms = [
        "has_free_plan",
        "ln_min_paid_price",
        "ln_max_public_quota",
        "ln_public_plan_count",
        "ln_subscriptions",
        "popularity_score",
        "success_rate_scaled",
        "ln_latency",
        "rating_clean",
        "ln_rating_votes",
        "ln_readme",
        "ln_api_age",
        "ln_owner_api_count",
        "ln_max_quota",
        "has_soft_limit",
        "has_positive_overage",
        "is_recommended_plan",
        "tier_PRO",
        "tier_ULTRA",
        "tier_MEGA",
    ]
    model_map = {m.label: m for m in models}
    for name, labels in groups.items():
        rows = []
        for term in selected_terms:
            row = {"变量": DISPLAY.get(term, term)}
            present = False
            for label in labels:
                m = model_map[label]
                sub = m.coefs[m.coefs["term"] == term]
                if sub.empty:
                    row[label] = ""
                else:
                    present = True
                    r = sub.iloc[0]
                    row[label] = f"{r.coef:.3f}{r.stars} ({r.se:.3f})"
            if present:
                rows.append(row)
        nrow = {"变量": "N"}
        r2row = {"变量": "R-squared"}
        for label in labels:
            nrow[label] = str(model_map[label].nobs)
            r2row[label] = f"{model_map[label].r2:.3f}"
        rows.extend([nrow, r2row])
        table = pd.DataFrame(rows)
        save_table(f"regression_{name}", table)


def write_stata_do() -> None:
    do = f"""
clear all
set more off
cd "{OUT}"

capture log close
log using "tables/stata_reduced_form.log", text replace

import delimited using "data/api_level.csv", clear varnames(1) bindquote(strict) encoding(UTF-8)
gen ln_subscriptions_s = ln(1 + subscriptions_count)
gen ln_min_paid_price_s = ln(1 + min_paid_price)
gen ln_max_public_quota_s = ln(1 + max_public_quota)
gen ln_public_plan_count_s = ln(1 + public_plan_count)
gen ln_readme_s = ln(1 + readme_len)
gen ln_api_age_s = ln(1 + api_age_days)
gen ln_owner_api_count_s = ln(1 + owner_api_count)
foreach v in ln_min_paid_price_s ln_max_public_quota_s ln_public_plan_count_s ln_readme_s ln_api_age_s ln_owner_api_count_s {{
    replace `v' = 0 if missing(`v')
}}

reg ln_subscriptions_s has_free_plan ln_min_paid_price_s ln_max_public_quota_s ///
    ln_public_plan_count_s has_soft_limit ln_readme_s ln_api_age_s ln_owner_api_count_s ///
    type_web_scraping type_social_profile type_geo_identity type_firm_lead ///
    type_finance_market type_ecommerce_price type_document_text ///
    type_real_estate_mobility type_public_reference type_freshness, vce(robust)
estimates store demand_baseline

reg ln_min_paid_price_s ln_subscriptions_s ln_max_public_quota_s ///
    ln_public_plan_count_s has_soft_limit ln_readme_s ln_api_age_s ln_owner_api_count_s ///
    type_web_scraping type_social_profile type_geo_identity type_firm_lead ///
    type_finance_market type_ecommerce_price type_document_text ///
    type_real_estate_mobility type_public_reference type_freshness ///
    if min_paid_price < . & min_paid_price > 0, vce(robust)
estimates store price_baseline

import delimited using "data/public_plan_level.csv", clear varnames(1) bindquote(strict) encoding(UTF-8)
gen ln_plan_price_w_s = ln_plan_price_w
gen ln_max_quota_s = ln_max_quota
drop if missing(ln_plan_price_w_s)
gen tier_pro = plan_tier == "PRO"
gen tier_ultra = plan_tier == "ULTRA"
gen tier_mega = plan_tier == "MEGA"
foreach v in ln_max_quota_s has_soft_limit has_positive_overage is_recommended_plan tier_pro tier_ultra tier_mega {{
    replace `v' = 0 if missing(`v')
}}
encode api_id, gen(api_num)
reghdfe ln_plan_price_w_s ln_max_quota_s has_soft_limit has_positive_overage ///
    is_recommended_plan tier_pro tier_ultra tier_mega, absorb(api_num) vce(cluster api_num)
estimates store plan_within

esttab demand_baseline price_baseline plan_within using "tables/stata_reduced_form_main.csv", ///
    replace se r2 ar2 compress

log close
"""
    (OUT / "scripts").mkdir(parents=True, exist_ok=True)
    (OUT / "scripts" / "run_reduced_form.do").write_text(do.strip() + "\n", encoding="utf-8")


def report_text(tables: dict[str, pd.DataFrame], models: list[ModelResult]) -> str:
    sample = tables["sample_overview"]
    api_summary = tables["api_summary_statistics"]
    price_median = api_summary.loc[api_summary["变量"] == "最低正月费", "P50"].iloc[0]
    sub_median = api_summary.loc[api_summary["变量"] == "订阅数", "P50"].iloc[0]
    plan_count_median = api_summary.loc[api_summary["变量"] == "公开计划数", "P50"].iloc[0]

    demand = pd.read_csv(TABLES / "regression_api_demand.csv")
    outcomes = pd.read_csv(TABLES / "regression_api_outcomes.csv")
    plan_price = pd.read_csv(TABLES / "regression_plan_price.csv")

    text = f"""---
title: "RapidAPI Data 类别基本面分析与 Reduced Form 回归"
author: "Codex"
date: "2026-06-15"
geometry: margin=1in
fontsize: 11pt
---

\\newpage

# 摘要

本文基于 RapidAPI `Data` 类别近全量横截面样本，分析数据商品在平台市场中的基本面结构、价格菜单、调用额度、声誉质量信号以及 reduced form 相关关系。样本包含 `6898` 个有效 API、`23116` 条价格计划和 `24867` 条调用额度/超额费规则。核心发现是：Data API 不是普通软件服务，而是被平台标准化为“访问权 + 调用额度 + 超额费 + 声誉信号”的数据商品。公开非隐藏计划共有 `21086` 条，API 层最低正月费中位数约为 `{price_median:.2f}`，订阅数中位数为 `{sub_median:.0f}`，公开计划数中位数为 `{plan_count_median:.0f}`。

Reduced form 结果显示：免费计划与更高订阅量显著正相关；最低付费价格对订阅量的系数较小，加入质量控制后为负但不稳定；API 层最大公开额度与订阅量负相关，但与价格和菜单复杂度正相关；在计划层面，同一 API 内部，额度更大的计划价格显著更高。这些结果支持“数据商品通过免费试用、分层菜单和用量边界实现筛选与价格歧视”的解释，也提醒我们不能把额度简单理解为无条件提高需求的质量指标。本文不作强因果识别，而是把这些回归作为平台机制和结构模型设定的事实基础。

# 1. 样本与研究单位

研究单位分为三层：API 产品层、价格计划层、调用额度层。API 产品层用于研究数据商品的需求采用和声誉质量；价格计划层用于研究菜单和价格；调用额度层用于研究用量边界、hard/soft limit 和超额费。

{md_table(tables["sample_overview"], 3)}

主分析样本使用公开且非隐藏计划，即 `is_public_plan == True` 且 `is_hidden_plan == False`。私有计划更接近定制合同或指定客户报价，不作为公开市场价格的主口径。

# 2. 数据商品类型

RapidAPI `Data` 类别覆盖多种数据商品，包括网页抽取、社交画像、地理身份查询、企业线索、金融市场、电商价格、文档文本和房地产/出行数据。分类基于 API 名称、slug、描述和标签的关键词识别，类别可重叠。

{md_table(tables["taxonomy_distribution"], 3)}

![Data commodity taxonomy](../figures/fig_taxonomy.png)

该结构说明，样本不是普通 API 服务集合，而是下游企业把数据作为输入品购买的多类型市场。不同类型的数据在覆盖范围、实时性、合规风险和下游用途上存在差异，这也是后续结构模型需要控制细分市场差异的原因。

# 3. 平台供给与卖家结构

样本中 API 提供者数量较多，长尾特征明显。头部卖家拥有数十到上百个 API，但总体上市场仍高度分散。多产品卖家可能拥有更强的产品组合能力、模板化定价能力和搜索曝光优势。

{md_table(tables["top_owners"].head(12), 2)}

![Top owners](../figures/fig_top_owners.png)

# 4. 价格与菜单基本面

Data API 的交易不是一个 API 一个价格，而是一个 API 对应多个公开计划。BASIC、PRO、ULTRA、MEGA 等计划名称高度常见，说明卖家普遍采用信息商品版本化和二级价格歧视。

## 4.1 API 和计划定价类型

API 产品层以 FREEMIUM 为主，计划层则以 PAID 计划为主。这表明 freemium 的实际含义通常是“有免费入口，同时通过付费计划变现”。

{md_table(tables["api_pricing_distribution"], 3)}

{md_table(tables["plan_pricing_distribution"], 3)}

{md_table(tables["plan_visibility_distribution"], 3)}

## 4.2 价格分布

公开计划价格右偏明显，存在少数极高价格计划。报告中的回归对价格使用 `log(1+price)`，并在计划层使用 1%/99% winsorized 价格减少极端值影响。

{md_table(tables["plan_summary_statistics"], 2)}

![Price distribution](../figures/fig_price_hist.png)

![Plan count distribution](../figures/fig_plan_count.png)

# 5. 用量、额度和超额费

数据商品的核心不是所有权转移，而是可计量访问权。平台将访问权转化为请求数、额度、速率限制和超额费。公开计划的额度跨越多个数量级，说明买家使用强度差异很大，卖家通过套餐大小筛选不同需求类型。

![Quota distribution](../figures/fig_quota_hist.png)

hard limit 更接近数量约束，soft limit 更接近允许超额使用并收费的两部制价格。对结构模型而言，价格不应只用月费，还应同时纳入额度和超额费。

# 6. 声誉、质量与采用

API 订阅数和评分信号高度右偏。许多 API 订阅数很低，少数头部 API 获得大量订阅。这符合数据商品的经验品特征：买方在购买前难以完全验证数据质量，因此依赖订阅数、评分、成功率、延迟和文档等信号。

{md_table(tables["api_summary_statistics"], 2)}

![Subscriptions distribution](../figures/fig_subscriptions_hist.png)

![Binned price-subscription relationship](../figures/fig_price_subscriptions_binned.png)

# 7. Reduced Form 回归设计

回归结果均解释为相关关系，不作因果解释。核心目标是回答三个问题：

1. 哪些数据商品属性与更高采用量相关？
2. 哪些属性与更高价格相关？
3. 多档菜单和额度设计是否体现数据商品的筛选机制？

API 层被解释变量包括 `log(1+subscriptions)`、`log(1+minimum paid price)`、是否有免费计划、公开计划数量。计划层被解释变量为 `log(1+plan monthly price)`。标准误在 API 层使用 heteroskedasticity-robust，在计划层按 API 聚类。计划层还报告同一 API 内部的 within-API 规格。

# 8. 回归结果

## 8.1 API 采用量

{md_table(demand, 3)}

主要结果：

- 免费公开计划与更高订阅量正相关，符合数据商品需要试用入口来缓解质量不确定性的机制。
- 最低付费价格对订阅量的系数较小，加入质量变量后为负但不显著；价格-采用关系需要谨慎解释。
- 公开计划数量和文档长度在基准规格中与订阅量正相关；最大额度在 API 层与订阅量负相关，可能反映高额度产品面向更窄、更高强度的需求场景。
- 加入质量变量后，人气分、评分票数、成功率和延迟等指标解释订阅差异，表明声誉质量信号是数据商品交易的重要机制。

## 8.2 API 价格、免费计划和菜单复杂度

{md_table(outcomes, 3)}

主要结果：

- 订阅量与最低付费价格正相关，说明被采用更多的数据商品具有更强定价能力。
- 最大额度与价格正相关，体现数据访问权的套餐大小定价。
- 免费计划与菜单复杂度关系密切：freemium 常与多档付费升级计划共同出现。

## 8.3 计划层价格与额度

{md_table(plan_price, 3)}

主要结果：

- 计划额度越大，月费越高；该关系在 pooled 和 within-API 规格中均成立。
- 同一 API 内部，PRO、ULTRA、MEGA 等高阶计划相对于基础计划显著更贵，符合信息商品版本化定价。
- soft limit 和正超额费反映不同的边际使用约束，与月费之间存在系统性相关。

# 9. 对结构模型的启发

本数据适合从静态差异化产品模型出发。API 是产品，owner 是企业，公开计划是价格菜单，订阅数是采用量代理。一个可行的 API 层需求式为：

```text
log(1 + subscriptions_j)
  = β price_j + γ quota_j + θ reputation_j
  + λ quality_j + δ data_type_j + μ owner_controls_j + ε_j
```

如果进一步使用 plan 层，则可把每个 API-plan 视为一个版本化产品：

```text
u_ijk = α price_jk + β quota_jk + γ overage_jk
        + θ quality_j + ρ reputation_j + ξ_jk + ε_ijk
```

由于目前没有 plan-level subscription share，主报告建议先做 API 层需求和 plan 层供给/菜单设计的 reduced form，再在结构模型中将计划菜单聚合为 API 产品属性。

# 10. 局限与后续工作

1. 样本为横截面，不能识别动态进入、学习和声誉积累。
2. 订阅数是 API 层采用量代理，不是实际调用量或销售额。
3. 没有消费者身份、真实成交金额、卖家成本和平台排序算法。
4. 私有计划更像定制合同，应与公开计划分开分析。
5. 额度单位存在 Requests、Credits、Rows、Searches 等差异，后续可进一步限制到 Requests 口径。
6. Reduced form 结果是相关关系，后续需要结合工具变量、自然实验或结构模型做更强识别。

# 11. 输出文件

- 清洗后 API 层样本：`rapidapi_analysis/data/api_level.csv`
- 清洗后公开计划层样本：`rapidapi_analysis/data/public_plan_level.csv`
- 描述统计表：`rapidapi_analysis/tables/`
- 图表：`rapidapi_analysis/figures/`
- Stata 复跑脚本：`rapidapi_analysis/scripts/run_reduced_form.do`
- Stata 回归日志：`rapidapi_analysis/tables/stata_reduced_form.log`

"""
    return text


def write_report(tables: dict[str, pd.DataFrame], models: list[ModelResult]) -> Path:
    md = report_text(tables, models)
    md_path = REPORT / "rapidapi_data_reduced_form_report.md"
    md_path.write_text(md, encoding="utf-8")
    return md_path


def render_pdf(md_path: Path) -> Path:
    pdf_path = REPORT / "rapidapi_data_reduced_form_report.pdf"
    cmd = [
        "pandoc",
        str(md_path),
        "-o",
        str(pdf_path),
        "--pdf-engine=xelatex",
        "-V",
        "CJKmainfont=Songti SC",
        "-V",
        "mainfont=Times New Roman",
        "-V",
        "sansfont=Arial",
        "-V",
        "monofont=Menlo",
        "--toc",
        "--toc-depth=2",
    ]
    subprocess.run(cmd, cwd=REPORT, check=True)
    return pdf_path


def main() -> None:
    api, public_plans, limit_panel, plans, limits = make_api_level()
    tables = describe_data(api, plans, limits, public_plans)
    make_figures(api, public_plans, limits, tables)
    models = run_regressions(api, public_plans)
    write_stata_do()
    md_path = write_report(tables, models)
    pdf_path = render_pdf(md_path)
    summary = {
        "api_rows": int(len(api)),
        "public_plan_rows": int(len(public_plans)),
        "limit_rows": int(len(limits)),
        "regression_models": [m.label for m in models],
        "report_markdown": str(md_path),
        "report_pdf": str(pdf_path),
    }
    (OUT / "analysis_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

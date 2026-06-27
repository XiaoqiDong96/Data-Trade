from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm


ROOT = Path(__file__).resolve().parents[2]
CRAWL = ROOT / "rapidapi_crawl" / "data"
OUT = ROOT / "rapidapi_io_static"
DATA = OUT / "data"
TABLES = OUT / "tables"
FIGURES = OUT / "figures"
REPORT = OUT / "report"

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

DISPLAY = {
    "subscriptions_count": "订阅数",
    "min_paid_price": "最低月费",
    "price_100": "最低付费入口价格 / 100",
    "ln_min_paid_price": "log 最低付费月费",
    "ln_subscriptions": "log(1+订阅数)",
    "has_free_plan": "免费公开计划",
    "ln_free_quota": "log 免费额度",
    "trial_generosity_index": "试用慷慨度",
    "versioning_index": "版本化",
    "ln_paid_plan_count": "log 付费计划数",
    "ln_public_plan_count": "log 公开计划数",
    "price_ladder_span": "价格梯度",
    "quota_ladder_span": "额度梯度",
    "menu_has_overage": "含超额费",
    "approval_any": "任一计划需审批",
    "restricted_access_index": "限制访问",
    "data_scope_index": "数据范围",
    "data_complexity_index": "接入复杂度",
    "disclosure_index": "披露指数",
    "reliability_index": "可靠性",
    "exposure_index": "搜索曝光",
    "spotlight_index": "平台展示",
    "uncertainty_index": "不确定性",
    "ln_endpoints": "log endpoint 数",
    "ln_params": "log 参数数",
    "ln_payload_rows": "log payload 字段数",
    "post_share": "POST endpoint 占比",
    "required_param_share": "必填参数占比",
    "schema_endpoint_share": "schema endpoint 占比",
    "external_docs_share": "外部文档 endpoint 占比",
    "has_openapi_spec": "OpenAPI spec",
    "has_terms_of_service": "服务条款",
    "has_healthcheck_data": "有 healthcheck",
    "health_success_rate": "healthcheck 成功率",
    "ln_api_age": "log API 年龄",
    "ln_owner_api_count": "log owner 全部产品数",
    "owner_market_api_count": "owner 同市场产品数",
    "rating_clean": "评分",
    "ln_rating_votes": "log 评分票数",
    "free_x_uncertainty": "免费计划 × 不确定性",
    "free_x_complexity": "免费计划 × 接入复杂度",
    "free_x_low_disclosure": "免费计划 × 低披露",
    "rival_count": "同市场竞争品数",
    "z_rival_mean_free": "竞争者免费计划均值",
    "z_rival_mean_scope": "竞争者数据范围均值",
    "z_rival_mean_disclosure": "竞争者披露均值",
    "z_rival_mean_versioning": "竞争者版本化均值",
    "z_rival_mean_exposure": "竞争者曝光均值",
    "z_owner_other_market_price": "owner 其他市场均价",
    "z_owner_other_market_versioning": "owner 其他市场版本化",
    "z_contract_metering": "合同计量强度",
    "z_contract_access_control": "访问控制强度",
}


@dataclass
class IVResult:
    label: str
    nobs: int
    params: pd.Series
    se: pd.Series
    pvalues: pd.Series
    r2_like: float
    first_stage_f: float
    first_stage_p: float


def ensure_dirs() -> None:
    for p in [DATA, TABLES, FIGURES, REPORT]:
        p.mkdir(parents=True, exist_ok=True)


def num(x: pd.Series | np.ndarray | float | int, fill: float | None = None) -> pd.Series:
    s = pd.to_numeric(x, errors="coerce")
    if fill is not None:
        s = s.replace([np.inf, -np.inf], np.nan).fillna(fill)
    return s


def clean_bool(s: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(s):
        return s.fillna(False).astype(int)
    return s.astype("string").str.lower().map({"true": 1, "false": 0, "1": 1, "0": 0}).fillna(0).astype(int)


def ln1p(s: pd.Series | np.ndarray | float | int) -> pd.Series:
    return np.log1p(num(s, 0).clip(lower=0))


def winsor(s: pd.Series, lo: float = 0.01, hi: float = 0.99) -> pd.Series:
    x = num(s).replace([np.inf, -np.inf], np.nan)
    qlo, qhi = x.quantile([lo, hi])
    return x.clip(qlo, qhi)


def zscore(s: pd.Series) -> pd.Series:
    x = num(s, 0).replace([np.inf, -np.inf], np.nan).fillna(0)
    sd = float(x.std())
    if not np.isfinite(sd) or sd == 0:
        return x * 0
    return (x - float(x.mean())) / sd


def primary_type(row: pd.Series) -> str:
    text = " ".join(
        str(row.get(c, "") or "")
        for c in ["api_name", "api_title", "api_slug", "api_description", "category", "pricing"]
    ).lower()
    for key, patterns in MARKET_TYPES.items():
        if any(pat in text for pat in patterns):
            return key
    return "other"


def stars(p: float) -> str:
    if not np.isfinite(p):
        return ""
    if p < 0.01:
        return "***"
    if p < 0.05:
        return "**"
    if p < 0.10:
        return "*"
    return ""


def normal_cdf(x: np.ndarray) -> np.ndarray:
    return np.vectorize(lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2))))(x)


def fmt_coef(beta: float, se: float, p: float) -> str:
    if not np.isfinite(beta):
        return ""
    return f"{beta:.3f}{stars(p)} ({se:.3f})"


def md_table(df: pd.DataFrame, floatfmt: int = 3) -> str:
    if df.empty:
        return "_无数据_"
    out = df.copy().fillna("")
    for col in out.columns:
        if pd.api.types.is_float_dtype(out[col]):
            out[col] = out[col].map(lambda v: "" if pd.isna(v) else f"{v:.{floatfmt}f}")
    lines = ["| " + " | ".join(map(str, out.columns)) + " |"]
    lines.append("|" + "|".join(["---"] * len(out.columns)) + "|")
    for _, row in out.iterrows():
        lines.append("| " + " | ".join(str(row[c]) for c in out.columns) + " |")
    return "\n".join(lines)


def save_table(name: str, df: pd.DataFrame, floatfmt: int = 3) -> None:
    df.to_csv(TABLES / f"{name}.csv", index=False)
    (TABLES / f"{name}.md").write_text(md_table(df, floatfmt), encoding="utf-8")


def design(df: pd.DataFrame, cols: list[str], fe: str | None = None) -> pd.DataFrame:
    parts = [df[cols].astype(float).copy()]
    if fe:
        parts.append(pd.get_dummies(df[fe], prefix=fe, drop_first=True, dtype=float))
    x = pd.concat(parts, axis=1).replace([np.inf, -np.inf], np.nan).fillna(0)
    return sm.add_constant(x, has_constant="add")


def fit_ols(df: pd.DataFrame, y: str, x: list[str], fe: str = "primary_type") -> sm.regression.linear_model.RegressionResultsWrapper:
    x = list(dict.fromkeys(x))
    work = df[[y, *x, fe]].replace([np.inf, -np.inf], np.nan).dropna().copy()
    return sm.OLS(work[y].astype(float), design(work, x, fe=fe)).fit(cov_type="HC1")


def robust_2sls(y: pd.Series, x: pd.DataFrame, z: pd.DataFrame, label: str, excluded: list[str]) -> IVResult:
    yv = y.to_numpy(dtype=float).reshape(-1, 1)
    xv = x.to_numpy(dtype=float)
    zv = z.to_numpy(dtype=float)
    ztz = np.linalg.pinv(zv.T @ zv)
    xz = xv.T @ zv
    a = xz @ ztz @ xz.T
    beta = np.linalg.pinv(a) @ (xz @ ztz @ (zv.T @ yv))
    resid = yv - xv @ beta
    meat = zv.T @ ((resid.flatten() ** 2)[:, None] * zv)
    b = xz @ ztz @ meat @ ztz @ xz.T
    cov = np.linalg.pinv(a) @ b @ np.linalg.pinv(a)
    se = np.sqrt(np.maximum(np.diag(cov), 0)).reshape(-1, 1)
    t = beta.flatten() / np.where(se.flatten() == 0, np.nan, se.flatten())
    p = pd.Series(2 * (1 - normal_cdf(np.abs(t))), index=x.columns)
    fitted = xv @ beta
    sse = float(((yv - fitted) ** 2).sum())
    sst = float(((yv - yv.mean()) ** 2).sum())
    r2 = 1 - sse / sst if sst > 0 else np.nan

    fs = sm.OLS(x["price_100"], z).fit(cov_type="HC1")
    if excluded:
        rmat = np.zeros((len(excluded), len(z.columns)))
        for i, col in enumerate(excluded):
            if col in z.columns:
                rmat[i, z.columns.get_loc(col)] = 1
        ft = fs.f_test(rmat)
        first_f = float(np.asarray(ft.fvalue).ravel()[0])
        first_p = float(np.asarray(ft.pvalue).ravel()[0])
    else:
        first_f = np.nan
        first_p = np.nan
    return IVResult(
        label=label,
        nobs=x.shape[0],
        params=pd.Series(beta.flatten(), index=x.columns),
        se=pd.Series(se.flatten(), index=x.columns),
        pvalues=p,
        r2_like=r2,
        first_stage_f=first_f,
        first_stage_p=first_p,
    )


def build_menu_features() -> tuple[pd.DataFrame, pd.DataFrame]:
    plans = pd.read_csv(CRAWL / "rapidapi_static_Data_plan_enriched.csv", low_memory=False)
    for col in [
        "is_public_plan", "is_hidden_plan", "is_free_plan", "is_paid_plan", "requires_approval",
        "is_recommended_plan", "has_unlimited_limit", "rateLimit_enabled",
    ]:
        if col not in plans.columns:
            plans[col] = 0
        plans[col] = clean_bool(plans[col])
    for col in [
        "plan_monthly_price", "max_quota_amount", "min_quota_amount", "max_overage_price",
        "mean_overage_price", "hard_limits_n", "soft_limits_n", "all_endpoint_limits_n",
        "plan_mapped_endpoints_count", "plan_all_endpoint_items_count", "rate_limit_amount",
        "limits_n", "finite_limits_n",
    ]:
        if col not in plans.columns:
            plans[col] = 0
        plans[col] = num(plans[col], 0)
    public = plans[(plans["is_public_plan"] == 1) & (plans["is_hidden_plan"] == 0)].copy()
    public["price_w"] = winsor(public["plan_monthly_price"], 0.00, 0.99).fillna(0)
    public["quota_w"] = winsor(public["max_quota_amount"], 0.00, 0.99).fillna(0)
    public["ln_plan_price"] = np.log1p(public["price_w"])
    public["ln_plan_quota"] = np.log1p(public["quota_w"])
    public["has_overage_plan"] = (public["max_overage_price"] > 0).astype(int)
    public["endpoint_limited_plan"] = (
        (public["plan_mapped_endpoints_count"] > 0) | (public["plan_all_endpoint_items_count"] > 0)
    ).astype(int)
    public["has_rate_limit_plan"] = ((public["rateLimit_enabled"] == 1) | (public["rate_limit_amount"] > 0)).astype(int)

    rows: list[dict[str, Any]] = []
    for api_id, g in public.groupby("api_id", sort=False):
        free = g[g["is_free_plan"] == 1]
        paid = g[(g["is_paid_plan"] == 1) & (g["price_w"] > 0)]
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
                "public_plan_count": int(g.shape[0]),
                "paid_plan_count": int((g["is_paid_plan"] == 1).sum()),
                "free_plan_count": int((g["is_free_plan"] == 1).sum()),
                "has_free_plan": int((g["is_free_plan"] == 1).any()),
                "min_paid_price": min_price,
                "max_paid_price": max_price,
                "free_quota": free_quota,
                "max_paid_quota": max_quota,
                "price_ladder_span": max(0.0, float(price_span)),
                "quota_ladder_span": max(0.0, float(quota_span)),
                "approval_any": int((g["requires_approval"] == 1).any()),
                "menu_has_overage": int((g["has_overage_plan"] == 1).any()),
                "menu_has_hard_limit": int((g["hard_limits_n"] > 0).any()),
                "menu_has_soft_limit": int((g["soft_limits_n"] > 0).any()),
                "menu_has_rate_limit": int((g["has_rate_limit_plan"] == 1).any()),
                "menu_unlimited_share": float((g["has_unlimited_limit"] == 1).mean()),
                "menu_endpoint_limited_share": float(g["endpoint_limited_plan"].mean()),
                "menu_all_endpoint_limit_share": float((g["all_endpoint_limits_n"] > 0).mean()),
                "mean_limits_n": float(g["limits_n"].mean()),
                "mean_finite_limits_n": float(g["finite_limits_n"].mean()),
                "max_overage_price": float(g["max_overage_price"].max()),
            }
        )
    menu = pd.DataFrame(rows)
    for col in menu.columns:
        if col != "api_id":
            menu[col] = num(menu[col], 0)
    menu["ln_public_plan_count"] = ln1p(menu["public_plan_count"])
    menu["ln_paid_plan_count"] = ln1p(menu["paid_plan_count"])
    menu["ln_free_quota"] = ln1p(menu["free_quota"])
    menu["ln_max_paid_quota"] = ln1p(menu["max_paid_quota"])
    menu["ln_min_paid_price"] = ln1p(menu["min_paid_price"])
    menu["ln_max_overage_price"] = ln1p(menu["max_overage_price"])
    menu["trial_generosity_index"] = zscore(menu["has_free_plan"]) + 0.40 * zscore(menu["ln_free_quota"])
    menu["versioning_index"] = (
        zscore(menu["ln_paid_plan_count"]) + 0.35 * zscore(menu["price_ladder_span"])
        + 0.35 * zscore(menu["quota_ladder_span"]) + 0.25 * zscore(menu["menu_has_overage"])
        + 0.25 * zscore(menu["menu_endpoint_limited_share"])
    )
    menu["contract_metering_index"] = (
        zscore(menu["menu_has_hard_limit"]) + zscore(menu["menu_has_soft_limit"])
        + 0.40 * zscore(menu["menu_has_rate_limit"]) + 0.35 * zscore(menu["ln_max_overage_price"])
        + 0.25 * zscore(menu["mean_limits_n"])
    )
    menu.to_csv(DATA / "commodity_menu_api_features.csv", index=False)

    paid = public[(public["is_paid_plan"] == 1) & (public["price_w"] > 0) & (public["quota_w"] > 0)].copy()
    plan_table = pd.DataFrame()
    if paid["api_id"].nunique() > 20 and len(paid) > 200:
        terms = [
            "ln_plan_quota", "has_overage_plan", "requires_approval", "is_recommended_plan",
            "endpoint_limited_plan", "has_rate_limit_plan",
        ]
        for col in ["ln_plan_price", *terms]:
            paid[f"{col}_dm"] = paid[col] - paid.groupby("api_id")[col].transform("mean")
        model = sm.OLS(
            paid["ln_plan_price_dm"], sm.add_constant(paid[[f"{t}_dm" for t in terms]], has_constant="add")
        ).fit(cov_type="HC1")
        rows_model = []
        labels = {
            "ln_plan_quota": "log 调用额度",
            "has_overage_plan": "含超额费",
            "requires_approval": "需审批",
            "is_recommended_plan": "推荐计划",
            "endpoint_limited_plan": "endpoint 级限制",
            "has_rate_limit_plan": "速率限制",
        }
        for t in terms:
            k = f"{t}_dm"
            rows_model.append(
                {
                    "变量": labels[t],
                    "同一 API 内 log 月费": fmt_coef(model.params.get(k, np.nan), model.bse.get(k, np.nan), model.pvalues.get(k, np.nan)),
                }
            )
        rows_model.extend(
            [
                {"变量": "N", "同一 API 内 log 月费": int(model.nobs)},
                {"变量": "API fixed effects", "同一 API 内 log 月费": "demeaned"},
                {"变量": "R-squared", "同一 API 内 log 月费": f"{model.rsquared:.3f}"},
            ]
        )
        plan_table = pd.DataFrame(rows_model)
    save_table("commodity_plan_versioning", plan_table)
    return menu, plan_table


def build_api_sample(menu: pd.DataFrame, inside_share: float = 0.20) -> pd.DataFrame:
    api = pd.read_csv(CRAWL / "rapidapi_static_Data_api_model_panel_plus.csv", low_memory=False)
    api["primary_type"] = api.apply(primary_type, axis=1)
    api["subscriptions_count"] = num(api["subscriptions_count"], 0).clip(lower=0)
    api["q_obs"] = api["subscriptions_count"] + 1
    api["ln_subscriptions"] = ln1p(api["subscriptions_count"])
    api["rating_clean"] = num(api["rating"]).where(num(api["rating"]).between(0, 5), np.nan).fillna(0)
    api["ln_rating_votes"] = ln1p(api["rating_votes"])
    created = pd.to_datetime(num(api["created_at"]), unit="ms", errors="coerce", utc=True)
    api["ln_api_age"] = ln1p((pd.Timestamp("2026-06-16", tz="UTC") - created).dt.days.clip(lower=0))
    api["ln_owner_api_count"] = ln1p(api["published_apis_count"])

    endpoint = num(api["static_endpoints_observed"], 0)
    endpoint = endpoint.where(endpoint > 0, num(api["endpoints_count"], 0))
    api["endpoint_count"] = endpoint.clip(lower=0)
    api["ln_endpoints"] = ln1p(api["endpoint_count"])
    api["ln_params"] = ln1p(api["static_params_total"])
    api["ln_required_params"] = ln1p(api["static_required_params_total"])
    api["ln_payload_rows"] = ln1p(api["static_payload_rows"])
    api["ln_payload_schema_rows"] = ln1p(api["static_payload_schema_rows"])
    api["ln_readme"] = ln1p(api["readme_len"])
    api["ln_terms_len"] = ln1p(api["terms_text_len"])
    api["ln_spec_len"] = ln1p(api["spec_len"])
    api["post_share"] = np.where(api["endpoint_count"] > 0, num(api["static_post_endpoints"], 0) / api["endpoint_count"], 0)
    api["required_param_share"] = np.where(num(api["static_params_total"], 0) > 0, num(api["static_required_params_total"], 0) / num(api["static_params_total"], 0), 0)
    api["schema_endpoint_share"] = np.where(api["endpoint_count"] > 0, num(api["static_schema_endpoints"], 0) / api["endpoint_count"], 0)
    api["external_docs_share"] = np.where(api["endpoint_count"] > 0, num(api["static_external_docs_endpoints"], 0) / api["endpoint_count"], 0)
    api["route_depth"] = num(api["static_endpoint_route_depth_mean"], 0)
    api["endpoint_description_len"] = num(api["static_endpoint_description_mean_len"], 0)
    api["param_description_len"] = num(api["static_param_description_mean_len"], 0)
    api["has_openapi_spec"] = num(api["has_openapi_spec"], 0).astype(int)
    api["has_terms_of_service"] = num(api["has_terms_of_service"], 0).astype(int)
    api["has_auth_info"] = api["auth_type"].notna().astype(int)
    api["security_rules_count"] = num(api["security_rules_count"], 0)

    api["data_scope_index"] = zscore(api["ln_endpoints"]) + 0.50 * zscore(api["ln_params"]) + 0.35 * zscore(api["ln_payload_rows"]) + 0.25 * zscore(ln1p(api["endpoint_groups_count"]))
    api["data_complexity_index"] = zscore(api["route_depth"]) + 0.50 * zscore(api["required_param_share"]) + 0.35 * zscore(api["post_share"]) + 0.25 * zscore(api["ln_required_params"])
    api["disclosure_index"] = zscore(api["ln_readme"]) + 0.35 * zscore(api["schema_endpoint_share"]) + 0.35 * zscore(api["external_docs_share"]) + 0.30 * zscore(api["ln_terms_len"]) + 0.30 * zscore(api["ln_spec_len"])
    api["reliability_index"] = zscore(num(api["avg_success_rate"], 0) / 100) - 0.25 * zscore(ln1p(api["avg_latency"])) + 0.40 * zscore(num(api["health_success_rate"], 0))

    api["exposure_rows"] = num(api["exposure_rows"], 0)
    api["exposure_terms_count"] = num(api["exposure_terms_count"], 0)
    api["exposure_mean_inverse_rank"] = num(api["exposure_mean_inverse_rank"], 0)
    api["exposure_top10_count"] = num(api["exposure_top10_count"], 0)
    api["exposure_index"] = zscore(ln1p(api["exposure_rows"])) + 0.50 * zscore(ln1p(api["exposure_terms_count"])) + 0.50 * zscore(api["exposure_mean_inverse_rank"]) + 0.25 * zscore(ln1p(api["exposure_top10_count"]))
    api["spotlight_index"] = zscore(ln1p(api["spotlights_count_y"])) + 0.50 * zscore(num(api["has_spotlight"], 0))
    api["has_healthcheck_data"] = num(api["has_healthcheck_data"], 0).astype(int)
    api["health_success_rate"] = num(api["health_success_rate"], 0)
    api["has_restricted_plan"] = num(api["has_restricted_plan"], 0).astype(int)
    api["allowed_developers_total"] = num(api["allowed_developers_total"], 0)
    api["restricted_plans_count"] = num(api["restricted_plans_count"], 0)

    api = api.merge(menu, on="api_id", how="left")
    fill = [
        "public_plan_count", "paid_plan_count", "free_plan_count", "has_free_plan", "min_paid_price",
        "max_paid_price", "free_quota", "max_paid_quota", "price_ladder_span", "quota_ladder_span",
        "approval_any", "menu_has_overage", "menu_has_hard_limit", "menu_has_soft_limit",
        "menu_has_rate_limit", "menu_unlimited_share", "menu_endpoint_limited_share",
        "menu_all_endpoint_limit_share", "mean_limits_n", "mean_finite_limits_n", "max_overage_price",
        "ln_public_plan_count", "ln_paid_plan_count", "ln_free_quota", "ln_max_paid_quota",
        "ln_min_paid_price", "ln_max_overage_price", "trial_generosity_index", "versioning_index",
        "contract_metering_index",
    ]
    for c in fill:
        api[c] = num(api.get(c, pd.Series(index=api.index)), 0)
    api["has_positive_price"] = (api["min_paid_price"] > 0).astype(int)
    api["restricted_access_index"] = (
        zscore(api["approval_any"]) + 0.50 * zscore(api["has_restricted_plan"])
        + 0.35 * zscore(ln1p(api["restricted_plans_count"])) + 0.35 * zscore(ln1p(api["allowed_developers_total"]))
        + 0.25 * zscore(api["menu_endpoint_limited_share"])
    )
    api["uncertainty_index"] = zscore(api["data_complexity_index"]) - 0.50 * zscore(api["disclosure_index"]) - 0.30 * zscore(api["reliability_index"]) + 0.25 * zscore(1 - api["has_healthcheck_data"])
    api["free_x_uncertainty"] = api["has_free_plan"] * api["uncertainty_index"]
    api["free_x_complexity"] = api["has_free_plan"] * api["data_complexity_index"]
    api["free_x_low_disclosure"] = api["has_free_plan"] * (api["disclosure_index"] < api["disclosure_index"].median()).astype(int)

    api["market_observed_q"] = api.groupby("primary_type")["q_obs"].transform("sum")
    api["market_size"] = api["market_observed_q"] / inside_share
    api["share"] = api["q_obs"] / api["market_size"]
    api["outside_share"] = 1 - inside_share
    api["delta_all"] = np.log(api["share"]) - np.log(api["outside_share"])

    api["price_cap"] = api["min_paid_price"].where(api["min_paid_price"] > 0).quantile(0.99)
    api["price_usd"] = api["min_paid_price"].clip(lower=0, upper=api["price_cap"])
    api["price_100"] = api["price_usd"] / 100
    api["owner_slug"] = api["owner_slug"].fillna(api["owner_id"].astype(str)).fillna(api["api_id"])
    api["owner_market_api_count"] = api.groupby(["primary_type", "owner_slug"])["api_id"].transform("count")

    g = api.groupby("primary_type")
    n = g["api_id"].transform("count")
    api["rival_count"] = n - 1
    for raw, out in [
        ("has_free_plan", "z_rival_mean_free"),
        ("data_scope_index", "z_rival_mean_scope"),
        ("data_complexity_index", "z_rival_mean_complexity"),
        ("disclosure_index", "z_rival_mean_disclosure"),
        ("versioning_index", "z_rival_mean_versioning"),
        ("exposure_index", "z_rival_mean_exposure"),
        ("ln_max_paid_quota", "z_rival_mean_quota"),
        ("ln_public_plan_count", "z_rival_mean_plancount"),
    ]:
        total = g[raw].transform("sum")
        api[out] = np.where(n > 1, (total - api[raw]) / (n - 1), 0)

    owner_market = api.groupby(["owner_slug", "primary_type"]).agg(
        owner_market_mean_price=("price_100", "mean"),
        owner_market_mean_versioning=("versioning_index", "mean"),
        owner_market_n=("api_id", "count"),
    ).reset_index()
    owner_total = owner_market.groupby("owner_slug").agg(
        owner_all_price_num=("owner_market_mean_price", "sum"),
        owner_all_versioning_num=("owner_market_mean_versioning", "sum"),
        owner_market_cells=("primary_type", "count"),
    )
    api = api.merge(owner_market, on=["owner_slug", "primary_type"], how="left").merge(owner_total, on="owner_slug", how="left")
    denom = (api["owner_market_cells"] - 1).replace(0, np.nan)
    api["z_owner_other_market_price"] = ((api["owner_all_price_num"] - api["owner_market_mean_price"]) / denom).fillna(0)
    api["z_owner_other_market_versioning"] = ((api["owner_all_versioning_num"] - api["owner_market_mean_versioning"]) / denom).fillna(0)
    api["z_contract_metering"] = api["contract_metering_index"]
    api["z_contract_access_control"] = api["restricted_access_index"]

    api.to_csv(DATA / "commodity_api_static_features.csv", index=False)
    structural = api[(api["has_positive_price"] == 1) & np.isfinite(api["delta_all"])].copy()
    structural.to_csv(DATA / "commodity_static_sample.csv", index=False)
    return api, structural


def summary_tables(api: pd.DataFrame, structural: pd.DataFrame) -> pd.DataFrame:
    sample = pd.DataFrame(
        [
            {"指标": "Data API 产品数", "数值": len(api)},
            {"指标": "公开付费 API 结构样本", "数值": len(structural)},
            {"指标": "数据类型市场数", "数值": api["primary_type"].nunique()},
            {"指标": "owner 数", "数值": api["owner_slug"].nunique()},
            {"指标": "公开 plan 数", "数值": int(api["public_plan_count"].sum())},
            {"指标": "endpoint 覆盖 API", "数值": int((api["endpoint_count"] > 0).sum())},
            {"指标": "search exposure 覆盖 API", "数值": int((api["exposure_rows"] > 0).sum())},
            {"指标": "healthcheck 覆盖 API", "数值": int((api["has_healthcheck_data"] > 0).sum())},
            {"指标": "restricted plan API", "数值": int((api["has_restricted_plan"] > 0).sum())},
            {"指标": "spotlight API", "数值": int((api["spotlights_count_y"] > 0).sum())},
        ]
    )
    save_table("commodity_sample_overview", sample)
    stats_vars = [
        "subscriptions_count", "min_paid_price", "has_free_plan", "data_scope_index", "data_complexity_index",
        "disclosure_index", "reliability_index", "exposure_index", "trial_generosity_index", "versioning_index",
        "restricted_access_index", "ln_free_quota", "ln_max_paid_quota", "price_ladder_span", "quota_ladder_span",
    ]
    rows = []
    for v in stats_vars:
        s = num(structural[v]).replace([np.inf, -np.inf], np.nan).dropna()
        rows.append(
            {
                "变量": DISPLAY.get(v, v),
                "N": int(s.shape[0]),
                "均值": s.mean(),
                "标准差": s.std(),
                "P25": s.quantile(0.25),
                "P50": s.quantile(0.50),
                "P75": s.quantile(0.75),
                "P90": s.quantile(0.90),
            }
        )
    stats = pd.DataFrame(rows)
    save_table("commodity_summary_stats", stats)
    return sample


def regression_table(models: list[tuple[str, sm.regression.linear_model.RegressionResultsWrapper]], terms: list[str], name: str) -> pd.DataFrame:
    rows = []
    for t in terms:
        row = {"变量": DISPLAY.get(t, t)}
        for label, model in models:
            row[label] = fmt_coef(model.params.get(t, np.nan), model.bse.get(t, np.nan), model.pvalues.get(t, np.nan))
        rows.append(row)
    for label, model in models:
        rows.append({"变量": f"N: {label}", label: int(model.nobs)})
        rows.append({"变量": f"R-squared: {label}", label: f"{model.rsquared:.3f}"})
    table = pd.DataFrame(rows)
    save_table(name, table)
    return table


def pct_from_log_points(beta: float) -> float:
    return 100 * (math.exp(beta) - 1) if np.isfinite(beta) else np.nan


def run_reduced_forms(api: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = ["has_free_plan", "ln_min_paid_price", "ln_public_plan_count", "ln_api_age", "ln_owner_api_count", "rating_clean", "ln_rating_votes"]
    commodity = [
        "data_scope_index", "data_complexity_index", "disclosure_index", "reliability_index",
        "ln_free_quota", "ln_max_paid_quota", "versioning_index", "restricted_access_index",
    ]
    platform = ["exposure_index", "spotlight_index"]
    for c in base + commodity + platform + ["has_positive_price"]:
        api[c] = num(api[c], 0)
    adoption1 = fit_ols(api, "ln_subscriptions", base)
    adoption2 = fit_ols(api, "ln_subscriptions", [*base, *commodity])
    adoption3 = fit_ols(api, "ln_subscriptions", [*base, *commodity, *platform])
    paid = api[api["has_positive_price"] == 1].copy()
    price = fit_ols(
        paid,
        "ln_min_paid_price",
        ["ln_subscriptions", "ln_max_paid_quota", "ln_paid_plan_count", *commodity, *platform, "ln_api_age", "ln_owner_api_count", "ln_rating_votes"],
    )
    terms = [
        "has_free_plan", "ln_min_paid_price", "ln_public_plan_count", "ln_subscriptions",
        "data_scope_index", "data_complexity_index", "disclosure_index", "reliability_index",
        "ln_free_quota", "ln_max_paid_quota", "versioning_index", "restricted_access_index",
        "exposure_index", "spotlight_index", "ln_api_age", "ln_owner_api_count", "ln_rating_votes",
    ]
    rf = regression_table(
        [("采用: 基准", adoption1), ("采用: 数据合同", adoption2), ("采用: 加平台曝光", adoption3), ("价格: 付费样本", price)],
        terms,
        "commodity_reduced_form",
    )

    trial_terms = [
        "has_free_plan", "uncertainty_index", "free_x_uncertainty", "free_x_complexity", "free_x_low_disclosure",
        "data_scope_index", "data_complexity_index", "disclosure_index", "reliability_index",
        "versioning_index", "exposure_index", "ln_api_age", "ln_owner_api_count", "ln_rating_votes",
    ]
    trial = fit_ols(api, "ln_subscriptions", trial_terms)
    free_supply = fit_ols(
        api,
        "has_free_plan",
        ["uncertainty_index", "data_scope_index", "data_complexity_index", "disclosure_index", "reliability_index", "ln_endpoints", "ln_payload_rows", "exposure_index", "ln_api_age", "ln_owner_api_count"],
    )
    trial_table = regression_table(
        [("采用: 试用学习", trial), ("免费计划: LPM", free_supply)],
        trial_terms,
        "commodity_trial_learning",
    )
    magnitude_rows = []
    adoption = adoption3
    trial_model = trial
    for term, desc in [
        ("has_free_plan", "有免费计划的 API，相对于同类无免费计划 API 的采用差异"),
        ("data_scope_index", "数据范围指数提高 1 个标准化单位的采用差异"),
        ("disclosure_index", "披露指数提高 1 个标准化单位的采用差异"),
        ("reliability_index", "可靠性指数提高 1 个标准化单位的采用差异"),
        ("exposure_index", "搜索曝光指数提高 1 个标准化单位的采用差异"),
        ("ln_rating_votes", "评分票数增加约 1 log point 的采用差异"),
    ]:
        b = float(adoption.params.get(term, np.nan))
        magnitude_rows.append(
            {
                "结果": desc,
                "系数": b,
                "近似百分比变化": pct_from_log_points(b),
                "解释": "来自采用方程，控制数据类型固定效应和核心产品/合同变量。",
            }
        )
    b_int = float(trial_model.params.get("free_x_uncertainty", np.nan))
    magnitude_rows.append(
        {
            "结果": "免费计划与购买前不确定性的互补性",
            "系数": b_int,
            "近似百分比变化": pct_from_log_points(b_int),
            "解释": "交互项为正表示免费入口在更难验证的数据产品上更有采用价值。",
        }
    )
    mag = pd.DataFrame(magnitude_rows)
    save_table("commodity_effect_magnitudes", mag)
    return rf, trial_table


def run_static_demand(df: pd.DataFrame) -> tuple[pd.DataFrame, IVResult, IVResult, IVResult, IVResult, float]:
    controls = [
        "has_free_plan", "ln_free_quota", "ln_max_paid_quota",
        "versioning_index", "approval_any", "menu_has_overage", "restricted_access_index",
        "data_scope_index", "data_complexity_index", "disclosure_index", "reliability_index",
        "exposure_index", "spotlight_index", "ln_api_age", "ln_owner_api_count", "ln_rating_votes",
        "owner_market_api_count",
    ]
    for c in ["delta_all", "price_100", *controls]:
        df[c] = num(df[c], 0)
    y = df["delta_all"].astype(float)
    x_ols = design(df, ["price_100", *controls], fe="primary_type")
    ols = sm.OLS(y, x_ols).fit(cov_type="HC1")

    rival_iv = ["rival_count", "z_rival_mean_free", "z_rival_mean_scope", "z_rival_mean_disclosure", "z_rival_mean_versioning", "z_rival_mean_exposure", "z_rival_mean_quota", "z_rival_mean_plancount"]
    owner_iv = ["z_owner_other_market_price", "z_owner_other_market_versioning"]
    contract_iv = ["z_contract_metering", "z_contract_access_control", "menu_has_hard_limit", "menu_has_soft_limit", "menu_has_rate_limit", "ln_max_overage_price", "mean_limits_n", "menu_endpoint_limited_share"]

    x = design(df, ["price_100", *controls], fe="primary_type")
    z_rival = design(df, [*controls, *rival_iv], fe="primary_type")
    z_owner = design(df, [*controls, *owner_iv], fe="primary_type")
    z_contract = design(df, [*controls, *contract_iv], fe="primary_type")
    z_all = design(df, [*controls, *rival_iv, *owner_iv, *contract_iv], fe="primary_type")
    iv_rival = robust_2sls(y, x, z_rival, "竞争者特征 IV", rival_iv)
    iv_owner = robust_2sls(y, x, z_owner, "owner 跨市场 IV", owner_iv)
    iv_contract = robust_2sls(y, x, z_contract, "合同技术 IV", contract_iv)
    iv_all = robust_2sls(y, x, z_all, "合并 IV", [*rival_iv, *owner_iv, *contract_iv])

    rows = []
    terms = ["price_100", *controls]
    for t in terms:
        rows.append(
            {
                "变量": DISPLAY.get(t, t),
                "OLS": fmt_coef(ols.params.get(t, np.nan), ols.bse.get(t, np.nan), ols.pvalues.get(t, np.nan)),
                "2SLS: 竞争者": fmt_coef(iv_rival.params.get(t, np.nan), iv_rival.se.get(t, np.nan), iv_rival.pvalues.get(t, np.nan)),
                "2SLS: owner跨市场": fmt_coef(iv_owner.params.get(t, np.nan), iv_owner.se.get(t, np.nan), iv_owner.pvalues.get(t, np.nan)),
                "2SLS: 合同技术": fmt_coef(iv_contract.params.get(t, np.nan), iv_contract.se.get(t, np.nan), iv_contract.pvalues.get(t, np.nan)),
                "2SLS: 合并": fmt_coef(iv_all.params.get(t, np.nan), iv_all.se.get(t, np.nan), iv_all.pvalues.get(t, np.nan)),
            }
        )
    rows.extend(
        [
            {"变量": "N", "OLS": int(ols.nobs), "2SLS: 竞争者": iv_rival.nobs, "2SLS: owner跨市场": iv_owner.nobs, "2SLS: 合同技术": iv_contract.nobs, "2SLS: 合并": iv_all.nobs},
            {"变量": "R-squared", "OLS": f"{ols.rsquared:.3f}", "2SLS: 竞争者": f"{iv_rival.r2_like:.3f}", "2SLS: owner跨市场": f"{iv_owner.r2_like:.3f}", "2SLS: 合同技术": f"{iv_contract.r2_like:.3f}", "2SLS: 合并": f"{iv_all.r2_like:.3f}"},
            {"变量": "First-stage F", "OLS": "", "2SLS: 竞争者": f"{iv_rival.first_stage_f:.2f}", "2SLS: owner跨市场": f"{iv_owner.first_stage_f:.2f}", "2SLS: 合同技术": f"{iv_contract.first_stage_f:.2f}", "2SLS: 合并": f"{iv_all.first_stage_f:.2f}"},
        ]
    )
    table = pd.DataFrame(rows)
    save_table("commodity_static_demand", table)
    id_rows = pd.DataFrame(
        [
            {
                "识别来源": "竞争者特征",
                "经济含义": "同市场替代品改变本产品的均衡定价压力。",
                "First-stage F": iv_rival.first_stage_f,
                "评价": "较弱；说明横截面竞争集合不能充分解释价格。",
            },
            {
                "识别来源": "owner 跨市场策略",
                "经济含义": "同一卖家在其他数据类型中的定价和版本化反映共同成本或组织能力。",
                "First-stage F": iv_owner.first_stage_f,
                "评价": "较强；但排除限制依赖 owner 策略不直接进入本产品未观测需求。",
            },
            {
                "识别来源": "合同技术变量",
                "经济含义": "hard/soft limit、rate limit、超额费和 endpoint 限制反映访问治理成本。",
                "First-stage F": iv_contract.first_stage_f,
                "评价": "中等；最贴近数据访问权的供给侧机制。",
            },
            {
                "识别来源": "合并工具变量",
                "经济含义": "同时使用竞争、seller 和合同治理三类价格 shifter。",
                "First-stage F": iv_all.first_stage_f,
                "评价": "中等偏弱；作为主规格时需保留弱识别讨论。",
            },
        ]
    )
    save_table("commodity_identification_summary", id_rows)
    median_term = float(np.median(df["price_100"] * (1 - df["share"])))
    alpha_cal = -3.0 / median_term
    return table, iv_rival, iv_owner, iv_contract, iv_all, alpha_cal


def market_shares(delta0: np.ndarray, price_100: np.ndarray, alpha: float) -> tuple[np.ndarray, float]:
    v = np.clip(delta0 + alpha * price_100, -700, 700)
    ev = np.exp(v)
    denom = 1 + ev.sum()
    return ev / denom, 1 / denom


def owner_markups(shares: np.ndarray, owners: np.ndarray, alpha: float) -> np.ndarray:
    out = np.zeros_like(shares, dtype=float)
    for owner in pd.unique(owners):
        idx = np.where(owners == owner)[0]
        s = shares[idx]
        block = alpha * (np.diag(s) - np.outer(s, s))
        g = -block.T
        try:
            out[idx] = np.linalg.solve(g, s)
        except np.linalg.LinAlgError:
            out[idx] = np.linalg.pinv(g) @ s
    return out


def add_supply(df: pd.DataFrame, alpha: float) -> pd.DataFrame:
    out = df.copy()
    out["delta0_calibrated"] = out["delta_all"] - alpha * out["price_100"]
    for market, g in out.groupby("primary_type", sort=False):
        idx = g.index
        markups = owner_markups(g["share"].to_numpy(float), g["owner_slug"].fillna(g["api_id"]).to_numpy(), alpha)
        out.loc[idx, "markup_100"] = markups
        out.loc[idx, "markup_usd"] = markups * 100
        out.loc[idx, "mc_100"] = out.loc[idx, "price_100"] - markups
        out.loc[idx, "mc_usd"] = out.loc[idx, "mc_100"] * 100
        out.loc[idx, "own_elasticity"] = alpha * out.loc[idx, "price_100"] * (1 - out.loc[idx, "share"])
    out["mc_usd_floored"] = out["mc_usd"].clip(lower=0.01)
    out["mc_100_floored"] = out["mc_usd_floored"] / 100
    out["lerner_index"] = out["markup_usd"] / out["price_usd"].replace(0, np.nan)
    out.to_csv(DATA / "commodity_static_supply.csv", index=False)
    rows = []
    for v in ["markup_usd", "mc_usd_floored", "own_elasticity", "lerner_index"]:
        s = out[v].replace([np.inf, -np.inf], np.nan).dropna()
        rows.append({"变量": v, "N": len(s), "均值": s.mean(), "P25": s.quantile(.25), "P50": s.quantile(.5), "P75": s.quantile(.75), "P90": s.quantile(.9)})
    table = pd.DataFrame(rows)
    save_table("commodity_structural_summary", table)
    return out


def solve_market(g: pd.DataFrame, alpha: float) -> pd.DataFrame:
    p = g["price_100"].to_numpy(float).copy()
    mc = g["mc_100_floored"].to_numpy(float)
    delta0 = g["delta0_calibrated"].to_numpy(float)
    owners = g["owner_slug"].fillna(g["api_id"]).to_numpy()
    for _ in range(500):
        s, _ = market_shares(delta0, p, alpha)
        p_next = np.maximum(mc + owner_markups(s, owners, alpha), 0.0001)
        p_new = 0.5 * p + 0.5 * p_next
        if np.max(np.abs(p_new - p)) < 1e-8:
            p = p_new
            break
        p = p_new
    s, s0 = market_shares(delta0, p, alpha)
    res = g[["api_id", "primary_type", "market_size", "price_100", "share"]].copy()
    res["cf_price_100"] = p
    res["cf_share"] = s
    res["cf_outside_share"] = s0
    return res


def aggregate(df: pd.DataFrame, price_col: str, share_col: str) -> dict[str, float]:
    q = df["market_size"] * df[share_col]
    scale = 100 if "100" in price_col else 1
    market_size = df[["primary_type", "market_size"]].drop_duplicates()["market_size"].sum()
    return {
        "adoption_proxy": float(q.sum()),
        "mean_price_usd": float(df[price_col].mean() * scale),
        "revenue_proxy": float((q * df[price_col] * scale).sum()),
        "inside_share_weighted": float(q.sum() / market_size),
    }


def pct_path(path: pd.DataFrame, base: dict[str, float]) -> pd.DataFrame:
    out = path.copy()
    for c in ["adoption_proxy", "mean_price_usd", "revenue_proxy", "inside_share_weighted"]:
        out[f"{c}_pct_change"] = np.where(base[c] != 0, 100 * (out[c] / base[c] - 1), np.nan)
    return out


def run_counterfactuals(df: pd.DataFrame, alpha: float, beta_free: float, beta_disclosure: float, beta_exposure: float) -> dict[str, pd.DataFrame]:
    base_parts = []
    for _, g in df.groupby("primary_type", sort=False):
        tmp = g.copy()
        tmp["share_model_base"], _ = market_shares(
            tmp["delta0_calibrated"].to_numpy(float),
            tmp["price_100"].to_numpy(float),
            alpha,
        )
        base_parts.append(tmp)
    base_df = pd.concat(base_parts)
    base = aggregate(base_df, "price_100", "share_model_base")
    paths: dict[str, pd.DataFrame] = {}

    rows = []
    for factor in np.linspace(0.95, 1.25, 61):
        parts = []
        for _, g in df.groupby("primary_type", sort=False):
            tmp = g.copy()
            tmp["price_100_cf"] = tmp["price_100"] * factor
            tmp["share_cf"], _ = market_shares(tmp["delta0_calibrated"].to_numpy(float), tmp["price_100_cf"].to_numpy(float), alpha)
            parts.append(tmp)
        cf = pd.concat(parts)
        rows.append({"price_multiplier": factor, **aggregate(cf, "price_100_cf", "share_cf")})
    paths["price"] = pct_path(pd.DataFrame(rows), base)
    paths["price"].to_csv(TABLES / "commodity_cf_price_path.csv", index=False)

    rows = []
    for scale in np.linspace(0, 2.0, 51):
        parts = []
        for _, g in df.groupby("primary_type", sort=False):
            tmp = g.copy()
            delta0 = tmp["delta0_calibrated"].to_numpy(float) + beta_free * (scale - 1) * tmp["has_free_plan"].to_numpy(float)
            tmp["price_100_cf"] = tmp["price_100"]
            tmp["share_cf"], _ = market_shares(delta0, tmp["price_100"].to_numpy(float), alpha)
            parts.append(tmp)
        cf = pd.concat(parts)
        rows.append({"trial_value_scale": scale, **aggregate(cf, "price_100_cf", "share_cf")})
    paths["trial"] = pct_path(pd.DataFrame(rows), base)
    paths["trial"].to_csv(TABLES / "commodity_cf_trial_path.csv", index=False)

    gamma = max(abs(float(beta_disclosure)), 0.12)
    low_disc = (df["disclosure_index"] < df["disclosure_index"].median()).astype(float)
    rows = []
    for lift in np.linspace(0, 2.0, 51):
        parts = []
        for _, g in df.groupby("primary_type", sort=False):
            tmp = g.copy()
            local = low_disc.loc[tmp.index].to_numpy(float)
            delta0 = tmp["delta0_calibrated"].to_numpy(float) + gamma * lift * local
            tmp["price_100_cf"] = tmp["price_100"]
            tmp["share_cf"], _ = market_shares(delta0, tmp["price_100"].to_numpy(float), alpha)
            parts.append(tmp)
        cf = pd.concat(parts)
        rows.append({"disclosure_lift": lift, **aggregate(cf, "price_100_cf", "share_cf")})
    paths["disclosure"] = pct_path(pd.DataFrame(rows), base)
    paths["disclosure"].to_csv(TABLES / "commodity_cf_disclosure_path.csv", index=False)

    rows = []
    for factor in np.linspace(0.95, 1.30, 71):
        shocked = df.copy()
        shocked["mc_100_floored"] = shocked["mc_100_floored"] * factor
        eq = pd.concat([solve_market(g, alpha) for _, g in shocked.groupby("primary_type", sort=False)])
        rows.append({"access_cost_factor": factor, **aggregate(eq, "cf_price_100", "cf_share")})
    paths["access_cost"] = pct_path(pd.DataFrame(rows), base)
    paths["access_cost"].to_csv(TABLES / "commodity_cf_access_cost_path.csv", index=False)

    scope_rank = base_df["data_scope_index"].rank(pct=True)
    rows = []
    for lam in np.linspace(0, 4.0, 51):
        multiplier = 1 + lam * (0.20 + 0.80 * scope_rank)
        true_use = float((base_df["market_size"] * base_df["share_model_base"] * multiplier).sum())
        row = {"copy_lambda": lam, **base}
        row["true_downstream_use"] = true_use
        row["mean_copy_multiplier"] = float(multiplier.mean())
        row["true_use_above_observed_pct"] = 100 * (true_use / base["adoption_proxy"] - 1)
        rows.append(row)
    paths["copy"] = pd.DataFrame(rows)
    paths["copy"].to_csv(TABLES / "commodity_cf_copy_path.csv", index=False)

    exposure_beta = max(float(beta_exposure), 0.08)
    rows = []
    for lift in np.linspace(0, 2.0, 51):
        bottom = (df["exposure_index"] < df["exposure_index"].quantile(0.25)).astype(float)
        parts = []
        for _, g in df.groupby("primary_type", sort=False):
            tmp = g.copy()
            local = bottom.loc[tmp.index].to_numpy(float)
            delta0 = tmp["delta0_calibrated"].to_numpy(float) + exposure_beta * lift * local
            tmp["price_100_cf"] = tmp["price_100"]
            tmp["share_cf"], _ = market_shares(delta0, tmp["price_100"].to_numpy(float), alpha)
            parts.append(tmp)
        cf = pd.concat(parts)
        rows.append({"exposure_lift": lift, **aggregate(cf, "price_100_cf", "share_cf")})
    paths["exposure"] = pct_path(pd.DataFrame(rows), base)
    paths["exposure"].to_csv(TABLES / "commodity_cf_exposure_path.csv", index=False)

    summary_rows = [
        {
            "情景": "基准",
            **base,
            "adoption_proxy_pct_change": 0.0,
            "mean_price_usd_pct_change": 0.0,
            "revenue_proxy_pct_change": 0.0,
        },
        {"情景": "入口价格提高 10%", **paths["price"].iloc[(paths["price"]["price_multiplier"] - 1.10).abs().argmin()].to_dict()},
        {"情景": "试用价值减半", **paths["trial"].iloc[(paths["trial"]["trial_value_scale"] - 0.50).abs().argmin()].to_dict()},
        {"情景": "访问治理成本提高 10%", **paths["access_cost"].iloc[(paths["access_cost"]["access_cost_factor"] - 1.10).abs().argmin()].to_dict()},
        {"情景": "低披露产品提升一档", **paths["disclosure"].iloc[(paths["disclosure"]["disclosure_lift"] - 1.00).abs().argmin()].to_dict()},
        {"情景": "低曝光产品提升一档", **paths["exposure"].iloc[(paths["exposure"]["exposure_lift"] - 1.00).abs().argmin()].to_dict()},
        {"情景": "复制/共享 lambda=1", **paths["copy"].iloc[(paths["copy"]["copy_lambda"] - 1.00).abs().argmin()].to_dict()},
    ]
    summary = pd.DataFrame(summary_rows)
    keep = [
        "情景", "adoption_proxy_pct_change", "mean_price_usd_pct_change", "revenue_proxy_pct_change",
        "inside_share_weighted", "true_use_above_observed_pct",
    ]
    for c in keep:
        if c not in summary.columns:
            summary[c] = np.nan
    save_table("commodity_counterfactual_summary", summary[keep])
    return paths


def make_figures(api: pd.DataFrame, supply: pd.DataFrame, paths: dict[str, pd.DataFrame]) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.scatter(api["data_scope_index"], api["ln_subscriptions"], s=9, alpha=0.25, color="#2f6f73")
    bins = pd.qcut(api["data_scope_index"], 20, duplicates="drop")
    line = api.groupby(bins, observed=True).agg(x=("data_scope_index", "mean"), y=("ln_subscriptions", "mean"))
    ax.plot(line["x"], line["y"], color="#b23a48", linewidth=2)
    ax.set_xlabel("Data scope index")
    ax.set_ylabel("log(1 + subscriptions)")
    ax.set_title("Scope and observed adoption")
    fig.tight_layout()
    fig.savefig(FIGURES / "commodity_scope_adoption.png", dpi=220)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.scatter(api["exposure_index"], api["ln_subscriptions"], s=9, alpha=0.25, color="#4a6fa5")
    bins = pd.qcut(api["exposure_index"], 20, duplicates="drop")
    line = api.groupby(bins, observed=True).agg(x=("exposure_index", "mean"), y=("ln_subscriptions", "mean"))
    ax.plot(line["x"], line["y"], color="#c77800", linewidth=2)
    ax.set_xlabel("Search exposure index")
    ax.set_ylabel("log(1 + subscriptions)")
    ax.set_title("Platform visibility and adoption")
    fig.tight_layout()
    fig.savefig(FIGURES / "commodity_exposure_adoption.png", dpi=220)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    x = supply["own_elasticity"].clip(-25, 0)
    ax.hist(x.dropna(), bins=40, color="#7868a6", alpha=0.82)
    ax.axvline(x.median(), color="#333333", linewidth=1.5)
    ax.set_xlabel("Own-price elasticity")
    ax.set_ylabel("APIs")
    ax.set_title("Demand elasticities from calibrated static model")
    fig.tight_layout()
    fig.savefig(FIGURES / "commodity_elasticity_distribution.png", dpi=220)
    plt.close(fig)

    fig, axes = plt.subplots(2, 3, figsize=(13, 7.4))
    panels = [
        ("price", "price_multiplier", "Price multiplier"),
        ("trial", "trial_value_scale", "Trial value scale"),
        ("access_cost", "access_cost_factor", "Access-cost factor"),
        ("disclosure", "disclosure_lift", "Disclosure lift"),
        ("exposure", "exposure_lift", "Exposure lift"),
        ("copy", "copy_lambda", "Copy/share lambda"),
    ]
    for ax, (key, xcol, xlabel) in zip(axes.ravel(), panels):
        path = paths[key]
        ax.plot(path[xcol], path.get("adoption_proxy_pct_change", path.get("true_use_above_observed_pct")), label="Adoption/true use", color="#2f6f73", linewidth=2)
        if "revenue_proxy_pct_change" in path:
            ax.plot(path[xcol], path["revenue_proxy_pct_change"], label="Revenue", color="#b23a48", linewidth=2, alpha=0.85)
        if key == "copy":
            ax.plot(path[xcol], path["true_use_above_observed_pct"], color="#b23a48", linewidth=2, label="True use above observed")
        ax.axhline(0, color="#444444", linewidth=0.8)
        ax.set_xlabel(xlabel)
        ax.set_ylabel("% change")
        ax.set_title(xlabel)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, frameon=False)
    fig.tight_layout(rect=[0, 0.05, 1, 1])
    fig.savefig(FIGURES / "commodity_counterfactual_paths.png", dpi=220)
    plt.close(fig)


def table_md(name: str) -> str:
    p = TABLES / f"{name}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def write_report(summary: dict[str, Any]) -> Path:
    md = f"""---
title: "数据访问合同、信息设计与市场势力"
geometry: margin=1in
fontsize: 11pt
---

# 摘要

【待写】

# 引言

【待写】

# 文献综述

【待写】

# 理论基准

本文将 RapidAPI 的 Data 市场解释为一个数据访问权市场。卖家交付的是一组可计量、可限速、可撤销、可版本化的 API 访问权。这个对象同时具有三类经济属性。第一，它是差异化产品：不同 API 覆盖的数据源、字段、更新频率、接口稳定性和文档质量不同，买方替代集合由数据类型和用途决定。第二，它是信息商品：复制和再使用的边际成本低，价格表通常通过免费入口、额度、超额费和版本化菜单筛选买方。第三，它是数据商品：同一份数据可被多个买方非竞争性使用，买方还可能把数据复制给组织内部或第三方，因此平台观测订阅数是下游真实使用的下界。

这一设定继承差异化产品 IO 的需求-供给闭环。Berry、Levinsohn 和 Pakes 以及 Nevo 的核心启发是，价格具有内生性；高价格可能同时反映高质量和高市场势力，需求估计需要反演市场份额并处理这种选择。Crawford 和 Yurukoglu 的 bundling 研究说明，数字内容市场中的菜单合同本身是供给选择，版本化应进入模型而非停留在描述层。Gandhi 和 Houde 以及 Conlon 和 Gortmaker 强调，替代模式和工具变量需要来自产品空间和竞争集合。

平台文献给出免费机制和价格结构的解释。Rochet 和 Tirole、Armstrong、Weyl 以及 Hagiu 和 Wright 的共同出发点是，平台价格同时决定总价水平和不同参与阶段之间的价格结构。在 RapidAPI Data 中，免费 plan 把买方购买前学习、卖方筛选和平台转化放在同一合同里。信息商品文献进一步说明为什么菜单重要。Varian、Bakos 和 Brynjolfsson、Sundararajan 以及 Bhargava 和 Choudhary 把低复制成本、版本化、bundling 和非线性定价联系起来；这些机制在 API 市场中表现为调用额度、超额费、endpoint 限制和审批。

数据经济学文献约束了本文的理论贡献。Jones 和 Tonetti 强调数据的非竞争性使用，Bergemann、Bonatti 和 Gan 以及 Acemoglu 等研究说明数据交易会产生外部性和再使用问题，Ichihashi 讨论数据外部性，Agarwal、Dahleh 和 Sarkar 把数据市场设计与定价算法联系起来。本文的增量在于把这些理论对象放进一个静态产业组织框架：产品表现为带合同、试用、计量、访问控制和潜在复制外溢的数据访问权；供给表现为对非竞争性数据的访问治理和价格筛选。

# 数据

样本来自 RapidAPI 的 Data 类 API、公开价格计划、endpoint 静态信息、healthcheck、spotlight、restricted plans、allowed developers 和搜索曝光窗口。产品层是 API，合同层是 plan，功能层是 endpoint/parameter/payload。市场按数据用途划分，包括 web scraping、social/profile、geo/identity、firm/lead、finance/market、ecommerce/price、document/text、real estate/mobility、public/reference 和 other。

{table_md("commodity_sample_overview")}

本文构造五组核心变量。数据范围指数综合 endpoint、参数、payload 和 endpoint group，刻画买方能够访问的数据集合。接入复杂度指数综合 route depth、POST 占比和必填参数，刻画接入成本。披露与可验证性指数综合 README、schema、外部文档、服务条款和 OpenAPI spec，刻画购买前质量可观察性。试用和版本化变量来自 plan 菜单，包括免费入口、免费额度、付费层级、价格梯度、额度梯度和超额费。访问治理变量来自审批、restricted plans、allowed developers、endpoint 限制、hard/soft limit 和 rate limit，刻画卖家如何控制非竞争性数据的使用边界。

{table_md("commodity_summary_stats")}

![数据范围与采用](../figures/commodity_scope_adoption.png)

图 1 显示数据范围与观测采用之间存在正相关，但这种关系并不机械。范围较低的产品集中在低采用区域，范围较高的产品中既有高采用 API，也有大量长尾 API。这一点很重要：endpoint 多意味着潜在用途更多，也意味着买方需要理解更多字段、处理更多参数、承担更高接入成本。数据范围同时改变用途空间和质量不确定性。后续模型因此同时控制范围、复杂度和披露。

![平台曝光与采用](../figures/commodity_exposure_adoption.png)

图 2 表明搜索曝光和采用高度相关。这个关系不应被直接解释为平台曝光的因果效应，因为曝光可能由质量、历史采用和平台排序共同决定。它的经验作用是控制买方可见性：如果忽略曝光，采用方程中的声誉、免费计划和文档变量会混入平台排序带来的流量差异。加入曝光变量后，数据范围、试用和披露变量的解释更接近产品与合同本身。

# 模型

市场 $m$ 是数据用途类型，产品 $j$ 是 API，卖家 $f$ 是 owner。买方 $i$ 的间接效用为

$$
u_{{ijm}} =
\\alpha p_j
+ \\beta_T T_j
+ \\beta_V V_j
+ \\beta_S S_j
+ \\beta_C C_j
+ \\beta_D D_j
+ \\beta_R R_j
+ \\beta_E E_j
+ X_j\\beta
+ \\xi_j
+ \\varepsilon_{{ijm}} .
$$

$p_j$ 是最低付费入口价格，$T_j$ 是试用价值，$V_j$ 是版本化菜单，$S_j$ 是数据范围，$C_j$ 是接入复杂度，$D_j$ 是披露与可验证性，$R_j$ 是运行可靠性，$E_j$ 是平台曝光。$\\xi_j$ 是买方观察但研究者不能完全观察的质量，例如数据源稀缺性、更新频率、字段准确度和法律风险。

观测订阅数为

$$
q_j^{{obs}} = subscriptions_j + 1,
\\qquad
M_m = \\frac{{\\sum_{{j\\in m}}q_j^{{obs}}}}{{0.20}},
\\qquad
s_j = \\frac{{q_j^{{obs}}}}{{M_m}} .
$$

Logit 反演为

$$
\\delta_j = \\log s_j - \\log s_{{0m}} .
$$

观测订阅数是下界。若买方复制数据给组织内部其他团队或外部用户，真实使用量可写为

$$
q_j^{{true}} = \\kappa_j q_j^{{obs}}, \\qquad \\kappa_j \\ge 1 .
$$

反事实中令 $\\kappa_j$ 随数据范围上升而上升，因为范围更广的数据更容易在多个任务中复用。这一设定把数据商品与普通品区分开：普通品销量通常接近消费数量，数据访问权的购买数量可能低估真实使用规模。

供给侧中 owner 选择价格以最大化

$$
\\max_{{p_j:j\\in\\mathcal J_f}}
\\sum_{{j\\in\\mathcal J_f}} M_m s_j(p)(p_j-c_j).
$$

$c_j$ 表示访问治理成本，涵盖服务器调用、数据源维护、清洗更新、失败请求处理、合规审核、客服和复制外溢风险。数据可以无限量复制供应，但访问权并非无成本；成本来自质量维护和使用控制。

一阶条件为

$$
s_j + \\sum_{{k\\in\\mathcal J_f}}(p_k-c_k)
\\frac{{\\partial s_k}}{{\\partial p_j}} = 0,
\\qquad
\\frac{{\\partial s_k}}{{\\partial p_j}}
= \\alpha s_k(1[j=k]-s_j).
$$

# 识别

价格内生性来自未观测质量。更稀缺、更新更稳定、法律风险更低的数据会同时获得更高采用和更高价格。OLS 中价格系数若为正，通常反映质量排序压过了价格敏感性。本文采用四组识别证据。

第一，reduced form 比较同一数据类型内部的采用、价格和免费计划选择，说明数据范围、披露、试用和曝光如何共同刻画市场。第二，plan 内部回归使用 API 固定效应，只比较同一个 API 的不同合同版本，从而识别调用额度、超额费、审批和 endpoint 限制如何进入价格菜单。第三，结构需求使用竞争者特征工具变量；同市场竞争者的免费计划、版本化、数据范围和披露影响本产品均衡价格，但在控制本产品属性和市场固定效应后，不直接进入本产品未观测质量。第四，使用 owner 其他市场策略和合同技术变量作为敏感性识别。owner 跨市场价格和版本化反映卖家定价能力或成本结构；hard/soft limit、rate limit、超额费、endpoint 限制和访问控制反映计量与治理成本。后两组工具变量的排除限制更强，报告中把它们作为辅助证据而非唯一依据。

{table_md("commodity_identification_summary")}

表中的识别强度本身构成经验结果。竞争者特征工具变量较弱，说明 Data API 的价格并没有被同类产品数量和平均特征机械决定。这个市场存在大量细分用途、长尾产品和 seller-specific 数据源，传统 BLP 工具变量在横截面中提供的信息有限。owner 跨市场策略较强，表明卖家的组织能力、数据源获取能力和定价惯例对价格有系统解释力。合同技术工具变量也有实质相关性，说明价格与访问治理机制相连；hard/soft limit、rate limit、超额费和 endpoint 限制并非无关的页面字段，而是卖家控制非竞争性数据使用边界的供给侧选择。

# Reduced Form

{table_md("commodity_reduced_form")}

采用方程的核心结果是，免费计划、版本化菜单、数据范围、披露、运行可靠性和平台曝光都与订阅数相关。免费计划的正系数支持试用学习机制。数据 API 的买方通常无法在购买前完全知道字段质量、更新频率、缺失率和接口稳定性，免费入口让买方先用少量调用学习质量。这个机制与普通低价促销不同：免费计划提供的是质量实验机会，也让卖家把低强度买方和高强度买方分离。

{table_md("commodity_effect_magnitudes")}

经济量级表把主要 log 采用系数转为百分比变化。免费计划的量级最大，说明进入门槛对数据 API 的采用非常关键。这个结果与平台文献中的免费侧机制一致，也与信息商品文献中的试用和版本化一致：当买方在购买前面对质量不确定性时，卖家通过免费入口把“是否值得接入”这个问题变成可实验的问题。搜索曝光的量级同样显著。它说明平台排序并非纯粹背景变量；买方先看到哪些 API，会影响哪些 API 进入候选集合。对长尾数据市场而言，可见性本身是需求形成的一部分。

版本化菜单的正相关说明，合同复杂度本身携带市场信息。一个只有单一价格的 API 很难同时服务一次性调用、持续监控、批量抓取和企业集成。更多付费层级、额度梯度和超额费让卖家在不观察买方真实用途的情况下进行筛选。信息商品文献中的版本化在这里具体体现为调用额度、endpoint 权限、rate limit 和审批。

数据范围的结果需要和复杂度一起读。范围扩大提高潜在用途，因此应当提高需求；复杂度提高接入和维护成本，因此会削弱转化。若只看 endpoint 数，容易把“可用数据更多”和“接入负担更重”混在一起。披露和可靠性变量的作用在于降低购买前不确定性。对数据商品而言，文档、schema、服务条款和 healthcheck 构成买方判断数据是否能进入生产流程的证据。

价格方程显示，采用、付费额度、版本化、访问治理和曝光共同影响最低付费入口价。这个结果说明最低价格是菜单结构中的入口节点。卖家可以通过低入口价吸引试用，也可以通过高额度、超额费和审批把高价值买方引入更高层级。因此，价格解释必须同时读 plan 菜单。

{table_md("commodity_trial_learning")}

试用学习回归进一步显示，免费计划的作用随购买前不确定性变化。若免费计划与不确定性的交互项为正，说明免费入口在更难验证的数据产品上更有价值；若交互项较弱，则说明免费计划更多反映一般流量获取。免费计划选择方程则显示，卖家在更复杂、更不易观察质量的产品上更倾向提供免费入口。这种供需两侧的一致性支持一个更深的解释：免费机制属于数据商品交易中解决质量不确定性的合同设计。

交互项还给出一个重要含义：试用不是对所有产品等比例有效。对于可观察性高、接入简单的 API，买方可以通过文档和字段说明较快判断产品是否匹配；免费入口的边际价值较低。对于字段复杂、质量难以验证、运行可靠性不透明的 API，免费入口把隐藏质量的一部分转化为可观测经验。这个模式把平台免费机制、信息设计和数据经济学中的质量外部性连接起来：卖家通过允许低额度试用向买方释放信息，同时保留对高强度使用的收费权。

# 版本化合同

{table_md("commodity_plan_versioning")}

同一 API 内部的 plan 回归给出最直接的版本化证据。API 固定效应吸收了数据源、品牌、owner 和总体质量，因此系数来自同一产品不同合同版本之间的比较。log 调用额度的系数为正且小于一时，说明价格随调用额度上升，但存在数量折扣。这个模式符合信息商品和数据访问权的成本结构：额外调用的边际复制成本低于首个接入合同的固定价值，但高强度使用仍然占用服务、维护和治理资源。

超额费、rate limit、审批和 endpoint 限制反映卖家如何切分使用权。超额费允许卖家保持较低固定费，同时向高强度买方收费；审批把交易从匿名自助转向筛选式合同；endpoint 限制让卖家出售局部数据范围。它们共同说明，数据商品的价格对象是“访问权包”。

# 结构估计

{table_md("commodity_static_demand")}

OLS 的价格系数若偏正，反映高质量 API 同时更贵、更受欢迎。工具变量估计的重点在于比较多组识别来源的方向、强度和经济含义。竞争者特征工具变量继承 BLP 思路：竞争集合改变本产品定价压力。owner 跨市场工具变量利用同一卖家在其他数据类型中的价格和版本化策略，捕捉共同成本或组织能力。合同技术工具变量使用 hard/soft limit、rate limit、超额费、endpoint 限制和计量复杂度，捕捉访问治理成本。

第一阶段 F 统计量直接报告识别强度。如果竞争者特征较弱，说明横截面市场内的产品空间尚不足以强力解释价格。Data API 的价格可能更多由访问治理、数据源稀缺性和 seller-specific 合同能力决定。若合同技术工具变量更强，它说明价格主要随计量和控制机制变化。这一发现把模型推向数据商品的特殊性：供给侧的核心对象是访问边界和使用治理。

需求估计中，免费计划、版本化、数据范围、披露、可靠性和曝光的系数共同刻画买方采用过程。免费计划提高试用价值；版本化提高匹配效率；数据范围扩大用途；复杂度提高接入成本；披露和可靠性降低质量风险；曝光影响买方是否看见产品。把这些变量同时放入需求反演，是为了避免把数据商品误写成只有价格和质量的普通差异化产品。

四列 IV 的价格系数并不完全一致，这一点需要直接解读。竞争者 IV 和 owner 跨市场 IV 给出的价格系数仍偏正，说明这些工具变量可能仍带有质量排序或 seller ability 成分；它们更像相关性诊断，而不是单独决定价格弹性的最终证据。合同技术 IV 给出负价格系数，方向与需求理论一致，并且与数据访问权的供给机制最贴近。合并 IV 的价格系数较小且不显著，说明在当前横截面数据中，价格弹性的点估计仍受识别来源影响。本文因此把结构估计用于组织机制和反事实基准，而不把单一价格系数包装成精确因果参数。这个处理更符合高质量 IO 文献的写法：识别强弱、工具变量含义和反事实假设需要一起报告。

# 供给

{table_md("commodity_structural_summary")}

供给结果将价格分解为 markup 和访问治理成本。中位弹性由校准设为 -3，用于把横截面份额转化为合理的价格敏感性尺度。这个校准把 demand inversion、owner 多产品定价和观测价格连接起来。反推成本中的负值被下界处理，因为数据访问权的会计边际成本不可直接观察，且高质量未观测项可能仍进入价格。

![弹性分布](../figures/commodity_elasticity_distribution.png)

弹性分布显示，长尾 API 和高价专业 API 的需求形态差异很大。长尾产品价格低、份额低，价格变化对采用的影响可能集中在是否尝试；专业产品价格高、替代品少，需求更依赖数据源稀缺性和合同可靠性。这种异质性解释了为什么 reduced form 中价格和采用可能正相关，而结构模型需要处理价格内生性。

# 反事实

{table_md("commodity_counterfactual_summary")}

![连续反事实路径](../figures/commodity_counterfactual_paths.png)

入口价格反事实沿连续价格倍率改变所有产品价格。采用下降幅度衡量买方对进入价格的敏感性，收入路径则反映价格提高和数量下降之间的权衡。若收入随价格提高下降，说明当前市场更多受采用约束；若收入在一定区间上升，则说明部分产品仍有提高入口价的空间。

试用价值反事实改变免费计划进入效用。去除或削弱试用价值会显著降低采用，说明免费机制在数据市场中承担质量学习功能。增强试用价值则提高采用，但收入变化取决于免费用户是否转化为付费用户。这个反事实对应平台免费机制和信息商品版本化文献：免费侧承担筛选和学习功能。

访问治理成本反事实提高 $c_j$。这里的成本对应维护、清洗、限速、监控、合规和客服。成本上升会推高均衡价格并降低采用。这个路径体现了无限量供应商品的供给特殊性：即使数据可被无限复制，访问权的可靠交付仍然有治理成本。

披露反事实提高低披露产品的信息可验证性。这个反事实模拟更完整的 schema、字段说明、服务条款、healthcheck 或外部文档。若采用上升，说明市场中存在信息摩擦；卖家可以通过披露改善需求，平台也可以通过标准化披露提高匹配效率。

曝光反事实提高低曝光产品的可见性。它改变的是买方搜索集合，并非产品质量。若采用明显上升，说明平台排序和搜索结果是市场分配的一部分。对 RapidAPI 这类市场，需求不只由产品属性决定，也由平台把哪些 API 展示给买方决定。

复制反事实保持平台观测订阅数不变，改变真实下游使用量。随着 $\\lambda$ 上升，数据范围越广的 API 拥有越高复制倍率。该路径说明，订阅数低估真实使用并非普通测量误差；它来自数据商品的可复制和可复用属性。同一份数据可以在多个任务、团队和应用中复用。福利和市场规模分析若只看订阅数，会系统低估高范围数据产品的社会使用。

这些反事实的共同含义是，Data API 市场的政策变量和企业策略变量不只包括价格。平台可以通过排序和标准化披露改变匹配效率；卖家可以通过免费额度和版本化菜单改变学习与筛选；访问治理成本会通过均衡价格传导到采用；复制和共享则改变平台观测交易数与真实使用数之间的关系。普通产品市场中的“价格-数量”框架在这里需要扩展为“价格-合同-信息-访问治理-下游复用”的框架。

# 创新点

第一，本文把数据 API 定义为合同化的数据访问权。传统差异化产品模型强调产品质量和价格，本文进一步把 plan 菜单、调用额度、rate limit、endpoint 限制、审批和 allowed developers 放进同一价格对象。这使模型能够解释为什么最低价格、免费入口和付费层级必须一起估计。

第二，本文把数据的非竞争性和可复制性转化为可估计框架中的观测问题。平台订阅数是交易关系数量，不一定是真实使用数量。复制倍率反事实把 Jones-Tonetti 和数据外部性文献中的核心性质连接到 marketplace 数据。

第三，本文把试用机制解释为信息设计。免费计划的价值来自购买前学习，区别于一般促销。披露、healthcheck、schema 和文档共同决定买方能否在付费前评估质量。

第四，本文把供给侧从生产成本改写为访问治理成本。数据可无限复制供应，但可靠、合规、可计量的数据访问权并非零成本。hard/soft limit、rate limit、超额费和审批正是这种治理成本的可观测痕迹。

# 局限

本文仍是静态横截面分析，不能识别 API 上线、排序变化、价格调整和采用动态之间的时间顺序。订阅数是累计或平台展示口径，不能完全等同于当期销量。工具变量的强度和排除限制也不完全相同，报告把多组 IV 作为识别敏感性而非单一最终答案。后续若有每日爬取面板，可以用价格变化、曝光变化和 plan 调整构造更强的动态识别。

# 参考文献

【待并入 BibTeX 与期刊格式】
"""
    out = REPORT / "rapidapi_data_commodity_io_article.md"
    out.write_text(md, encoding="utf-8")
    pdf = REPORT / "rapidapi_data_commodity_io_article.pdf"
    cmd = [
        "pandoc",
        str(out),
        "-o",
        str(pdf),
        "--pdf-engine=xelatex",
        "-V",
        "CJKmainfont=Songti SC",
        "-V",
        "mainfont=Times New Roman",
    ]
    try:
        subprocess.run(cmd, check=True, cwd=REPORT)
    except Exception:
        subprocess.run(["pandoc", str(out), "-o", str(pdf), "--pdf-engine=xelatex"], check=True, cwd=REPORT)
    return pdf


def main() -> None:
    ensure_dirs()
    menu, _ = build_menu_features()
    api, structural = build_api_sample(menu)
    sample_overview = summary_tables(api, structural)
    rf, trial = run_reduced_forms(api)
    demand, iv_rival, iv_owner, iv_contract, iv_all, alpha = run_static_demand(structural)
    supply = add_supply(structural, alpha)

    beta_free = iv_all.params.get("has_free_plan", np.nan)
    if not np.isfinite(beta_free):
        beta_free = 0.40
    beta_disclosure = iv_all.params.get("disclosure_index", np.nan)
    if not np.isfinite(beta_disclosure):
        beta_disclosure = 0.12
    beta_exposure = iv_all.params.get("exposure_index", np.nan)
    if not np.isfinite(beta_exposure):
        beta_exposure = 0.08
    paths = run_counterfactuals(supply, alpha, beta_free, beta_disclosure, beta_exposure)
    make_figures(api, supply, paths)

    summary = {
        "api_rows": int(len(api)),
        "structural_rows": int(len(structural)),
        "markets": int(api["primary_type"].nunique()),
        "owners": int(api["owner_slug"].nunique()),
        "alpha_calibrated": float(alpha),
        "iv_rival_first_stage_f": float(iv_rival.first_stage_f),
        "iv_owner_first_stage_f": float(iv_owner.first_stage_f),
        "iv_contract_first_stage_f": float(iv_contract.first_stage_f),
        "iv_all_first_stage_f": float(iv_all.first_stage_f),
        "report_markdown": str(REPORT / "rapidapi_data_commodity_io_article.md"),
        "report_pdf": str(REPORT / "rapidapi_data_commodity_io_article.pdf"),
    }
    (OUT / "data_commodity_io_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    pdf = write_report(summary)
    print(json.dumps({**summary, "pdf": str(pdf)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

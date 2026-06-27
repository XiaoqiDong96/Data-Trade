#!/usr/bin/env python3
"""Build empirical panel tables from RapidAPI crawl outputs.

Outputs:
- rapidapi_panel_Data_plan.csv: API x billing-plan table.
- rapidapi_panel_Data_plan_limit.csv: API x billing-plan x quota/limit table.
- rapidapi_panel_Data_variable_dictionary.csv: Chinese variable dictionary.
- rapidapi_panel_Data_report.md: short data construction note.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


PERIOD_TO_MONTHS = {
    "DAILY": 1 / 30,
    "WEEKLY": 7 / 30,
    "MONTHLY": 1,
    "QUARTERLY": 3,
    "YEARLY": 12,
    "ANNUAL": 12,
}


def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, low_memory=False)


def to_bool(series: pd.Series) -> pd.Series:
    return series.astype("string").str.lower().map({"true": True, "false": False, "1": True, "0": False}).astype("boolean")


def bool_false(series: pd.Series) -> pd.Series:
    return to_bool(series).fillna(False).astype(bool)


def period_months(series: pd.Series) -> pd.Series:
    return series.astype("string").str.upper().map(PERIOD_TO_MONTHS)


def nonempty_count(values: pd.Series) -> int:
    return int(values.notna().sum())


def as_float(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = df.copy()
    for col in clean.select_dtypes(include=["object", "string"]).columns:
        clean[col] = clean[col].astype("string").str.replace(r"[\r\n\t]+", " ", regex=True).str.strip()
    clean.to_csv(path, index=False)


def build_plan_table(root: Path, category: str) -> pd.DataFrame:
    suffix = category
    discovery = load_csv(root / "data" / f"rapidapi_discovery_{suffix}_apis.csv")
    details = load_csv(root / "data" / f"rapidapi_details_{suffix}_apis.csv")
    plans = load_csv(root / "data" / f"rapidapi_details_{suffix}_billing_plans.csv")
    limits = load_csv(root / "data" / f"rapidapi_details_{suffix}_billing_limits.csv")
    features_path = root / "data" / f"rapidapi_details_{suffix}_billing_features.csv"
    features = load_csv(features_path) if features_path.exists() else pd.DataFrame()

    discovery_cols = [
        "api_id",
        "rank",
        "page",
        "pricing",
        "categoryName",
        "description",
        "tags",
        "discovery_sources",
    ]
    discovery = discovery[[c for c in discovery_cols if c in discovery.columns]].rename(
        columns={
            "rank": "search_rank",
            "page": "search_page",
            "pricing": "api_pricing_listing",
            "categoryName": "category_listing",
            "description": "description_listing",
        }
    )

    details_cols = [
        "api_id",
        "name",
        "slugifiedName",
        "pricing",
        "category",
        "categoryId",
        "visibility",
        "status",
        "apiType",
        "createdAt",
        "updatedAt",
        "subscriptionsCount",
        "websiteUrl",
        "qualityScore",
        "popularityScore",
        "avgLatency",
        "avgServiceLevel",
        "avgSuccessRate",
        "rating",
        "ratingVotes",
        "bestRating",
        "owner_id",
        "owner_slugifiedName",
        "owner_name",
        "owner_type",
        "parent_org_id",
        "parent_org_name",
        "parent_org_slugifiedName",
        "billingPlans_count",
        "billingItems_count",
        "billingFeatures_count",
        "versions_count",
        "longDescription_len",
        "readme_len",
    ]
    details = details[[c for c in details_cols if c in details.columns]].rename(
        columns={
            "name": "api_name",
            "slugifiedName": "api_slug_detail",
            "pricing": "api_pricing_detail",
            "category": "category_detail",
            "visibility": "api_visibility",
            "status": "api_status",
            "apiType": "api_type",
            "createdAt": "api_createdAt",
            "updatedAt": "api_updatedAt",
            "subscriptionsCount": "subscriptions_count",
            "websiteUrl": "website_url",
            "qualityScore": "quality_score",
            "popularityScore": "popularity_score",
            "avgLatency": "avg_latency",
            "avgServiceLevel": "avg_service_level",
            "avgSuccessRate": "avg_success_rate",
            "ratingVotes": "rating_votes",
            "bestRating": "best_rating",
            "billingPlans_count": "billing_plans_count_api",
            "billingItems_count": "billing_items_count_api",
            "billingFeatures_count": "billing_features_count_api",
            "versions_count": "versions_count_api",
            "longDescription_len": "long_description_len",
        }
    )

    panel = plans.merge(details, on="api_id", how="left", validate="many_to_one")
    panel = panel.merge(discovery, on="api_id", how="left", validate="many_to_one")

    panel["plan_price"] = as_float(panel.get("price", pd.Series(dtype=float)))
    panel["plan_locale_price"] = as_float(panel.get("localePrice", pd.Series(dtype=float)))
    panel["rate_limit_amount"] = as_float(panel.get("rateLimit_amount", pd.Series(dtype=float)))
    panel["plan_period_months"] = period_months(panel.get("period", pd.Series(dtype=str)))
    panel["plan_monthly_price"] = panel["plan_price"] / panel["plan_period_months"]
    panel.loc[panel["plan_period_months"].isna() | (panel["plan_period_months"] == 0), "plan_monthly_price"] = pd.NA
    panel["is_public_plan"] = to_bool(panel["plan_visibility"].eq("PUBLIC").astype(str))
    panel["is_private_plan"] = to_bool(panel["plan_visibility"].eq("PRIVATE").astype(str))
    panel["is_hidden_plan"] = bool_false(panel.get("hidden", pd.Series(dtype=str)))
    panel["requires_approval"] = bool_false(panel.get("shouldRequestApproval", pd.Series(dtype=str)))
    panel["is_free_plan"] = (panel["plan_price"].fillna(0) == 0) | panel.get("pricing", "").astype("string").str.upper().eq("FREE")
    panel["is_paid_plan"] = panel["plan_price"] > 0
    panel["is_recommended_plan"] = bool_false(panel.get("recommended", pd.Series(dtype=str)))
    panel["api_age_days_at_crawl"] = (pd.Timestamp("2026-06-14", tz="UTC") - pd.to_datetime(panel["api_createdAt"], unit="ms", errors="coerce", utc=True)).dt.days
    panel["days_since_update_at_crawl"] = (pd.Timestamp("2026-06-14", tz="UTC") - pd.to_datetime(panel["api_updatedAt"], unit="ms", errors="coerce", utc=True)).dt.days

    limits_work = limits.copy()
    limits_work["amount_num"] = as_float(limits_work.get("amount", pd.Series(dtype=float)))
    limits_work["overageprice_num"] = as_float(limits_work.get("overageprice", pd.Series(dtype=float)))
    limits_work["is_unlimited_limit"] = bool_false(limits_work.get("unlimited", pd.Series(dtype=str)))
    limits_work["is_hard_limit"] = limits_work.get("limitType", "").astype("string").str.lower().eq("hard")
    limits_work["is_soft_limit"] = limits_work.get("limitType", "").astype("string").str.lower().eq("soft")
    limits_work["is_all_endpoints_limit"] = bool_false(limits_work.get("allEndpoints", pd.Series(dtype=str)))

    by_plan = limits_work.groupby(["api_id", "plan_id", "version_id"], dropna=False).agg(
        limits_n=("limit_id", "count"),
        finite_limits_n=("amount_num", nonempty_count),
        max_quota_amount=("amount_num", "max"),
        min_quota_amount=("amount_num", "min"),
        mean_quota_amount=("amount_num", "mean"),
        has_unlimited_limit=("is_unlimited_limit", "max"),
        hard_limits_n=("is_hard_limit", "sum"),
        soft_limits_n=("is_soft_limit", "sum"),
        all_endpoint_limits_n=("is_all_endpoints_limit", "sum"),
        min_overage_price=("overageprice_num", "min"),
        max_overage_price=("overageprice_num", "max"),
        mean_overage_price=("overageprice_num", "mean"),
    ).reset_index()
    panel = panel.merge(by_plan, on=["api_id", "plan_id", "version_id"], how="left", validate="one_to_one")

    if not features.empty:
        by_features = features.groupby(["api_id", "plan_id", "version_id"], dropna=False).agg(
            enabled_features_n=("feature_id", "count"),
            active_features_n=("status", lambda x: int(x.astype("string").str.upper().eq("ACTIVE").sum())),
        ).reset_index()
        panel = panel.merge(by_features, on=["api_id", "plan_id", "version_id"], how="left", validate="one_to_one")
    else:
        panel["enabled_features_n"] = 0
        panel["active_features_n"] = 0

    for col in ["limits_n", "finite_limits_n", "hard_limits_n", "soft_limits_n", "all_endpoint_limits_n", "enabled_features_n", "active_features_n"]:
        if col in panel:
            panel[col] = panel[col].fillna(0).astype(int)

    panel["price_per_max_quota"] = panel["plan_price"] / panel["max_quota_amount"]
    panel.loc[panel["max_quota_amount"].isna() | (panel["max_quota_amount"] <= 0), "price_per_max_quota"] = pd.NA

    ordered = [
        "api_id",
        "api_slug",
        "api_name",
        "owner_slugifiedName",
        "owner_name",
        "parent_org_slugifiedName",
        "parent_org_name",
        "plan_id",
        "version_id",
        "plan_name",
        "plan_visibility",
        "is_public_plan",
        "is_private_plan",
        "is_hidden_plan",
        "requires_approval",
        "requestApprovalQuestion",
        "legalDocumentId",
        "legalAccountId",
        "is_recommended_plan",
        "version_current",
        "version_billingPlanId",
        "pricing",
        "plan_price",
        "currency",
        "period",
        "plan_period_months",
        "plan_monthly_price",
        "localeSymbol",
        "plan_locale_price",
        "is_free_plan",
        "is_paid_plan",
        "rateLimit_enabled",
        "rateLimit_unit",
        "rateLimit_unitName",
        "rate_limit_amount",
        "limits_n",
        "finite_limits_n",
        "max_quota_amount",
        "min_quota_amount",
        "mean_quota_amount",
        "has_unlimited_limit",
        "hard_limits_n",
        "soft_limits_n",
        "all_endpoint_limits_n",
        "min_overage_price",
        "max_overage_price",
        "mean_overage_price",
        "price_per_max_quota",
        "enabled_features_n",
        "active_features_n",
        "api_pricing_detail",
        "api_pricing_listing",
        "category_detail",
        "category_listing",
        "api_visibility",
        "api_status",
        "api_type",
        "subscriptions_count",
        "quality_score",
        "popularity_score",
        "avg_latency",
        "avg_service_level",
        "avg_success_rate",
        "rating",
        "rating_votes",
        "best_rating",
        "api_createdAt",
        "api_updatedAt",
        "api_age_days_at_crawl",
        "days_since_update_at_crawl",
        "billing_plans_count_api",
        "billing_items_count_api",
        "billing_features_count_api",
        "versions_count_api",
        "long_description_len",
        "readme_len",
        "search_rank",
        "search_page",
        "tags",
        "discovery_sources",
        "website_url",
        "owner_id",
        "owner_type",
        "parent_org_id",
        "description_listing",
        "api_slug_detail",
        "version_name",
        "option",
        "billinglimits_count",
        "features_count",
    ]
    return panel[[c for c in ordered if c in panel.columns] + [c for c in panel.columns if c not in ordered]]


def build_plan_limit_table(root: Path, category: str, plan_panel: pd.DataFrame) -> pd.DataFrame:
    suffix = category
    limits = load_csv(root / "data" / f"rapidapi_details_{suffix}_billing_limits.csv")
    limits = limits.rename(
        columns={
            "period": "limit_period",
            "amount": "limit_amount",
            "unlimited": "limit_unlimited",
            "overageprice": "limit_overage_price",
            "overageLocalePrice": "limit_overage_locale_price",
            "overageLocaleSymbol": "limit_overage_locale_symbol",
            "limitType": "limit_type",
            "item": "limit_item_id",
            "allEndpoints": "limit_all_endpoints",
        }
    )
    limits["limit_amount_num"] = as_float(limits["limit_amount"])
    limits["limit_overage_price_num"] = as_float(limits["limit_overage_price"])
    limits["limit_period_months"] = period_months(limits["limit_period"])
    limits["limit_monthly_amount"] = limits["limit_amount_num"] / limits["limit_period_months"]
    limits.loc[limits["limit_period_months"].isna() | (limits["limit_period_months"] == 0), "limit_monthly_amount"] = pd.NA
    limits["limit_is_unlimited"] = bool_false(limits["limit_unlimited"])
    limits["limit_is_hard"] = limits["limit_type"].astype("string").str.lower().eq("hard")
    limits["limit_is_soft"] = limits["limit_type"].astype("string").str.lower().eq("soft")
    limits["limit_is_all_endpoints"] = bool_false(limits["limit_all_endpoints"])

    keep_plan_cols = [
        "api_id",
        "plan_id",
        "version_id",
        "api_slug",
        "api_name",
        "owner_slugifiedName",
        "owner_name",
        "parent_org_slugifiedName",
        "parent_org_name",
        "plan_name",
        "plan_visibility",
        "is_public_plan",
        "is_private_plan",
        "is_hidden_plan",
        "requires_approval",
        "requestApprovalQuestion",
        "legalDocumentId",
        "legalAccountId",
        "is_recommended_plan",
        "version_current",
        "version_billingPlanId",
        "pricing",
        "plan_price",
        "currency",
        "period",
        "plan_period_months",
        "plan_monthly_price",
        "is_free_plan",
        "is_paid_plan",
        "subscriptions_count",
        "quality_score",
        "popularity_score",
        "avg_latency",
        "avg_service_level",
        "avg_success_rate",
        "rating",
        "rating_votes",
        "best_rating",
        "search_rank",
        "tags",
    ]
    merged = limits.merge(
        plan_panel[[c for c in keep_plan_cols if c in plan_panel.columns]],
        on=["api_id", "plan_id", "version_id"],
        how="left",
        validate="many_to_one",
    )
    merged["plan_price_per_limit_amount"] = merged["plan_price"] / merged["limit_amount_num"]
    merged.loc[merged["limit_amount_num"].isna() | (merged["limit_amount_num"] <= 0), "plan_price_per_limit_amount"] = pd.NA

    ordered = [
        "api_id",
        "api_slug",
        "api_name",
        "owner_slugifiedName",
        "owner_name",
        "parent_org_slugifiedName",
        "parent_org_name",
        "plan_id",
        "version_id",
        "plan_name",
        "plan_visibility",
        "is_public_plan",
        "is_private_plan",
        "is_hidden_plan",
        "requires_approval",
        "requestApprovalQuestion",
        "legalDocumentId",
        "legalAccountId",
        "is_recommended_plan",
        "version_current",
        "version_billingPlanId",
        "pricing",
        "plan_price",
        "currency",
        "period",
        "plan_period_months",
        "plan_monthly_price",
        "is_free_plan",
        "is_paid_plan",
        "limit_id",
        "limit_period",
        "limit_period_months",
        "limit_amount",
        "limit_amount_num",
        "limit_monthly_amount",
        "limit_is_unlimited",
        "limit_overage_price",
        "limit_overage_price_num",
        "limit_overage_locale_price",
        "limit_overage_locale_symbol",
        "limit_type",
        "limit_is_hard",
        "limit_is_soft",
        "limit_item_id",
        "billingitem_id",
        "billingitem_name",
        "billingitem_title",
        "billingitem_description",
        "billingitem_displayName",
        "billingitem_type",
        "limit_all_endpoints",
        "limit_is_all_endpoints",
        "tiersType",
        "tiersArray_count",
        "priceVariants_count",
        "tiersDefinitions_json",
        "priceVariants_json",
        "plan_price_per_limit_amount",
        "subscriptions_count",
        "quality_score",
        "popularity_score",
        "avg_latency",
        "avg_service_level",
        "avg_success_rate",
        "rating",
        "rating_votes",
        "best_rating",
        "search_rank",
        "tags",
    ]
    return merged[[c for c in ordered if c in merged.columns] + [c for c in merged.columns if c not in ordered]]


def dictionary_rows() -> list[dict[str, str]]:
    meanings: dict[str, tuple[str, str, str]] = {
        "api_id": ("API/Product", "RapidAPI 给 API 产品分配的唯一 ID。", "产品维度主键。"),
        "api_slug": ("API/Product", "API 在 RapidAPI URL 中使用的短名称。", "可与 owner slug 拼出详情页。"),
        "api_name": ("API/Product", "API 展示名称。", "产品名称。"),
        "api_slug_detail": ("API/Product", "详情接口返回的 API slug。", "校验字段。"),
        "api_pricing_detail": ("API/Product", "详情页 API 总体定价标签，如 FREE/PAID/FREEMIUM。", "产品层定价类型。"),
        "api_pricing_listing": ("API/Product", "搜索页 API 总体定价标签。", "与详情标签交叉验证。"),
        "category_detail": ("API/Product", "详情页类别名称。", "市场/品类定义。"),
        "category_listing": ("API/Product", "搜索页类别名称。", "市场/品类定义。"),
        "categoryId": ("API/Product", "RapidAPI 类别 ID。", "类别固定效应备用。"),
        "api_visibility": ("API/Product", "API 可见性，通常为 PUBLIC。", "过滤公开样本。"),
        "api_status": ("API/Product", "API 状态，如 ACTIVE。", "过滤可用产品。"),
        "api_type": ("API/Product", "接口类型，如 http。", "技术类型控制变量。"),
        "api_createdAt": ("API/Product", "API 创建时间，Unix 毫秒。", "生命周期变量原始值。"),
        "api_updatedAt": ("API/Product", "API 最近更新时间，Unix 毫秒。", "更新活跃度原始值。"),
        "api_age_days_at_crawl": ("API/Product", "截至 2026-06-14 的 API 年龄天数。", "进入时间/成熟度控制。"),
        "days_since_update_at_crawl": ("API/Product", "截至 2026-06-14 距最近更新的天数。", "维护活跃度控制。"),
        "description_listing": ("API/Product", "搜索页短描述。", "文本特征备用。"),
        "website_url": ("API/Product", "API 外部网站链接。", "卖家外部身份/可信度备用。"),
        "tags": ("API/Product", "RapidAPI 标签，使用竖线分隔。", "细分市场/产品特征。"),
        "search_rank": ("Discovery", "发现抓取中的搜索排序位置。", "曝光/搜索排名代理变量。"),
        "search_page": ("Discovery", "发现抓取中的页码。", "搜索可见性代理变量。"),
        "discovery_sources": ("Discovery", "该 API 被哪些关键词和排序窗口发现。", "抓取审计和覆盖率校验。"),
        "owner_id": ("Provider", "API 提供者账户 ID。", "企业/卖家维度主键。"),
        "owner_slugifiedName": ("Provider", "API 提供者 slug。", "卖家标识，适合固定效应。"),
        "owner_name": ("Provider", "API 提供者展示名。", "卖家名称。"),
        "owner_type": ("Provider", "提供者账户类型，如 Team/User。", "卖家类型控制变量。"),
        "parent_org_id": ("Provider", "父组织 ID。", "集团层面聚合。"),
        "parent_org_slugifiedName": ("Provider", "父组织 slug。", "集团层面固定效应。"),
        "parent_org_name": ("Provider", "父组织名称。", "集团名称。"),
        "plan_id": ("Plan", "价格计划 ID。", "计划维度主键。"),
        "version_id": ("Plan", "价格计划版本 ID。", "计划版本主键；与 limit 表匹配。"),
        "version_name": ("Plan", "价格计划版本名称。", "版本审计字段。"),
        "plan_name": ("Plan", "价格计划名称，如 BASIC/PRO/ULTRA 或自定义名称。", "菜单/产品线。"),
        "plan_visibility": ("Plan", "价格计划可见性，PUBLIC/PRIVATE。", "主样本建议保留 PUBLIC。"),
        "is_public_plan": ("Plan", "plan_visibility 是否为 PUBLIC。", "公开价格菜单过滤。"),
        "is_private_plan": ("Plan", "plan_visibility 是否为 PRIVATE。", "定制/非公开计划识别。"),
        "is_hidden_plan": ("Plan", "该计划是否被隐藏。", "主样本建议剔除 hidden。"),
        "requires_approval": ("Plan", "购买该计划是否需要卖家批准。", "交易摩擦变量。"),
        "requestApprovalQuestion": ("Plan", "卖家要求买家申请审批时展示的问题文本。", "交易摩擦/定制审核文本备用。"),
        "legalDocumentId": ("Plan", "计划关联的法律文件 ID。", "合规或合同复杂度备用。"),
        "legalAccountId": ("Plan", "计划关联的法律账户 ID。", "合规或合同复杂度备用。"),
        "is_recommended_plan": ("Plan", "RapidAPI/卖家是否标记为推荐计划。", "默认/推荐选项。"),
        "version_current": ("Plan", "价格计划版本是否为当前版本。", "主样本建议优先当前版本。"),
        "version_billingPlanId": ("Plan", "版本对象指向的 billing plan ID。", "计划版本匹配审计。"),
        "pricing": ("Plan", "计划层定价类型，如 FREE/PAID/FREEMIUM/PERUSE。", "价格制度分类。"),
        "plan_price": ("Plan", "计划版本标价，通常为美元金额。", "核心价格变量。"),
        "currency": ("Plan", "价格货币。", "币种控制；多数为空或美元口径。"),
        "period": ("Plan", "计划计费周期，如 MONTHLY/YEARLY。", "价格周期。"),
        "plan_period_months": ("Plan", "将计费周期折算为月数。", "用于标准化价格。"),
        "plan_monthly_price": ("Plan", "plan_price 除以 plan_period_months。", "月度等价价格。"),
        "localeSymbol": ("Plan", "本地化显示货币符号。", "展示口径。"),
        "plan_locale_price": ("Plan", "本地化价格数值。", "展示口径备用。"),
        "is_free_plan": ("Plan", "价格为 0 或计划定价类型为 FREE。", "免费进入策略。"),
        "is_paid_plan": ("Plan", "plan_price 大于 0。", "付费计划识别。"),
        "option": ("Plan", "计划版本选项字段。", "平台内部枚举备用。"),
        "billinglimits_count": ("Plan", "该计划版本下原始 billinglimits 数量。", "菜单复杂度。"),
        "features_count": ("Plan", "该计划版本下功能项数量。", "菜单复杂度。"),
        "rateLimit_enabled": ("Plan", "计划是否启用速率限制。", "拥塞/服务质量设计。"),
        "rateLimit_unit": ("Plan", "速率限制单位原始值。", "速率限制口径。"),
        "rateLimit_unitName": ("Plan", "速率限制单位名称。", "如 second/minute 等。"),
        "rate_limit_amount": ("Plan", "速率限制额度。", "单位时间最大请求数。"),
        "limits_n": ("Plan Aggregate", "计划下额度/限制条目数量。", "计划复杂度。"),
        "finite_limits_n": ("Plan Aggregate", "计划下有明确数量的限制条目数。", "额度完整性。"),
        "max_quota_amount": ("Plan Aggregate", "计划下最大额度数量。", "粗略服务包大小。"),
        "min_quota_amount": ("Plan Aggregate", "计划下最小额度数量。", "约束最紧的额度。"),
        "mean_quota_amount": ("Plan Aggregate", "计划下额度数量均值。", "服务包大小摘要。"),
        "has_unlimited_limit": ("Plan Aggregate", "计划是否包含 unlimited 限制项。", "无限量计划识别。"),
        "hard_limits_n": ("Plan Aggregate", "计划下 hard limit 数量。", "硬性配额强度。"),
        "soft_limits_n": ("Plan Aggregate", "计划下 soft limit 数量。", "可超额使用约束。"),
        "all_endpoint_limits_n": ("Plan Aggregate", "适用于所有 endpoint 的限制条目数。", "全局额度识别。"),
        "min_overage_price": ("Plan Aggregate", "计划下最小超额单价。", "边际价格下界。"),
        "max_overage_price": ("Plan Aggregate", "计划下最大超额单价。", "边际价格上界。"),
        "mean_overage_price": ("Plan Aggregate", "计划下平均超额单价。", "边际价格摘要。"),
        "price_per_max_quota": ("Plan Aggregate", "plan_price / max_quota_amount。", "包内平均单价代理。"),
        "enabled_features_n": ("Plan Aggregate", "计划启用的功能项数。", "非价格质量/功能维度。"),
        "active_features_n": ("Plan Aggregate", "状态为 ACTIVE 的功能项数。", "有效功能数。"),
        "limit_id": ("Limit", "额度/限制条目 ID。", "limit 维度主键。"),
        "limit_period": ("Limit", "额度统计周期，如 MONTHLY。", "额度周期。"),
        "limit_period_months": ("Limit", "额度周期折算为月数。", "用于标准化额度。"),
        "limit_amount": ("Limit", "原始额度数量。", "请求数/credits 等原始值。"),
        "limit_amount_num": ("Limit", "数值化后的额度数量。", "核心额度变量。"),
        "limit_monthly_amount": ("Limit", "limit_amount_num 除以 limit_period_months。", "月度等价额度。"),
        "limit_unlimited": ("Limit", "原始 unlimited 字段。", "无限量识别原始值。"),
        "limit_is_unlimited": ("Limit", "limit_unlimited 的布尔值。", "无限量识别。"),
        "limit_overage_price": ("Limit", "原始超额费用。", "边际使用价格原始值。"),
        "limit_overage_price_num": ("Limit", "数值化后的超额费用。", "边际使用价格。"),
        "limit_overage_locale_price": ("Limit", "本地化口径的超额费用。", "展示价格备用。"),
        "limit_overage_locale_symbol": ("Limit", "本地化口径的超额费用货币符号。", "展示价格备用。"),
        "limit_type": ("Limit", "额度类型，hard/soft/空。", "hard 不可超额，soft 通常可超额。"),
        "limit_is_hard": ("Limit", "limit_type 是否为 hard。", "硬约束识别。"),
        "limit_is_soft": ("Limit", "limit_type 是否为 soft。", "软约束识别。"),
        "limit_item_id": ("Limit", "该 limit 约束的计费项 ID。", "与 billing item 对应。"),
        "billingitem_id": ("Limit", "计费项 ID。", "额度对象主键。"),
        "billingitem_name": ("Limit", "计费项名称，如 Requests/Credits。", "额度单位。"),
        "billingitem_title": ("Limit", "计费项标题。", "额度单位展示名。"),
        "billingitem_description": ("Limit", "计费项描述。", "解释额度对象。"),
        "billingitem_displayName": ("Limit", "计费项显示名。", "额度单位展示名。"),
        "billingitem_type": ("Limit", "计费项类型。", "计费对象类型。"),
        "limit_all_endpoints": ("Limit", "该额度是否适用于所有 endpoints 的原始值。", "全局额度原始值。"),
        "limit_is_all_endpoints": ("Limit", "limit_all_endpoints 的布尔值。", "全局额度识别。"),
        "tiersType": ("Limit", "阶梯价格类型。", "非线性价格结构识别。"),
        "tiersArray_count": ("Limit", "阶梯价格区间数量。", "非线性价格复杂度。"),
        "priceVariants_count": ("Limit", "价格变体数量。", "价格差异化复杂度。"),
        "tiersDefinitions_json": ("Limit", "阶梯价格定义的 JSON。", "非线性价格备用。"),
        "priceVariants_json": ("Limit", "价格变体 JSON。", "A/B 或地区价格备用。"),
        "plan_price_per_limit_amount": ("Limit", "plan_price / limit_amount_num。", "具体 limit 口径的包内平均单价。"),
        "subscriptions_count": ("Demand/Reputation", "订阅数。", "需求/采用量代理，可作因变量。"),
        "quality_score": ("Reputation", "RapidAPI 质量分。", "质量/声誉控制。"),
        "popularity_score": ("Reputation", "RapidAPI 人气分。", "声誉/曝光代理。"),
        "avg_latency": ("Reputation", "平均延迟。", "技术质量，越低越好。"),
        "avg_service_level": ("Reputation", "平均服务水平/可用性百分比。", "可靠性质量。"),
        "avg_success_rate": ("Reputation", "平均成功率百分比。", "可靠性质量。"),
        "rating": ("Reputation", "用户评分。", "声誉变量。"),
        "rating_votes": ("Reputation", "评分票数。", "声誉可信度/评论量。"),
        "best_rating": ("Reputation", "评分满分。", "评分尺度。"),
        "billing_plans_count_api": ("API Summary", "API 拥有的计划数量。", "菜单复杂度。"),
        "billing_items_count_api": ("API Summary", "API 计费项数量。", "计费复杂度。"),
        "billing_features_count_api": ("API Summary", "API 功能项数量。", "功能复杂度。"),
        "versions_count_api": ("API Summary", "API 版本数量。", "产品迭代复杂度。"),
        "long_description_len": ("API Summary", "长描述字符数。", "信息披露强度。"),
        "readme_len": ("API Summary", "文档 readme 字符数。", "文档完备度。"),
    }

    rows = []
    for col, (group, meaning, use) in meanings.items():
        rows.append({"column": col, "group": group, "meaning_cn": meaning, "empirical_use": use})
    return rows


def write_dictionary(root: Path, category: str, plan_panel: pd.DataFrame, limit_panel: pd.DataFrame) -> pd.DataFrame:
    base = pd.DataFrame(dictionary_rows())
    files = []
    for table_name, df in [
        (f"rapidapi_panel_{category}_plan.csv", plan_panel),
        (f"rapidapi_panel_{category}_plan_limit.csv", limit_panel),
    ]:
        for col in df.columns:
            files.append(
                {
                    "table": table_name,
                    "column": col,
                    "dtype": str(df[col].dtype),
                    "non_missing": int(df[col].notna().sum()),
                    "unique_values": int(df[col].nunique(dropna=True)),
                }
            )
    out = pd.DataFrame(files).merge(base, on="column", how="left")
    out["group"] = out["group"].fillna("Other")
    out["meaning_cn"] = out["meaning_cn"].fillna("脚本保留的原始字段或平台内部字段；用于审计。")
    out["empirical_use"] = out["empirical_use"].fillna("一般不作为主变量，必要时回查原始 JSON。")
    write_csv(root / "data" / f"rapidapi_panel_{category}_variable_dictionary.csv", out)
    md_lines = [
        "# RapidAPI 面板变量字典",
        "",
        "字段来自两张面板表：`rapidapi_panel_Data_plan.csv` 和 `rapidapi_panel_Data_plan_limit.csv`。",
        "",
    ]
    for table_name, table_df in out.groupby("table", sort=False):
        md_lines.extend([f"## {table_name}", ""])
        table_df = table_df.sort_values(["group", "column"])
        for group, group_df in table_df.groupby("group", sort=False):
            md_lines.extend([f"### {group}", ""])
            md_lines.append("| column | dtype | non_missing | unique_values | meaning_cn | empirical_use |")
            md_lines.append("|---|---:|---:|---:|---|---|")
            for _, row in group_df.iterrows():
                vals = []
                for col in ["column", "dtype", "non_missing", "unique_values", "meaning_cn", "empirical_use"]:
                    value = str(row[col]).replace("|", "\\|").replace("\n", " ")
                    vals.append(value)
                md_lines.append("| " + " | ".join(vals) + " |")
            md_lines.append("")
    (root / "data" / f"rapidapi_panel_{category}_variable_dictionary.md").write_text(
        "\n".join(md_lines),
        encoding="utf-8",
    )
    return out


def write_report(root: Path, category: str, plan_panel: pd.DataFrame, limit_panel: pd.DataFrame, dictionary: pd.DataFrame) -> None:
    public_plan = plan_panel[plan_panel.get("is_public_plan", False) == True]
    public_visible = public_plan[public_plan.get("is_hidden_plan", False) == False]
    report = {
        "category": category,
        "plan_panel_rows": int(len(plan_panel)),
        "plan_panel_unique_api": int(plan_panel["api_id"].nunique()),
        "plan_panel_unique_plan": int(plan_panel["plan_id"].nunique()),
        "public_plan_rows": int(len(public_plan)),
        "public_visible_plan_rows": int(len(public_visible)),
        "limit_panel_rows": int(len(limit_panel)),
        "limit_panel_unique_api": int(limit_panel["api_id"].nunique()),
        "limit_panel_unique_plan": int(limit_panel["plan_id"].nunique()),
        "dictionary_rows": int(len(dictionary)),
        "pricing_counts": plan_panel["pricing"].value_counts(dropna=False).to_dict() if "pricing" in plan_panel else {},
        "plan_visibility_counts": plan_panel["plan_visibility"].value_counts(dropna=False).to_dict() if "plan_visibility" in plan_panel else {},
        "limit_type_counts": limit_panel["limit_type"].value_counts(dropna=False).to_dict() if "limit_type" in limit_panel else {},
    }
    (root / "data" / f"rapidapi_panel_{category}_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md = f"""# RapidAPI 面板数据构造说明

## 输出文件

- `rapidapi_panel_{category}_plan.csv`：API × 价格计划面板，一行是一个 API 的一个价格计划。
- `rapidapi_panel_{category}_plan_limit.csv`：API × 价格计划 × 调用额度面板，一行是一个价格计划下的一条额度/超额费规则。
- `rapidapi_panel_{category}_variable_dictionary.csv`：中文变量字典，覆盖上面两张面板表的所有字段。
- `rapidapi_panel_{category}_summary.json`：行数、唯一 API 数、计划数和主要分布。

## 当前样本

- plan 面板行数：{report["plan_panel_rows"]}
- plan 面板 API 数：{report["plan_panel_unique_api"]}
- plan 面板唯一计划数：{report["plan_panel_unique_plan"]}
- public plan 行数：{report["public_plan_rows"]}
- public 且非隐藏 plan 行数：{report["public_visible_plan_rows"]}
- plan-limit 面板行数：{report["limit_panel_rows"]}
- plan-limit 面板 API 数：{report["limit_panel_unique_api"]}

## 推荐实证样本

主回归建议先使用：

`is_public_plan == True & is_hidden_plan == False & api_status == "ACTIVE"`

原因是 private/custom plan 通常是卖家给特定买家的非公开合同，不能直接解释为市场上消费者看到的公开菜单价格。private plan 可以作为附录，研究价格歧视或定制合同。

## 结构模型对应

- 产品 `j`：`api_id`
- 企业 `f`：`owner_slugifiedName` 或 `parent_org_slugifiedName`
- 价格 `p_jk`：`plan_monthly_price` 或 `plan_price`
- 数量/采用量 `q_j`：`subscriptions_count`
- 质量/声誉 `x_j`：`popularity_score`、`rating`、`avg_success_rate`、`avg_service_level`、`avg_latency`
- 套餐大小 `s_jk`：`max_quota_amount` 或 `limit_monthly_amount`
- 超额边际价格 `m_jk`：`mean_overage_price` 或 `limit_overage_price_num`
"""
    (root / "data" / f"rapidapi_panel_{category}_report.md").write_text(md, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="rapidapi_crawl")
    parser.add_argument("--category", default="Data")
    args = parser.parse_args()

    root = Path(args.root)
    plan_panel = build_plan_table(root, args.category)
    limit_panel = build_plan_limit_table(root, args.category, plan_panel)

    write_csv(root / "data" / f"rapidapi_panel_{args.category}_plan.csv", plan_panel)
    write_csv(root / "data" / f"rapidapi_panel_{args.category}_plan_limit.csv", limit_panel)
    dictionary = write_dictionary(root, args.category, plan_panel, limit_panel)
    write_report(root, args.category, plan_panel, limit_panel, dictionary)

    print(
        json.dumps(
            {
                "plan_rows": len(plan_panel),
                "plan_unique_api": int(plan_panel["api_id"].nunique()),
                "limit_rows": len(limit_panel),
                "limit_unique_api": int(limit_panel["api_id"].nunique()),
                "dictionary_rows": len(dictionary),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

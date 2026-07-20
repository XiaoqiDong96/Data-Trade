from __future__ import annotations

import argparse
import json
import math
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyblp
import pyhdfe
import statsmodels.api as sm
from linearmodels.iv import IV2SLS, IVLIML


ROOT = Path(__file__).resolve().parents[2]
CRAWL = ROOT / "rapidapi_crawl"
OUT = ROOT / "rapidapi_io_static" / "full_results"
TABLES = OUT / "tables"
FIGURES = OUT / "figures"
REPORT = OUT / "report"
DATA = OUT / "data"
SNAPSHOT_DATE = pd.Timestamp.now(tz="UTC").normalize()

LABELS = {
    "has_free_plan": "Free plan",
    "ln_price": "Log minimum paid price",
    "prices": "Minimum paid price / 100",
    "trial_learning": "Free plan x ex ante uncertainty",
    "trial_signal_precision": "Trial signal precision",
    "bayes_learning_index": "Calibrated Bayesian learning value",
    "ln_free_quota": "Log free quota",
    "ln_max_paid_quota": "Log maximum paid quota",
    "data_scope_index": "Data scope",
    "data_complexity_index": "Integration complexity",
    "disclosure_index": "Disclosure",
    "reliability_index": "Reliability",
    "ln_public_plan_count": "Log public plan count",
    "versioning_index": "Versioning",
    "open_best_score": "Open-data substitute score",
    "open_score_z": "Open-data substitute score",
    "schema_overlap_best": "Best schema overlap",
    "schema_overlap_z": "Best schema overlap",
    "ln_schema_near": "Log close schema substitutes",
    "ln_owner_size": "Log owner portfolio size",
    "ln_api_age": "Log API age",
    "menu_has_overage": "Overage contract present",
    "ln_q": "Log call quota",
    "overage": "Positive overage fee",
    "requires_approval": "Approval required",
    "is_recommended_plan": "Recommended plan",
    "endpoint_limit": "Endpoint-restricted plan",
    "rate_limit": "Rate limit present",
    "restricted_dev": "Named developers only",
    "has_restricted_plan": "Named-developer restriction",
    "ln_subscriptions": "Log platform subscriptions",
    "api_has_free_plan": "Free plan",
    "api_data_scope_index": "Data scope",
    "api_data_complexity_index": "Integration complexity",
    "api_disclosure_index": "Disclosure",
    "api_reliability_index": "Reliability",
    "ln_q_search": "Log platform subscriptions",
    "rel_exposure_z": "Relevance-sort exposure",
}


def ensure_dirs() -> None:
    for path in (TABLES, FIGURES, REPORT, DATA):
        path.mkdir(parents=True, exist_ok=True)


def numeric(series: pd.Series, fill: float = 0.0) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(fill)


def zscore(series: pd.Series) -> pd.Series:
    x = numeric(series)
    sd = float(x.std())
    return (x - float(x.mean())) / sd if sd > 0 else x * 0


def stars(pvalue: float) -> str:
    if not np.isfinite(pvalue):
        return ""
    if pvalue < 0.01:
        return "***"
    if pvalue < 0.05:
        return "**"
    if pvalue < 0.10:
        return "*"
    return ""


def fmt(beta: float, se: float, pvalue: float) -> str:
    if not np.isfinite(beta):
        return ""
    return f"{beta:.3f}{stars(pvalue)} ({se:.3f})"


def markdown_table(frame: pd.DataFrame) -> str:
    out = frame.copy()
    for col in out.columns:
        if pd.api.types.is_numeric_dtype(out[col]) and not pd.api.types.is_integer_dtype(out[col]):
            out[col] = out[col].map(lambda v: "" if pd.isna(v) else f"{v:.3f}")
    out = out.fillna("")
    lines = ["| " + " | ".join(out.columns.astype(str)) + " |"]
    lines.append("|" + "|".join(["---"] * len(out.columns)) + "|")
    for _, row in out.iterrows():
        lines.append("| " + " | ".join(str(row[c]) for c in out.columns) + " |")
    return "\n".join(lines)


def save_table(name: str, frame: pd.DataFrame) -> None:
    frame.to_csv(TABLES / f"{name}.csv", index=False)
    (TABLES / f"{name}.md").write_text(markdown_table(frame), encoding="utf-8")


def market_design(frame: pd.DataFrame, variables: list[str]) -> pd.DataFrame:
    parts = [frame[variables].astype(float)]
    parts.append(pd.get_dummies(frame["primary_type"], prefix="market", drop_first=True, dtype=float))
    return sm.add_constant(pd.concat(parts, axis=1), has_constant="add")


def result_value(result, variable: str) -> tuple[float, float, float]:
    if variable not in result.params.index:
        return np.nan, np.nan, np.nan
    return float(result.params[variable]), float(result.bse[variable]), float(result.pvalues[variable])


def load_api_data() -> pd.DataFrame:
    master = pd.read_csv(CRAWL / "data_merged" / "rapidapi_merged_api_master.csv", low_memory=False)
    external = pd.read_csv(CRAWL / "data_external" / "rapidapi_external_enriched_panel.csv", low_memory=False)
    external_columns = [
        "api_id",
        "github_repository_count",
        "github_code_match_count",
        "github_repo_star_sum",
        "open_best_score",
        "open_best_source",
        "schema_overlap_best",
        "schema_overlap_mean_top5",
        "schema_near_substitutes_020",
        "competitor_match_count",
        "domain",
        "owner_country_iso3",
        "digital_stri",
        "gdp_per_capita_usd",
    ]
    api = master.merge(external[external_columns], on="api_id", how="left", validate="one_to_one")
    for col in [
        "subscriptions_count",
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
        "ln_api_age",
        "published_apis_count",
        "open_best_score",
        "schema_overlap_best",
        "schema_near_substitutes_020",
        "github_repository_count",
        "menu_has_overage",
        "has_restricted_plan",
        "menu_has_hard_limit",
        "menu_has_soft_limit",
        "menu_has_rate_limit",
        "ln_max_overage_price",
        "mean_limits_n",
        "menu_endpoint_limited_share",
        "z_owner_other_market_price",
        "z_owner_other_market_versioning",
    ]:
        api[col] = numeric(api[col])

    created = pd.to_datetime(numeric(api["created_at"]), unit="ms", errors="coerce", utc=True)
    api["age_years"] = ((SNAPSHOT_DATE - created).dt.total_seconds() / 86400 / 365.25).clip(lower=0.25)
    api["q_flow"] = (api["subscriptions_count"] + 0.5) / api["age_years"]
    api["ln_flow"] = np.log(api["q_flow"])
    api["ln_price"] = np.log1p(api["min_paid_price"].clip(upper=api["min_paid_price"].quantile(0.99)))
    api["upgrade_price_usd"] = api["min_paid_price"].clip(upper=api["min_paid_price"].quantile(0.99))
    api["prices"] = api["upgrade_price_usd"] / 100
    api["entry_prices"] = api["prices"] * (1 - api["has_free_plan"])
    api["trial_learning"] = api["has_free_plan"] * api["uncertainty_index"]
    quota_precision = zscore(api["ln_free_quota"])
    disclosure_precision = zscore(api["disclosure_index"])
    reliability_precision = zscore(api["reliability_index"])
    api["trial_signal_precision"] = api["has_free_plan"] * zscore(
        0.50 * quota_precision + 0.25 * disclosure_precision + 0.25 * reliability_precision
    )
    prior_variance = np.exp(np.clip(zscore(api["uncertainty_index"]), -2.5, 2.5))
    signal_precision = np.exp(np.clip(
        0.50 * quota_precision + 0.25 * disclosure_precision + 0.25 * reliability_precision,
        -2.5,
        2.5,
    ))
    variance_reduction = prior_variance - 1 / (1 / prior_variance + signal_precision)
    api["bayes_learning_index"] = api["has_free_plan"] * zscore(variance_reduction)
    api["ln_schema_near"] = np.log1p(api["schema_near_substitutes_020"])
    api["ln_owner_size"] = np.log1p(api["published_apis_count"])
    api["ln_subscriptions"] = np.log1p(api["subscriptions_count"])
    api["open_020"] = (api["open_best_score"] >= 0.20).astype(int)
    api["any_github"] = (api["github_repository_count"] > 0).astype(int)
    api["owner_key"] = api["owner_id"].fillna(api["api_id"]).astype(str)
    api["market_ids"] = api["primary_type"].astype(str)
    api["firm_ids"] = api["owner_key"]
    api["product_ids"] = api["api_id"].astype(str)
    api["market_size"] = api.groupby("primary_type")["q_flow"].transform("sum") / 0.20
    api["shares"] = api["q_flow"] / api["market_size"]
    api["delta_logit"] = np.log(api["shares"]) - np.log(0.80)
    api["open_score_z"] = zscore(api["open_best_score"])
    api["schema_overlap_z"] = zscore(api["schema_overlap_best"])
    api["reuse_rank"] = (
        zscore(api["data_scope_index"])
        + zscore(api["schema_overlap_best"])
        + zscore(np.log1p(api["github_repository_count"]))
    ).rank(pct=True)
    api["clustering_ids"] = api["owner_key"]
    return api


def sample_audit(api: pd.DataFrame) -> pd.DataFrame:
    plans = pd.read_csv(CRAWL / "data_merged" / "rapidapi_merged_plan_contracts.csv", low_memory=False)
    endpoints = pd.read_csv(CRAWL / "data_merged" / "rapidapi_merged_endpoint_schema.csv", low_memory=False)
    search = pd.read_csv(CRAWL / "data_merged" / "rapidapi_merged_search_exposure.csv", low_memory=False)
    schema = pd.read_csv(CRAWL / "data_external" / "schema_overlap_pairs.csv", low_memory=False)
    rows = [
        ("API products", len(api), api["api_id"].nunique()),
        ("Owners", api["owner_key"].nunique(), api["owner_key"].nunique()),
        ("Use-case markets", api["primary_type"].nunique(), api["primary_type"].nunique()),
        ("Plan contracts", len(plans), plans["api_id"].nunique()),
        ("Endpoint schemas", len(endpoints), endpoints["api_id"].nunique()),
        ("Search result rows", len(search), search["api_id"].nunique()),
        ("Schema-overlap pairs", len(schema), pd.unique(schema[["api_id_left", "api_id_right"]].values.ravel()).size),
        ("APIs with a free plan", int(api["has_free_plan"].sum()), int(api.loc[api["has_free_plan"] == 1, "api_id"].nunique())),
        ("Paid-entry APIs", int(((api["has_free_plan"] == 0) & (api["prices"] > 0)).sum()), int(((api["has_free_plan"] == 0) & (api["prices"] > 0)).sum())),
        ("APIs matched to GitHub repositories", int(api["any_github"].sum()), int(api["any_github"].sum())),
        ("APIs with open-data score >= 0.20", int(api["open_020"].sum()), int(api["open_020"].sum())),
    ]
    out = pd.DataFrame(rows, columns=["Object", "Rows or count", "API coverage"])
    save_table("sample_audit", out)
    return out


def fundamental_analysis(api: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    variables = [
        "subscriptions_count",
        "q_flow",
        "min_paid_price",
        "free_quota",
        "max_paid_quota",
        "public_plan_count",
        "endpoint_count",
        "data_scope_index",
        "data_complexity_index",
        "disclosure_index",
        "reliability_index",
        "versioning_index",
        "restricted_access_index",
        "exposure_index",
    ]
    rows = []
    for variable in variables:
        values = numeric(api[variable]).replace([np.inf, -np.inf], np.nan).dropna()
        rows.append(
            {
                "Variable": LABELS.get(variable, variable),
                "N": len(values),
                "Mean": float(values.mean()),
                "SD": float(values.std()),
                "P10": float(values.quantile(0.10)),
                "P25": float(values.quantile(0.25)),
                "P50": float(values.quantile(0.50)),
                "P75": float(values.quantile(0.75)),
                "P90": float(values.quantile(0.90)),
            }
        )
    summary = pd.DataFrame(rows)
    save_table("fundamental_summary_statistics", summary)

    market_rows = []
    for market, group in api.groupby("primary_type", sort=False):
        adoption = group["q_obs"].to_numpy(float)
        shares = adoption / adoption.sum()
        owners = group.groupby("owner_key")["q_obs"].sum()
        owner_shares = owners / owners.sum()
        market_rows.append(
            {
                "Use-case market": market,
                "APIs": len(group),
                "Owners": group["owner_key"].nunique(),
                "Platform subscriptions": int(group["subscriptions_count"].sum()),
                "Free-plan share": float(group["has_free_plan"].mean()),
                "Positive-upgrade-price share": float((group["min_paid_price"] > 0).mean()),
                "Median upgrade price": float(group.loc[group["min_paid_price"] > 0, "min_paid_price"].median()),
                "Product adoption HHI": float(np.square(shares).sum()),
                "Owner adoption HHI": float(np.square(owner_shares).sum()),
                "Top-four product share": float(np.sort(shares)[-4:].sum()),
            }
        )
    markets = pd.DataFrame(market_rows).sort_values("APIs", ascending=False).reset_index(drop=True)
    save_table("fundamental_market_structure", markets)

    fig, axes = plt.subplots(1, 2, figsize=(10.8, 4.5))
    order = markets.sort_values("APIs")
    axes[0].barh(order["Use-case market"], order["APIs"], color="#15616d")
    axes[0].set(xlabel="Number of APIs", ylabel="")
    axes[1].scatter(markets["Product adoption HHI"], markets["Free-plan share"], color="#a23e48")
    for _, row in markets.iterrows():
        axes[1].annotate(str(row["Use-case market"]), (row["Product adoption HHI"], row["Free-plan share"]), fontsize=7, xytext=(3, 2), textcoords="offset points")
    axes[1].set(xlabel="Product adoption HHI", ylabel="Free-plan share")
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIGURES / "fundamental_market_structure.png", dpi=220)
    plt.close(fig)
    return summary, markets, {
        "api_count": len(api),
        "owner_count": int(api["owner_key"].nunique()),
        "market_count": int(api["primary_type"].nunique()),
        "free_share": float(api["has_free_plan"].mean()),
        "positive_price_share": float((api["min_paid_price"] > 0).mean()),
        "median_positive_price": float(api.loc[api["min_paid_price"] > 0, "min_paid_price"].median()),
    }


def contract_descriptives(api: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    plans = pd.read_csv(CRAWL / "data_merged" / "rapidapi_merged_plan_contracts.csv", low_memory=False)
    for column in [
        "is_public_plan",
        "is_hidden_plan",
        "is_paid_plan",
        "requires_approval",
        "rateLimit_enabled",
        "max_overage_price",
        "plan_mapped_endpoints_count",
        "plan_all_endpoint_items_count",
        "access_allowed_plan_developers_count",
    ]:
        plans[column] = numeric(plans[column])
    public = plans.loc[(plans["is_public_plan"] == 1) & (plans["is_hidden_plan"] == 0)].copy()
    spotlight = api["spotlights_count_y"] if "spotlights_count_y" in api else pd.Series(0, index=api.index)
    facts = [
        ("APIs with a free plan", float(api["has_free_plan"].mean()), len(api)),
        ("APIs with an overage contract", float(api["menu_has_overage"].mean()), len(api)),
        ("APIs with a rate limit", float(api["menu_has_rate_limit"].mean()), len(api)),
        ("APIs with endpoint-specific limits", float((api["menu_endpoint_limited_share"] > 0).mean()), len(api)),
        ("APIs with public healthcheck data", float(api["has_healthcheck_data"].mean()), len(api)),
        ("APIs with a spotlight", float((numeric(spotlight) > 0).mean()), len(api)),
        ("Public plans requiring approval", float((public["requires_approval"] > 0).mean()), len(public)),
        ("Public plans with rate limits", float((public["rateLimit_enabled"] > 0).mean()), len(public)),
        ("Public plans with positive overage fees", float((public["max_overage_price"] > 0).mean()), len(public)),
        (
            "Public plans restricted to named developers",
            float((public["access_allowed_plan_developers_count"] > 0).mean()),
            len(public),
        ),
    ]
    table = pd.DataFrame(facts, columns=["Contract fact", "Share", "Denominator"])
    table["Percent"] = 100 * table["Share"]
    save_table("contract_descriptive_facts", table[["Contract fact", "Percent", "Denominator"]])
    key = {row[0]: row[1] for row in facts}
    return table, key


def adoption_reduced_form(api: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    variables = [
        "has_free_plan",
        "ln_price",
        "trial_learning",
        "ln_free_quota",
        "ln_max_paid_quota",
        "data_scope_index",
        "data_complexity_index",
        "disclosure_index",
        "reliability_index",
        "versioning_index",
        "has_restricted_plan",
        "open_best_score",
        "ln_schema_near",
        "ln_owner_size",
    ]
    x = market_design(api, variables)
    groups = pd.factorize(api["owner_key"])[0]
    ols = sm.OLS(api["ln_flow"], x).fit(cov_type="cluster", cov_kwds={"groups": groups})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ppml = sm.GLM(
            api["subscriptions_count"],
            x,
            family=sm.families.Poisson(),
            offset=np.log(api["age_years"]),
        ).fit(cov_type="cluster", cov_kwds={"groups": groups})

    owner_n = api.groupby("owner_key")["api_id"].transform("count")
    within = api.loc[owner_n >= 2].copy().reset_index(drop=True)
    ids = within[["owner_key", "primary_type"]].astype(str).to_numpy()
    algorithm = pyhdfe.create(ids, drop_singletons=False)
    y_dm = algorithm.residualize(within[["ln_flow"]].to_numpy(float)).ravel()
    x_dm = algorithm.residualize(within[variables].to_numpy(float))
    keep = x_dm.std(axis=0) > 1e-10
    within_names = np.array(variables)[keep]
    owner_fe = sm.OLS(y_dm, x_dm[:, keep]).fit(
        cov_type="cluster", cov_kwds={"groups": pd.factorize(within["owner_key"])[0]}
    )

    rows = []
    for variable in variables:
        b1, s1, p1 = result_value(ols, variable)
        b2, s2, p2 = result_value(ppml, variable)
        if variable in within_names:
            k = int(np.where(within_names == variable)[0][0])
            b3, s3, p3 = float(owner_fe.params[k]), float(owner_fe.bse[k]), float(owner_fe.pvalues[k])
        else:
            b3 = s3 = p3 = np.nan
        rows.append(
            {
                "Variable": LABELS.get(variable, variable),
                "Log adoption flow": fmt(b1, s1, p1),
                "PPML with age exposure": fmt(b2, s2, p2),
                "Owner and market FE": fmt(b3, s3, p3),
            }
        )
    rows.extend(
        [
            {"Variable": "N", "Log adoption flow": int(ols.nobs), "PPML with age exposure": int(ppml.nobs), "Owner and market FE": int(owner_fe.nobs)},
            {"Variable": "Owner FE", "Log adoption flow": "No", "PPML with age exposure": "No", "Owner and market FE": "Yes"},
            {"Variable": "Market FE", "Log adoption flow": "Yes", "PPML with age exposure": "Yes", "Owner and market FE": "Yes"},
        ]
    )
    table = pd.DataFrame(rows)
    save_table("reduced_form_adoption", table)
    key = {
        "ols_free": float(ols.params["has_free_plan"]),
        "ols_free_se": float(ols.bse["has_free_plan"]),
        "ppml_free": float(ppml.params["has_free_plan"]),
        "ppml_free_se": float(ppml.bse["has_free_plan"]),
        "owner_fe_free": float(owner_fe.params[np.where(within_names == "has_free_plan")[0][0]]),
        "owner_fe_free_se": float(owner_fe.bse[np.where(within_names == "has_free_plan")[0][0]]),
        "ppml_scope": float(ppml.params["data_scope_index"]),
        "ppml_reliability": float(ppml.params["reliability_index"]),
        "owner_fe_scope": float(owner_fe.params[np.where(within_names == "data_scope_index")[0][0]]),
        "owner_fe_reliability": float(owner_fe.params[np.where(within_names == "reliability_index")[0][0]]),
        "owner_fe_n": int(owner_fe.nobs),
        "owner_fe_owners": int(within["owner_key"].nunique()),
    }
    return table, key


def trial_learning_reduced_form(api: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    common = [
        "ln_price",
        "data_scope_index",
        "data_complexity_index",
        "disclosure_index",
        "reliability_index",
        "versioning_index",
        "has_restricted_plan",
        "open_best_score",
        "ln_schema_near",
        "ln_owner_size",
    ]
    specifications = [
        ("Free x uncertainty", "trial_learning", ["has_free_plan", "trial_learning", "ln_free_quota"]),
        (
            "Signal precision",
            "trial_signal_precision",
            ["has_free_plan", "uncertainty_index", "trial_signal_precision"],
        ),
        ("Bayesian variance reduction", "bayes_learning_index", ["has_free_plan", "bayes_learning_index"]),
    ]
    owner_groups = pd.factorize(api["owner_key"])[0]
    owner_n = api.groupby("owner_key")["api_id"].transform("count")
    within = api.loc[owner_n >= 2].copy().reset_index(drop=True)
    ids = within[["owner_key", "primary_type"]].astype(str).to_numpy()
    algorithm = pyhdfe.create(ids, drop_singletons=False)
    y_dm = algorithm.residualize(within[["ln_flow"]].to_numpy(float)).ravel()

    rows = []
    key: dict[str, float] = {}
    for label, focal, mechanism in specifications:
        variables = list(dict.fromkeys(mechanism + common))
        x = market_design(api, variables)
        ols = sm.OLS(api["ln_flow"], x).fit(cov_type="cluster", cov_kwds={"groups": owner_groups})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ppml = sm.GLM(
                api["subscriptions_count"],
                x,
                family=sm.families.Poisson(),
                offset=np.log(api["age_years"]),
            ).fit(cov_type="cluster", cov_kwds={"groups": owner_groups})

        x_dm = algorithm.residualize(within[variables].to_numpy(float))
        keep = x_dm.std(axis=0) > 1e-10
        names = np.array(variables)[keep]
        owner_fe = sm.OLS(y_dm, x_dm[:, keep]).fit(
            cov_type="cluster", cov_kwds={"groups": pd.factorize(within["owner_key"])[0]}
        )
        k = int(np.where(names == focal)[0][0])
        rows.append(
            {
                "Learning proxy": label,
                "Log adoption flow": fmt(
                    float(ols.params[focal]), float(ols.bse[focal]), float(ols.pvalues[focal])
                ),
                "PPML with age exposure": fmt(
                    float(ppml.params[focal]), float(ppml.bse[focal]), float(ppml.pvalues[focal])
                ),
                "Owner and market FE": fmt(
                    float(owner_fe.params[k]), float(owner_fe.bse[k]), float(owner_fe.pvalues[k])
                ),
            }
        )
        key[f"{focal}_ols"] = float(ols.params[focal])
        key[f"{focal}_ols_se"] = float(ols.bse[focal])
        key[f"{focal}_ppml"] = float(ppml.params[focal])
        key[f"{focal}_ppml_se"] = float(ppml.bse[focal])
        key[f"{focal}_fe"] = float(owner_fe.params[k])
        key[f"{focal}_fe_se"] = float(owner_fe.bse[k])

    table = pd.DataFrame(rows)
    save_table("trial_learning_identification", table)
    return table, key


def reduced_form_stability(api: pd.DataFrame) -> pd.DataFrame:
    variables = [
        "has_free_plan",
        "ln_price",
        "trial_learning",
        "data_scope_index",
        "disclosure_index",
        "reliability_index",
        "versioning_index",
        "open_best_score",
        "ln_schema_near",
        "ln_owner_size",
    ]
    rows = []
    markets = ["All markets", *sorted(api["primary_type"].unique())]
    for market in markets:
        sample = api if market == "All markets" else api.loc[api["primary_type"] != market]
        x = market_design(sample, variables)
        model = sm.OLS(sample["ln_flow"], x).fit(
            cov_type="cluster", cov_kwds={"groups": pd.factorize(sample["owner_key"])[0]}
        )
        for variable in ["has_free_plan", "data_scope_index", "reliability_index"]:
            rows.append(
                {
                    "Market omitted": market,
                    "Variable": LABELS[variable],
                    "Estimate": float(model.params[variable]),
                    "SE": float(model.bse[variable]),
                }
            )
    frame = pd.DataFrame(rows)
    save_table("reduced_form_leave_one_market_out", frame)

    fig, axes = plt.subplots(1, 3, figsize=(11.2, 4.2), sharey=True)
    for ax, variable in zip(axes, ["Free plan", "Data scope", "Reliability"]):
        sub = frame.loc[frame["Variable"] == variable].reset_index(drop=True)
        y = np.arange(len(sub))
        ax.errorbar(sub["Estimate"], y, xerr=1.96 * sub["SE"], fmt="o", color="#15616d", capsize=2)
        ax.axvline(0, color="#777777", linewidth=0.8)
        ax.set_title(variable)
        ax.set_xlabel("Coefficient")
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_yticks(y)
        ax.set_yticklabels(sub["Market omitted"] if ax is axes[0] else [])
    fig.tight_layout()
    fig.savefig(FIGURES / "reduced_form_leave_one_market_out.png", dpi=220)
    plt.close(fig)
    return frame


def adoption_specification_curve(api: pd.DataFrame) -> pd.DataFrame:
    focal_variables = ["has_free_plan", "data_scope_index", "reliability_index"]
    lean_controls = ["has_free_plan", "ln_price", "data_scope_index", "reliability_index"]
    full_controls = [
        "has_free_plan",
        "ln_price",
        "trial_learning",
        "ln_free_quota",
        "ln_max_paid_quota",
        "data_scope_index",
        "data_complexity_index",
        "disclosure_index",
        "reliability_index",
        "versioning_index",
        "has_restricted_plan",
        "open_best_score",
        "ln_schema_near",
        "ln_owner_size",
    ]
    top_cutoff = api["subscriptions_count"].quantile(0.99)
    samples = {
        "All APIs": pd.Series(True, index=api.index),
        "Observed upgrade price": api["prices"] > 0,
        "Exclude top 1% adoption": api["subscriptions_count"] <= top_cutoff,
    }
    outcomes = {"Adoption flow": "ln_flow", "Subscription stock": "ln_subscriptions"}
    rows = []
    specification = 0
    for outcome_label, outcome in outcomes.items():
        for sample_label, keep in samples.items():
            for controls_label, controls in [("Lean", lean_controls), ("Full", full_controls)]:
                specification += 1
                work = api.loc[keep].copy()
                x = market_design(work, controls)
                model = sm.OLS(work[outcome], x).fit(
                    cov_type="cluster", cov_kwds={"groups": pd.factorize(work["owner_key"])[0]}
                )
                for variable in focal_variables:
                    rows.append(
                        {
                            "Specification": specification,
                            "Outcome": outcome_label,
                            "Sample": sample_label,
                            "Controls": controls_label,
                            "Variable": LABELS[variable],
                            "Estimate": float(model.params[variable]),
                            "SE": float(model.bse[variable]),
                            "N": int(model.nobs),
                        }
                    )
    curve = pd.DataFrame(rows)
    save_table("adoption_specification_curve", curve)
    fig, axes = plt.subplots(1, 3, figsize=(11.2, 4.2), sharex=True)
    for ax, variable in zip(axes, [LABELS[name] for name in focal_variables]):
        subset = curve[curve["Variable"] == variable].sort_values("Specification")
        ax.errorbar(
            subset["Estimate"],
            subset["Specification"],
            xerr=1.96 * subset["SE"],
            fmt="o",
            color="#15616d",
            markersize=3,
            capsize=2,
        )
        ax.axvline(0, color="#777777", linewidth=0.8)
        ax.set(title=variable, xlabel="Coefficient")
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("Specification")
    fig.tight_layout()
    fig.savefig(FIGURES / "adoption_specification_curve.png", dpi=220)
    plt.close(fig)
    return curve


def plan_versioning() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    plans = pd.read_csv(CRAWL / "data_merged" / "rapidapi_merged_plan_contracts.csv", low_memory=False)
    columns = [
        "is_public_plan",
        "is_hidden_plan",
        "is_paid_plan",
        "plan_monthly_price",
        "max_quota_amount",
        "max_overage_price",
        "requires_approval",
        "is_recommended_plan",
        "plan_mapped_endpoints_count",
        "plan_all_endpoint_items_count",
        "rateLimit_enabled",
        "rate_limit_amount",
        "access_allowed_plan_developers_count",
    ]
    for col in columns:
        plans[col] = numeric(plans[col]).astype(float)
    paid = plans.loc[
        (plans["is_public_plan"] == 1)
        & (plans["is_hidden_plan"] == 0)
        & (plans["is_paid_plan"] == 1)
        & (plans["plan_monthly_price"] > 0)
        & (plans["max_quota_amount"] > 0)
    ].copy()
    paid = paid.loc[paid["plan_monthly_price"] <= paid["plan_monthly_price"].quantile(0.99)].reset_index(drop=True)
    paid["ln_p"] = np.log(paid["plan_monthly_price"])
    paid["ln_q"] = np.log(paid["max_quota_amount"])
    paid["overage"] = (paid["max_overage_price"] > 0).astype(float)
    paid["endpoint_limit"] = (
        (paid["plan_mapped_endpoints_count"] > 0) | (paid["plan_all_endpoint_items_count"] > 0)
    ).astype(float)
    paid["rate_limit"] = ((paid["rateLimit_enabled"] > 0) | (paid["rate_limit_amount"] > 0)).astype(float)
    paid["restricted_dev"] = (paid["access_allowed_plan_developers_count"] > 0).astype(float)
    variables = [
        "ln_q",
        "overage",
        "requires_approval",
        "is_recommended_plan",
        "endpoint_limit",
        "rate_limit",
        "restricted_dev",
    ]
    algorithm = pyhdfe.create(paid[["api_id"]].astype(str).to_numpy(), drop_singletons=False)
    y_dm = algorithm.residualize(paid[["ln_p"]].to_numpy(float)).ravel()
    x_dm = algorithm.residualize(paid[variables].to_numpy(float))
    keep = x_dm.std(axis=0) > 1e-10
    names = np.array(variables)[keep]
    model = sm.OLS(y_dm, x_dm[:, keep]).fit(
        cov_type="cluster", cov_kwds={"groups": pd.factorize(paid["api_id"])[0]}
    )
    rows = []
    for variable in variables:
        if variable in names:
            k = int(np.where(names == variable)[0][0])
            value = fmt(float(model.params[k]), float(model.bse[k]), float(model.pvalues[k]))
        else:
            value = "Absorbed/no within-API variation"
        rows.append({"Variable": LABELS.get(variable, variable), "Within-API log monthly price": value})
    rows.extend(
        [
            {"Variable": "N", "Within-API log monthly price": int(model.nobs)},
            {"Variable": "API fixed effects", "Within-API log monthly price": "Yes"},
            {"Variable": "SE clustered by API", "Within-API log monthly price": "Yes"},
        ]
    )
    table = pd.DataFrame(rows)
    save_table("plan_versioning_fe", table)

    adjacent = []
    for _, group in paid.sort_values(["api_id", "max_quota_amount", "plan_monthly_price"]).groupby("api_id"):
        group = group.drop_duplicates(["max_quota_amount", "plan_monthly_price"])
        if len(group) > 1:
            adjacent.extend(zip(np.diff(group["max_quota_amount"]), np.diff(group["plan_monthly_price"])))
    pairs = np.asarray(adjacent, dtype=float)
    monotonic = pd.DataFrame(
        [
            {"Statistic": "Adjacent plan pairs", "Value": len(pairs)},
            {"Statistic": "Higher quota with weakly higher price", "Value": float(np.mean((pairs[:, 0] > 0) & (pairs[:, 1] >= 0)))},
            {"Statistic": "Higher quota with lower price", "Value": float(np.mean((pairs[:, 0] > 0) & (pairs[:, 1] < 0)))},
        ]
    )
    save_table("plan_menu_monotonicity", monotonic)
    quota_k = int(np.where(names == "ln_q")[0][0])
    key = {
        "n": int(model.nobs),
        "apis": int(paid["api_id"].nunique()),
        "quota_beta": float(model.params[quota_k]),
        "quota_se": float(model.bse[quota_k]),
        "monotone": float(np.mean((pairs[:, 0] > 0) & (pairs[:, 1] >= 0))),
        "violations": float(np.mean((pairs[:, 0] > 0) & (pairs[:, 1] < 0))),
    }
    return table, monotonic, key


def nonrival_supply_calibration() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    plans = pd.read_csv(CRAWL / "data_merged" / "rapidapi_merged_plan_contracts.csv", low_memory=False)
    for column in ["is_public_plan", "is_hidden_plan", "is_paid_plan", "plan_monthly_price", "max_quota_amount"]:
        plans[column] = numeric(plans[column])
    paid = plans.loc[
        (plans["is_public_plan"] == 1)
        & (plans["is_hidden_plan"] == 0)
        & (plans["is_paid_plan"] == 1)
        & (plans["plan_monthly_price"] > 0)
        & (plans["max_quota_amount"] > 0)
    ].copy()
    paid = paid.loc[
        (paid["plan_monthly_price"] <= paid["plan_monthly_price"].quantile(0.99))
        & (paid["max_quota_amount"] <= paid["max_quota_amount"].quantile(0.99))
    ]

    cloud = pd.read_csv(CRAWL / "data_external" / "cloud_api_costs.csv", low_memory=False)
    cloud["price_usd"] = numeric(cloud["price_usd"])
    cloud["begin_range"] = numeric(cloud["begin_range"])
    request_prices = cloud.loc[
        cloud["service"].astype(str).eq("AmazonApiGateway")
        & cloud["unit"].astype(str).str.lower().eq("requests")
        & cloud["price_usd"].gt(0)
        & cloud["begin_range"].eq(0),
        "price_usd",
    ]
    cost_per_million = float(request_prices.median() * 1_000_000)
    paid["gateway_cost_usd"] = paid["max_quota_amount"] * cost_per_million / 1_000_000
    paid["gateway_cost_share"] = paid["gateway_cost_usd"] / paid["plan_monthly_price"]
    facts = pd.DataFrame(
        [
            {"Calibration statistic": "First-tier AWS API Gateway price per million requests", "Value": cost_per_million},
            {"Calibration statistic": "Paid public plans with finite positive quota", "Value": len(paid)},
            {"Calibration statistic": "Median gateway cost share of monthly price", "Value": float(paid["gateway_cost_share"].median())},
            {"Calibration statistic": "P90 gateway cost share of monthly price", "Value": float(paid["gateway_cost_share"].quantile(0.90))},
            {"Calibration statistic": "Plans with price above calibrated gateway cost", "Value": float((paid["gateway_cost_share"] <= 1).mean())},
        ]
    )
    save_table("nonrival_supply_calibration", facts)

    path_rows = []
    for assumed_cost in np.linspace(0, 20, 81):
        cost_share = paid["max_quota_amount"] * assumed_cost / 1_000_000 / paid["plan_monthly_price"]
        path_rows.append(
            {
                "Gateway service cost per million calls": assumed_cost,
                "Median variable service cost share": float(cost_share.median()),
                "P90 variable service cost share": float(cost_share.quantile(0.90)),
                "Share of plans with nonnegative gross margin": float((cost_share <= 1).mean()),
            }
        )
    path = pd.DataFrame(path_rows)
    save_table("nonrival_supply_cost_path", path)
    fig, ax = plt.subplots(figsize=(7.2, 4.3))
    ax.plot(path.iloc[:, 0], 100 * path["Median variable service cost share"], color="#15616d", label="Median")
    ax.plot(path.iloc[:, 0], 100 * path["P90 variable service cost share"], color="#a23e48", label="P90")
    ax.axvline(cost_per_million, color="#777777", linestyle="--", linewidth=1)
    ax.set(xlabel="Gateway service cost per million calls (USD)", ylabel="Service cost as percent of monthly plan price")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIGURES / "nonrival_supply_cost_path.png", dpi=220)
    plt.close(fig)
    return facts, path, {
        "gateway_cost_per_million": cost_per_million,
        "plan_n": len(paid),
        "median_cost_share": float(paid["gateway_cost_share"].median()),
        "p90_cost_share": float(paid["gateway_cost_share"].quantile(0.90)),
        "positive_margin_share": float((paid["gateway_cost_share"] <= 1).mean()),
    }


def search_analysis(api: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    search = pd.read_csv(CRAWL / "data_merged" / "rapidapi_merged_search_exposure.csv", low_memory=False)
    search["cell"] = search["search_term"].fillna("").astype(str) + "|" + search["search_sort"].fillna("").astype(str)
    search["visibility"] = -np.log(numeric(search["search_rank"]).clip(lower=1))
    raw = [
        "api_has_free_plan",
        "api_min_paid_price",
        "api_data_scope_index",
        "api_data_complexity_index",
        "api_disclosure_index",
        "api_reliability_index",
        "api_q_obs",
    ]
    for col in raw:
        search[col] = numeric(search[col])
    search["ln_price"] = np.log1p(search["api_min_paid_price"].clip(upper=search["api_min_paid_price"].quantile(0.99)))
    search["ln_q_search"] = np.log1p(search["api_q_obs"])
    variables = [
        "api_has_free_plan",
        "ln_price",
        "api_data_scope_index",
        "api_data_complexity_index",
        "api_disclosure_index",
        "api_reliability_index",
        "ln_q_search",
    ]
    grouped = search.groupby("cell")
    y = search["visibility"] - grouped["visibility"].transform("mean")
    x = search[variables] - grouped[variables].transform("mean")
    clusters = np.column_stack([pd.factorize(search["api_id"])[0], pd.factorize(search["cell"])[0]])
    ranking = sm.OLS(y, x).fit(cov_type="cluster", cov_kwds={"groups": clusters})
    ranking_table = pd.DataFrame(
        [
            {
                "Variable": LABELS.get(variable, variable),
                "Within-query visibility": fmt(float(ranking.params[variable]), float(ranking.bse[variable]), float(ranking.pvalues[variable])),
            }
            for variable in variables
        ]
        + [
            {"Variable": "N", "Within-query visibility": int(ranking.nobs)},
            {"Variable": "Query x sort FE", "Within-query visibility": "Yes"},
            {"Variable": "Two-way clustered SE", "Within-query visibility": "API and query cell"},
        ]
    )
    save_table("search_ranking_mechanism", ranking_table)

    search["inverse_rank"] = 1 / numeric(search["search_rank"]).clip(lower=1)
    search["top50"] = (numeric(search["search_rank"]) <= 50).astype(int)
    collapsed = (
        search.groupby(["api_id", "search_sort"])
        .agg(inverse_rank=("inverse_rank", "sum"), term_count=("search_term", "nunique"), top50=("top50", "sum"))
        .reset_index()
    )
    collapsed["sort"] = collapsed["search_sort"].map(
        {"ByRelevance": "rel", "ByAlphabetical": "alpha", "ByUpdatedAt": "updated"}
    )
    wide = collapsed.pivot(index="api_id", columns="sort", values=["inverse_rank", "term_count", "top50"])
    wide.columns = ["_".join(col) for col in wide.columns]
    merged = api.merge(wide.reset_index(), on="api_id", how="left")
    for col in ["inverse_rank_rel", "inverse_rank_alpha", "inverse_rank_updated"]:
        merged[col] = numeric(merged[col])
        merged[col.replace("inverse_rank", "exposure") + "_z"] = zscore(np.log1p(merged[col]))
    merged["name_len"] = merged["api_name"].fillna("").astype(str).str.len()
    merged["name_initial"] = merged["api_name"].fillna("").astype(str).str[:1].str.lower()
    merged.loc[~merged["name_initial"].str.match("[a-z]", na=False), "name_initial"] = "other"
    controls = [
        "has_free_plan",
        "ln_price",
        "data_scope_index",
        "data_complexity_index",
        "disclosure_index",
        "reliability_index",
        "versioning_index",
        "ln_api_age",
        "ln_owner_size",
        "name_len",
    ]
    exog = pd.concat(
        [
            pd.Series(1.0, index=merged.index, name="constant"),
            merged[controls].astype(float),
            pd.get_dummies(merged[["primary_type", "name_initial"]], drop_first=True, dtype=float),
        ],
        axis=1,
    )
    owner_clusters = pd.factorize(merged["owner_key"])[0]
    model = IV2SLS(
        merged["ln_flow"],
        exog,
        merged[["exposure_rel_z"]],
        merged[["exposure_alpha_z"]],
    ).fit(cov_type="clustered", clusters=owner_clusters)
    diag = model.first_stage.diagnostics.loc["exposure_rel_z"]
    exposure_table = pd.DataFrame(
        [
            {"Statistic": "2SLS relevance exposure effect", "Value": float(model.params["exposure_rel_z"])},
            {"Statistic": "Clustered standard error", "Value": float(model.std_errors["exposure_rel_z"])},
            {"Statistic": "p-value", "Value": float(model.pvalues["exposure_rel_z"])},
            {"Statistic": "First-stage chi-square", "Value": float(diag["f.stat"])},
            {"Statistic": "Partial R-squared", "Value": float(diag["partial.rsquared"])},
            {"Statistic": "Excluded instrument", "Value": "Alphabetical-sort exposure"},
        ]
    )
    save_table("search_exposure_iv", exposure_table)
    key = {
        "ranking_n": int(ranking.nobs),
        "query_cells": int(search["cell"].nunique()),
        "rank_q": float(ranking.params["ln_q_search"]),
        "iv_exposure": float(model.params["exposure_rel_z"]),
        "iv_exposure_se": float(model.std_errors["exposure_rel_z"]),
        "iv_first_stage": float(diag["f.stat"]),
        "iv_partial_r2": float(diag["partial.rsquared"]),
    }
    return ranking_table, exposure_table, key


def external_diffusion(api: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    variables = [
        "ln_subscriptions",
        "has_free_plan",
        "data_scope_index",
        "data_complexity_index",
        "disclosure_index",
        "reliability_index",
        "open_best_score",
        "schema_overlap_best",
        "ln_schema_near",
        "ln_owner_size",
    ]
    x = market_design(api, variables)
    model = sm.OLS(api["any_github"], x).fit(
        cov_type="cluster", cov_kwds={"groups": pd.factorize(api["owner_key"])[0]}
    )
    rows = []
    for variable in variables:
        b, se, p = result_value(model, variable)
        rows.append({"Variable": LABELS.get(variable, variable), "Any public GitHub repository": fmt(b, se, p)})
    rows.extend(
        [
            {"Variable": "Positive outcomes", "Any public GitHub repository": int(api["any_github"].sum())},
            {"Variable": "N", "Any public GitHub repository": int(model.nobs)},
            {"Variable": "Market FE", "Any public GitHub repository": "Yes"},
        ]
    )
    table = pd.DataFrame(rows)
    save_table("external_diffusion_validation", table)
    return table, {
        "positive": int(api["any_github"].sum()),
        "subscription_beta": float(model.params["ln_subscriptions"]),
        "subscription_se": float(model.bse["ln_subscriptions"]),
        "scope_beta": float(model.params["data_scope_index"]),
        "scope_se": float(model.bse["data_scope_index"]),
    }


def differentiation_instruments(api: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    formulation = pyblp.Formulation(
        "0 + data_scope_index + data_complexity_index + disclosure_index + reliability_index"
    )
    matrix = pyblp.build_differentiation_instruments(formulation, api, version="local").astype(float)
    names = []
    for k in range(matrix.shape[1]):
        name = f"diff_iv_{k}"
        api[name] = zscore(pd.Series(matrix[:, k], index=api.index))
        names.append(name)
    return api, names


def price_identification(api: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float], list[str], pd.DataFrame]:
    api = api.loc[api["prices"] > 0].copy().reset_index(drop=True)
    api, diff = differentiation_instruments(api)
    contract = [
        "menu_has_hard_limit",
        "menu_has_soft_limit",
        "menu_has_rate_limit",
        "ln_max_overage_price",
        "mean_limits_n",
        "menu_endpoint_limited_share",
    ]
    owner = ["z_owner_other_market_price", "z_owner_other_market_versioning"]
    for col in contract + owner:
        api[col] = zscore(api[col])
    controls = [
        "has_free_plan",
        "trial_learning",
        "ln_free_quota",
        "ln_max_paid_quota",
        "data_scope_index",
        "data_complexity_index",
        "disclosure_index",
        "reliability_index",
        "ln_public_plan_count",
        "versioning_index",
        "has_restricted_plan",
        "open_best_score",
        "schema_overlap_best",
        "ln_api_age",
    ]
    exog = pd.concat(
        [
            pd.Series(1.0, index=api.index, name="constant"),
            api[controls].astype(float),
            pd.get_dummies(api["primary_type"], prefix="market", drop_first=True, dtype=float),
        ],
        axis=1,
    )
    clusters = pd.factorize(api["owner_key"])[0]
    sets = {
        "Differentiation IVs": diff,
        "Owner other-market IVs": owner,
        "All governance IVs": contract,
        "Overage price only": ["ln_max_overage_price"],
        "Overage price + number of limits": ["ln_max_overage_price", "mean_limits_n"],
        "All IV groups": diff + owner + contract,
    }
    preferred_instruments = ["ln_max_overage_price", "mean_limits_n"]
    ols_x = pd.concat([exog, api[["prices"]]], axis=1)
    ols = sm.OLS(api["delta_logit"], ols_x).fit(
        cov_type="cluster", cov_kwds={"groups": clusters}
    )
    rows = [
        {
            "Instrument set": "OLS (no excluded instrument)",
            "Price coefficient": float(ols.params["prices"]),
            "Clustered SE": float(ols.bse["prices"]),
            "p-value": float(ols.pvalues["prices"]),
            "First-stage chi-square": np.nan,
            "Partial R-squared": np.nan,
            "Overidentification p-value": np.nan,
        }
    ]
    fitted = {}
    for label, instruments in sets.items():
        model = IV2SLS(api["delta_logit"], exog, api[["prices"]], api[instruments]).fit(
            cov_type="clustered", clusters=clusters
        )
        fitted[label] = model
        diag = model.first_stage.diagnostics.loc["prices"]
        overid_p = np.nan
        if len(instruments) > 1:
            try:
                overid_p = float(model.wooldridge_overid.pval)
            except Exception:
                pass
        rows.append(
            {
                "Instrument set": label,
                "Price coefficient": float(model.params["prices"]),
                "Clustered SE": float(model.std_errors["prices"]),
                "p-value": float(model.pvalues["prices"]),
                "First-stage chi-square": float(diag["f.stat"]),
                "Partial R-squared": float(diag["partial.rsquared"]),
                "Overidentification p-value": overid_p,
            }
        )
    liml = IVLIML(
        api["delta_logit"], exog, api[["prices"]], api[preferred_instruments]
    ).fit(cov_type="clustered", clusters=clusters)
    liml_diag = liml.first_stage.diagnostics.loc["prices"]
    rows.append(
        {
            "Instrument set": "Governance IVs (LIML)",
            "Price coefficient": float(liml.params["prices"]),
            "Clustered SE": float(liml.std_errors["prices"]),
            "p-value": float(liml.pvalues["prices"]),
            "First-stage chi-square": float(liml_diag["f.stat"]),
            "Partial R-squared": float(liml_diag["partial.rsquared"]),
            "Overidentification p-value": np.nan,
        }
    )
    table = pd.DataFrame(rows)
    save_table("price_identification", table)

    ar_z = "ln_max_overage_price"
    ar_x = pd.concat([exog, api[preferred_instruments]], axis=1)
    restriction = np.zeros((len(preferred_instruments), ar_x.shape[1]))
    for row_index, instrument in enumerate(preferred_instruments):
        restriction[row_index, ar_x.columns.get_loc(instrument)] = 1
    ar_rows = []
    for candidate in np.linspace(-20, 10, 301):
        model = sm.OLS(api["delta_logit"] - candidate * api["prices"], ar_x).fit(
            cov_type="cluster", cov_kwds={"groups": clusters}
        )
        pvalue = float(np.asarray(model.f_test(restriction).pvalue).ravel()[0])
        ar_rows.append({"Candidate price coefficient": candidate, "AR p-value": pvalue})
    ar = pd.DataFrame(ar_rows)
    accepted = ar.loc[ar["AR p-value"] >= 0.05, "Candidate price coefficient"]
    ar_low = float(accepted.min()) if not accepted.empty else np.nan
    ar_high = float(accepted.max()) if not accepted.empty else np.nan
    ar["Accepted at 5 percent"] = ar["AR p-value"] >= 0.05
    save_table("anderson_rubin_grid", ar)

    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    ax.plot(ar["Candidate price coefficient"], ar["AR p-value"], color="#15616d", linewidth=2)
    ax.axhline(0.05, color="#a23e48", linestyle="--", linewidth=1)
    if np.isfinite(ar_low):
        ax.axvspan(ar_low, ar_high, color="#edc4b3", alpha=0.45)
    ax.set(xlabel="Candidate price coefficient (price in USD 100s)", ylabel="Anderson-Rubin p-value")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIGURES / "anderson_rubin_price_identification.png", dpi=220)
    plt.close(fig)

    sensitivity = []
    for weight in np.linspace(0, 1, 11):
        price = api["prices"] * ((1 - api["has_free_plan"]) + weight * api["has_free_plan"])
        model = IV2SLS(api["delta_logit"], exog, price.rename("effective_price"), api[[ar_z]]).fit(
            cov_type="clustered", clusters=clusters
        )
        diag = model.first_stage.diagnostics.loc["effective_price"]
        sensitivity.append(
            {
                "Paid-upgrade weight for free-tier APIs": weight,
                "Price coefficient": float(model.params["effective_price"]),
                "Clustered SE": float(model.std_errors["effective_price"]),
                "First-stage chi-square": float(diag["f.stat"]),
            }
        )
    sensitivity_frame = pd.DataFrame(sensitivity)
    save_table("price_definition_sensitivity", sensitivity_frame)
    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    ax.plot(
        sensitivity_frame["Paid-upgrade weight for free-tier APIs"],
        sensitivity_frame["Price coefficient"],
        color="#15616d",
        marker="o",
    )
    ax.fill_between(
        sensitivity_frame["Paid-upgrade weight for free-tier APIs"],
        sensitivity_frame["Price coefficient"] - 1.96 * sensitivity_frame["Clustered SE"],
        sensitivity_frame["Price coefficient"] + 1.96 * sensitivity_frame["Clustered SE"],
        color="#8ecae6",
        alpha=0.35,
    )
    ax.axhline(0, color="#4a4a4a", linewidth=1)
    ax.set(xlabel="Weight placed on the paid upgrade price", ylabel="Estimated price coefficient")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIGURES / "price_definition_sensitivity.png", dpi=220)
    plt.close(fig)

    conley_rows = []
    for direct_effect in np.linspace(-0.75, 0.75, 121):
        adjusted_outcome = api["delta_logit"] - direct_effect * api[ar_z]
        model = IV2SLS(adjusted_outcome, exog, api[["prices"]], api[[ar_z]]).fit(
            cov_type="clustered", clusters=clusters
        )
        conley_rows.append(
            {
                "Assumed direct utility effect of overage instrument": direct_effect,
                "Price coefficient": float(model.params["prices"]),
                "Clustered SE": float(model.std_errors["prices"]),
            }
        )
    conley = pd.DataFrame(conley_rows)
    save_table("price_plausibly_exogenous_path", conley)
    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    ax.plot(conley.iloc[:, 0], conley["Price coefficient"], color="#15616d", linewidth=2)
    ax.fill_between(
        conley.iloc[:, 0],
        conley["Price coefficient"] - 1.96 * conley["Clustered SE"],
        conley["Price coefficient"] + 1.96 * conley["Clustered SE"],
        color="#8ecae6",
        alpha=0.35,
    )
    ax.axhline(0, color="#4a4a4a", linewidth=1)
    ax.axvline(0, color="#4a4a4a", linewidth=1)
    ax.set(
        xlabel="Assumed direct utility effect of the overage instrument",
        ylabel="Estimated price coefficient",
    )
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIGURES / "price_plausibly_exogenous_path.png", dpi=220)
    plt.close(fig)

    leave_out_rows = []
    for omitted in ["None", *sorted(api["primary_type"].unique())]:
        keep = pd.Series(True, index=api.index) if omitted == "None" else api["primary_type"] != omitted
        exog_sub = exog.loc[keep].copy()
        varying = exog_sub.std(axis=0) > 1e-12
        varying.loc["constant"] = True
        exog_sub = exog_sub.loc[:, varying]
        combined = np.column_stack([exog_sub.to_numpy(), api.loc[keep, "prices"].to_numpy()])
        if np.linalg.matrix_rank(combined) < combined.shape[1]:
            market_columns = [column for column in exog_sub if column.startswith("market_")]
            if market_columns:
                exog_sub = exog_sub.drop(columns=market_columns[0])
        model = IV2SLS(
            api.loc[keep, "delta_logit"],
            exog_sub,
            api.loc[keep, ["prices"]],
            api.loc[keep, preferred_instruments],
        ).fit(cov_type="clustered", clusters=clusters[keep])
        diag = model.first_stage.diagnostics.loc["prices"]
        leave_out_rows.append(
            {
                "Market omitted": omitted,
                "Price coefficient": float(model.params["prices"]),
                "Clustered SE": float(model.std_errors["prices"]),
                "First-stage chi-square": float(diag["f.stat"]),
            }
        )
    leave_out = pd.DataFrame(leave_out_rows)
    save_table("price_leave_one_market_out", leave_out)

    copy_rows = []
    for copying in np.linspace(0, 4, 41):
        true_use = api["q_flow"] * (1 + copying * (0.2 + 0.8 * api["reuse_rank"]))
        market_total = true_use.groupby(api["primary_type"]).transform("sum")
        delta_copy = np.log(0.20 * true_use / market_total) - np.log(0.80)
        model = IV2SLS(delta_copy, exog, api[["prices"]], api[preferred_instruments]).fit(
            cov_type="clustered", clusters=clusters
        )
        copy_rows.append(
            {
                "Copying/reuse intensity": copying,
                "Price coefficient": float(model.params["prices"]),
                "Clustered SE": float(model.std_errors["prices"]),
            }
        )
    copy_sensitivity = pd.DataFrame(copy_rows)
    save_table("price_copying_measurement_sensitivity", copy_sensitivity)

    balance_rows = []
    for instrument in preferred_instruments:
        z_within = api[instrument] - api.groupby("primary_type")[instrument].transform("mean")
        for variable in [
            "data_scope_index",
            "data_complexity_index",
            "disclosure_index",
            "reliability_index",
            "ln_api_age",
            "ln_owner_size",
            "open_best_score",
            "schema_overlap_best",
        ]:
            x_within = api[variable] - api.groupby("primary_type")[variable].transform("mean")
            balance_rows.append(
                {
                    "Instrument": instrument,
                    "Observed product attribute": variable,
                    "Within-market correlation": float(z_within.corr(x_within)),
                }
            )
    balance = pd.DataFrame(balance_rows)
    save_table("price_instrument_balance", balance)

    preferred = fitted["Overage price + number of limits"]
    key = {
        "contract_price": float(preferred.params["prices"]),
        "contract_price_se": float(preferred.std_errors["prices"]),
        "contract_first_stage": float(preferred.first_stage.diagnostics.loc["prices", "f.stat"]),
        "contract_overid_p": float(preferred.wooldridge_overid.pval),
        "overage_price": float(fitted["Overage price only"].params["prices"]),
        "overage_price_se": float(fitted["Overage price only"].std_errors["prices"]),
        "ar_low": ar_low,
        "ar_high": ar_high,
        "diff_price": float(fitted["Differentiation IVs"].params["prices"]),
        "diff_price_se": float(fitted["Differentiation IVs"].std_errors["prices"]),
        "owner_price": float(fitted["Owner other-market IVs"].params["prices"]),
        "owner_price_se": float(fitted["Owner other-market IVs"].std_errors["prices"]),
        "ols_price": float(ols.params["prices"]),
        "ols_price_se": float(ols.bse["prices"]),
        "conley_negative_share": float((conley["Price coefficient"] < 0).mean()),
        "conley_zero_crossing": float(
            conley.loc[conley["Price coefficient"].abs().idxmin(), conley.columns[0]]
        ),
        "leave_out_negative_share": float((leave_out["Price coefficient"] < 0).mean()),
        "copy_price_min": float(copy_sensitivity["Price coefficient"].min()),
        "copy_price_max": float(copy_sensitivity["Price coefficient"].max()),
        "liml_price": float(liml.params["prices"]),
        "liml_price_se": float(liml.std_errors["prices"]),
        "price_sample_n": len(api),
        "ar_grid_touches_boundary": bool(
            not accepted.empty
            and (accepted.min() == ar["Candidate price coefficient"].min() or accepted.max() == ar["Candidate price coefficient"].max())
        ),
    }
    return table, sensitivity_frame, key, diff, api


def solve_blp(api: pd.DataFrame, diff: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float], object, pd.DataFrame]:
    pyblp.options.verbose = False

    entry = api.copy()
    entry["prices"] = entry["entry_prices"]
    for k, col in enumerate(diff):
        entry[f"demand_instruments{k}"] = entry[col]
    entry_x1 = pyblp.Formulation(
        "0 + prices + has_free_plan + bayes_learning_index + data_scope_index + data_complexity_index + "
        "disclosure_index + reliability_index + ln_public_plan_count + versioning_index + "
        "has_restricted_plan + open_score_z + schema_overlap_z + ln_api_age",
        absorb="C(market_ids)",
    )
    entry_x2 = pyblp.Formulation("0 + prices + data_scope_index + bayes_learning_index")
    entry_problem = pyblp.Problem(
        (entry_x1, entry_x2),
        entry,
        integration=pyblp.Integration("monte_carlo", size=12, specification_options={"seed": 123}),
    )
    entry_result = entry_problem.solve(
        sigma=np.diag([0.1, 0.1, 0.1]),
        method="1s",
        optimization=pyblp.Optimization("l-bfgs-b", {"gtol": 1e-4, "maxiter": 60}),
        sigma_bounds=(np.zeros((3, 3)), np.full((3, 3), np.inf)),
        se_type="clustered",
    )

    preferred = api.copy()
    for k, col in enumerate(["ln_max_overage_price", "mean_limits_n"]):
        preferred[f"demand_instruments{k}"] = zscore(preferred[col])
    preferred_x1 = pyblp.Formulation(
        "0 + prices + has_free_plan + bayes_learning_index + ln_free_quota + ln_max_paid_quota + "
        "data_scope_index + data_complexity_index + disclosure_index + reliability_index + "
        "ln_public_plan_count + versioning_index + has_restricted_plan + open_score_z + schema_overlap_z + "
        "ln_api_age + menu_has_overage",
        absorb="C(market_ids)",
    )
    preferred_x2 = pyblp.Formulation("0 + prices")
    preferred_problem = pyblp.Problem(
        (preferred_x1, preferred_x2),
        preferred,
        integration=pyblp.Integration("monte_carlo", size=20, specification_options={"seed": 123}),
    )
    preferred_result = preferred_problem.solve(
        sigma=np.array([[0.5]]),
        method="2s",
        optimization=pyblp.Optimization("l-bfgs-b", {"gtol": 1e-5, "maxiter": 100}),
        sigma_bounds=(np.zeros((1, 1)), np.full((1, 1), np.inf)),
        se_type="clustered",
    )

    classified = preferred.loc[preferred["primary_type"] != "other"].copy().reset_index(drop=True)
    classified_problem = pyblp.Problem(
        (preferred_x1, preferred_x2),
        classified,
        integration=pyblp.Integration("monte_carlo", size=20, specification_options={"seed": 123}),
    )
    classified_result = classified_problem.solve(
        sigma=np.array([[0.5]]),
        method="2s",
        optimization=pyblp.Optimization("l-bfgs-b", {"gtol": 1e-5, "maxiter": 100}),
        sigma_bounds=(np.zeros((1, 1)), np.full((1, 1), np.inf)),
        se_type="clustered",
    )

    paid_only = api.loc[(api["has_free_plan"] == 0) & (api["prices"] > 0)].copy().reset_index(drop=True)
    paid_only["demand_instruments0"] = zscore(paid_only["ln_max_overage_price"])
    paid_x1 = pyblp.Formulation(
        "0 + prices + ln_max_paid_quota + data_scope_index + data_complexity_index + disclosure_index + "
        "reliability_index + ln_public_plan_count + versioning_index + open_score_z + "
        "schema_overlap_z + ln_api_age + menu_has_overage",
        absorb="C(market_ids)",
    )
    paid_problem = pyblp.Problem(paid_x1, paid_only)
    paid_result = paid_problem.solve(
        method="2s",
        se_type="clustered",
    )

    rows = []
    for specification, result in [
        ("Entry-price diagnostic", entry_result),
        ("Full-market upgrade-price BLP", preferred_result),
        ("Classified-use-case BLP", classified_result),
        ("Paid-entry-only IV logit", paid_result),
    ]:
        for label, beta, se in zip(result.beta_labels, result.beta.ravel(), result.beta_se.ravel()):
            rows.append(
                {
                    "Specification": specification,
                    "Parameter": LABELS.get(str(label), str(label)),
                    "Estimate": float(beta),
                    "SE": float(se),
                }
            )
        for i, label in enumerate(result.sigma_labels):
            rows.append(
                {
                    "Specification": specification,
                    "Parameter": f"Random-coefficient SD: {label}",
                    "Estimate": float(result.sigma[i, i]),
                    "SE": float(result.sigma_se[i, i]) if np.isfinite(result.sigma_se[i, i]) else np.nan,
                }
            )
    estimates = pd.DataFrame(rows)
    save_table("blp_estimates", estimates)
    diagnostics = pd.DataFrame(
        [
            {
                "Specification": "Entry-price diagnostic",
                "Products": entry_problem.N,
                "Markets": entry_problem.T,
                "Converged": bool(entry_result.converged),
                "Objective": float(entry_result.objective),
                "Price coefficient": float(entry_result.beta[0, 0]),
                "Random price SD": float(entry_result.sigma[0, 0]),
            },
            {
                "Specification": "Full-market upgrade-price BLP",
                "Products": preferred_problem.N,
                "Markets": preferred_problem.T,
                "Converged": bool(preferred_result.converged),
                "Objective": float(preferred_result.objective),
                "Price coefficient": float(preferred_result.beta[0, 0]),
                "Random price SD": float(preferred_result.sigma[0, 0]),
            },
            {
                "Specification": "Paid-entry-only IV logit",
                "Products": paid_problem.N,
                "Markets": paid_problem.T,
                "Converged": bool(paid_result.converged),
                "Objective": float(paid_result.objective),
                "Price coefficient": float(paid_result.beta[0, 0]),
                "Random price SD": np.nan,
            },
            {
                "Specification": "Classified-use-case BLP",
                "Products": classified_problem.N,
                "Markets": classified_problem.T,
                "Converged": bool(classified_result.converged),
                "Objective": float(classified_result.objective),
                "Price coefficient": float(classified_result.beta[0, 0]),
                "Random price SD": float(classified_result.sigma[0, 0]),
            },
        ]
    )
    save_table("blp_diagnostics", diagnostics)
    beta = {str(label): float(value) for label, value in zip(preferred_result.beta_labels, preferred_result.beta.ravel())}
    key = {
        "entry_price": float(entry_result.beta[0, 0]),
        "entry_price_se": float(entry_result.beta_se[0, 0]),
        "preferred_price": float(preferred_result.beta[0, 0]),
        "preferred_price_se": float(preferred_result.beta_se[0, 0]),
        "price_sigma": float(preferred_result.sigma[0, 0]),
        "price_sigma_se": float(preferred_result.sigma_se[0, 0]),
        "paid_only_price": float(paid_result.beta[0, 0]),
        "paid_only_price_se": float(paid_result.beta_se[0, 0]),
        "paid_only_sigma": np.nan,
        "paid_only_sigma_se": np.nan,
        "classified_price": float(classified_result.beta[0, 0]),
        "classified_price_se": float(classified_result.beta_se[0, 0]),
        "classified_sigma": float(classified_result.sigma[0, 0]),
        "classified_sigma_se": float(classified_result.sigma_se[0, 0]),
        "full_products": int(preferred_problem.N),
        "classified_products": int(classified_problem.N),
        "paid_only_products": int(paid_problem.N),
        **{f"beta_{key}": value for key, value in beta.items()},
    }
    return estimates, diagnostics, key, preferred_result, preferred


def simulate(api: pd.DataFrame, utility_shock: np.ndarray, price_usd: np.ndarray, beta: float, conversion: float = 0.25) -> dict[str, float]:
    adoption = revenue = surplus = 0.0
    for _, group in api.assign(_shock=utility_shock, _price=price_usd).groupby("primary_type", sort=False):
        delta = group["delta_logit"].to_numpy(float) + group["_shock"].to_numpy(float)
        maximum = max(0.0, float(delta.max()))
        denominator_scaled = math.exp(-maximum) + float(np.exp(delta - maximum).sum())
        shares = np.exp(delta - maximum) / denominator_scaled
        market_size = float(group["market_size"].iloc[0])
        quantity = market_size * shares
        free = group["has_free_plan"].to_numpy(float)
        expected_payment = group["_price"].to_numpy(float) * ((1 - free) + conversion * free)
        adoption += float(quantity.sum())
        revenue += float((quantity * expected_payment).sum())
        if beta < 0:
            logsum = maximum + math.log(denominator_scaled)
            surplus += market_size * 100 / (-beta) * logsum
    return {"adoption": adoption, "revenue": revenue, "consumer_surplus": surplus}


def pct(value: float, baseline: float) -> float:
    return 100 * (value / baseline - 1) if baseline != 0 else np.nan


def counterfactuals(api: pd.DataFrame, blp_key: dict[str, float], price_key: dict[str, float]) -> tuple[pd.DataFrame, dict[str, float]]:
    beta_point = blp_key["preferred_price"]
    beta_low = price_key["ar_low"] if np.isfinite(price_key["ar_low"]) else beta_point * 1.5
    beta_high = price_key["ar_high"] if np.isfinite(price_key["ar_high"]) else beta_point * 0.5
    zero = np.zeros(len(api))
    base_price = api["upgrade_price_usd"].to_numpy(float)

    price_rows = []
    for factor in np.linspace(0.5, 1.5, 101):
        row = {"Price multiplier": factor}
        for label, beta in [("AR low", beta_low), ("BLP point", beta_point), ("AR high", beta_high)]:
            baseline = simulate(api, zero, base_price, beta)
            shock = beta * (api["prices"].to_numpy(float) * factor - api["prices"].to_numpy(float))
            outcome = simulate(api, shock, base_price * factor, beta)
            row[f"Adoption change: {label}"] = pct(outcome["adoption"], baseline["adoption"])
            row[f"Revenue change: {label}"] = pct(outcome["revenue"], baseline["revenue"])
            row[f"Consumer surplus change: {label}"] = pct(outcome["consumer_surplus"], baseline["consumer_surplus"])
        price_rows.append(row)
    price_path = pd.DataFrame(price_rows)
    save_table("counterfactual_price_path", price_path)

    beta_free = blp_key.get("beta_has_free_plan", 0.0)
    beta_trial = blp_key.get("beta_bayes_learning_index", 0.0)
    baseline = simulate(api, zero, base_price, beta_point)
    trial_rows = []
    for scale in np.linspace(0, 2, 81):
        shock = (scale - 1) * (
            beta_free * api["has_free_plan"].to_numpy(float)
            + beta_trial * api["bayes_learning_index"].to_numpy(float)
        )
        outcome = simulate(api, shock, base_price, beta_point)
        trial_rows.append(
            {
                "Trial information scale": scale,
                "Adoption change": pct(outcome["adoption"], baseline["adoption"]),
                "Revenue change": pct(outcome["revenue"], baseline["revenue"]),
                "Consumer surplus change": pct(outcome["consumer_surplus"], baseline["consumer_surplus"]),
            }
        )
    trial_path = pd.DataFrame(trial_rows)
    save_table("counterfactual_trial_path", trial_path)

    beta_disclosure = blp_key.get("beta_disclosure_index", 0.0)
    low_disclosure = (api["disclosure_index"].rank(method="first", pct=True) <= 0.25).to_numpy(float)
    disclosure_rows = []
    for lift in np.linspace(0, 2, 81):
        outcome = simulate(api, beta_disclosure * lift * low_disclosure, base_price, beta_point)
        disclosure_rows.append(
            {
                "Disclosure lift (SD)": lift,
                "Adoption change": pct(outcome["adoption"], baseline["adoption"]),
                "Revenue change": pct(outcome["revenue"], baseline["revenue"]),
                "Consumer surplus change": pct(outcome["consumer_surplus"], baseline["consumer_surplus"]),
            }
        )
    disclosure_path = pd.DataFrame(disclosure_rows)
    save_table("counterfactual_disclosure_path", disclosure_path)

    beta_open = blp_key.get("beta_open_score_z", 0.0)
    exposed = (api["open_best_score"] >= 0.20).to_numpy(float)
    open_rows = []
    for lift in np.linspace(0, 2, 81):
        outcome = simulate(api, beta_open * lift * exposed, base_price, beta_point)
        open_rows.append(
            {
                "Open-substitute salience lift (SD)": lift,
                "Adoption change": pct(outcome["adoption"], baseline["adoption"]),
                "Revenue change": pct(outcome["revenue"], baseline["revenue"]),
                "Consumer surplus change": pct(outcome["consumer_surplus"], baseline["consumer_surplus"]),
            }
        )
    open_path = pd.DataFrame(open_rows)
    save_table("counterfactual_open_substitute_path", open_path)

    conversion_rows = []
    base_conversion = simulate(api, zero, base_price, beta_point, conversion=0.25)
    for conversion in np.linspace(0, 1, 101):
        outcome = simulate(api, zero, base_price, beta_point, conversion=conversion)
        conversion_rows.append(
            {
                "Free-to-paid conversion rate": conversion,
                "Revenue change relative to 25 percent": pct(outcome["revenue"], base_conversion["revenue"]),
            }
        )
    conversion_path = pd.DataFrame(conversion_rows)
    save_table("counterfactual_conversion_path", conversion_path)

    beta_governance = blp_key.get("beta_menu_has_overage", 0.0)
    beta_restriction = blp_key.get("beta_has_restricted_plan", 0.0)
    governance_utility = (
        beta_governance * api["menu_has_overage"].to_numpy(float)
        + beta_restriction * api["has_restricted_plan"].to_numpy(float)
    )
    governance_rows = []
    for scale in np.linspace(0, 2, 81):
        shock = (scale - 1) * governance_utility
        outcome = simulate(api, shock, base_price, beta_point)
        governance_rows.append(
            {
                "Access-governance utility scale": scale,
                "Adoption change": pct(outcome["adoption"], baseline["adoption"]),
                "Revenue change": pct(outcome["revenue"], baseline["revenue"]),
                "Consumer surplus change": pct(outcome["consumer_surplus"], baseline["consumer_surplus"]),
            }
        )
    governance_path = pd.DataFrame(governance_rows)
    save_table("counterfactual_governance_path", governance_path)

    reuse_score = zscore(api["data_scope_index"] + api["schema_overlap_z"] + api["any_github"])
    reuse_rank = reuse_score.rank(pct=True).to_numpy(float)
    observed_use = float(api["q_flow"].sum())
    copy_rows = []
    for copying in np.linspace(0, 4, 81):
        multiplier = 1 + copying * (0.2 + 0.8 * reuse_rank)
        true_use = float((api["q_flow"].to_numpy(float) * multiplier).sum())
        copy_rows.append(
            {
                "Copying/reuse intensity": copying,
                "Mean use multiplier": float(multiplier.mean()),
                "Downstream use above platform subscriptions": pct(true_use, observed_use),
            }
        )
    copy_path = pd.DataFrame(copy_rows)
    save_table("counterfactual_copying_path", copy_path)

    outside_rows = []
    for inside_share in np.linspace(0.05, 0.50, 46):
        scaled = api.copy()
        scaled["market_size"] = scaled.groupby("primary_type")["q_flow"].transform("sum") / inside_share
        scaled["shares"] = scaled["q_flow"] / scaled["market_size"]
        scaled["delta_logit"] = np.log(scaled["shares"]) - np.log(1 - inside_share)
        base_scaled = simulate(scaled, zero, base_price, beta_point)
        price_shock = beta_point * (scaled["prices"].to_numpy(float) * 1.10 - scaled["prices"].to_numpy(float))
        higher_price = simulate(scaled, price_shock, base_price * 1.10, beta_point)
        outside_rows.append(
            {
                "Assumed aggregate inside share": inside_share,
                "Adoption change from 10 percent price increase": pct(
                    higher_price["adoption"], base_scaled["adoption"]
                ),
                "Revenue change from 10 percent price increase": pct(
                    higher_price["revenue"], base_scaled["revenue"]
                ),
            }
        )
    outside_path = pd.DataFrame(outside_rows)
    save_table("counterfactual_market_size_path", outside_path)

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    ax.plot(price_path["Price multiplier"], price_path["Adoption change: BLP point"], color="#15616d", linewidth=2, label="Adoption")
    ax.fill_between(
        price_path["Price multiplier"],
        np.minimum(price_path["Adoption change: AR low"], price_path["Adoption change: AR high"]),
        np.maximum(price_path["Adoption change: AR low"], price_path["Adoption change: AR high"]),
        color="#8ecae6",
        alpha=0.35,
        label="AR-robust price-response range",
    )
    ax.plot(price_path["Price multiplier"], price_path["Revenue change: BLP point"], color="#a23e48", linewidth=2, label="Seller revenue proxy")
    ax.axvline(1, color="#4a4a4a", linewidth=1)
    ax.axhline(0, color="#4a4a4a", linewidth=1)
    ax.set(xlabel="Paid upgrade price multiplier", ylabel="Percent change from baseline")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIGURES / "counterfactual_price_path.png", dpi=220)
    plt.close(fig)

    fig, axes = plt.subplots(2, 2, figsize=(10.4, 7.2))
    axes[0, 0].plot(trial_path.iloc[:, 0], trial_path["Adoption change"], color="#15616d")
    axes[0, 0].set(title="Trial information", xlabel="Scale", ylabel="Adoption change (%)")
    axes[0, 1].plot(disclosure_path.iloc[:, 0], disclosure_path["Adoption change"], color="#2a9d8f")
    axes[0, 1].set(title="Disclosure for low-disclosure APIs", xlabel="Lift (SD)", ylabel="Adoption change (%)")
    axes[1, 0].plot(open_path.iloc[:, 0], open_path["Adoption change"], color="#a23e48")
    axes[1, 0].set(title="Open substitute salience", xlabel="Lift (SD)", ylabel="Adoption change (%)")
    axes[1, 1].plot(copy_path.iloc[:, 0], copy_path.iloc[:, 2], color="#6d597a")
    axes[1, 1].set(title="Copying and internal reuse", xlabel="Reuse intensity", ylabel="Use above observed (%)")
    for ax in axes.ravel():
        ax.axhline(0, color="#777777", linewidth=0.8)
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIGURES / "counterfactual_mechanism_paths.png", dpi=220)
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(11.2, 3.8))
    axes[0].plot(
        conversion_path["Free-to-paid conversion rate"],
        conversion_path["Revenue change relative to 25 percent"],
        color="#15616d",
    )
    axes[0].axvline(0.25, color="#777777", linewidth=0.8)
    axes[0].set(title="Free-to-paid conversion", xlabel="Conversion rate", ylabel="Revenue change (%)")
    axes[1].plot(governance_path.iloc[:, 0], governance_path["Adoption change"], color="#2a9d8f")
    axes[1].axvline(1, color="#777777", linewidth=0.8)
    axes[1].set(title="Access governance", xlabel="Utility scale", ylabel="Adoption change (%)")
    axes[2].plot(outside_path.iloc[:, 0], outside_path.iloc[:, 1], color="#a23e48")
    axes[2].set(title="Market-size normalization", xlabel="Inside share", ylabel="Price +10% adoption change")
    for ax in axes:
        ax.axhline(0, color="#777777", linewidth=0.8)
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIGURES / "counterfactual_monetization_paths.png", dpi=220)
    plt.close(fig)

    def nearest(frame: pd.DataFrame, column: str, value: float) -> pd.Series:
        return frame.iloc[(frame[column] - value).abs().argmin()]

    price_110 = nearest(price_path, "Price multiplier", 1.10)
    trial_zero = nearest(trial_path, "Trial information scale", 0.0)
    disclosure_one = nearest(disclosure_path, "Disclosure lift (SD)", 1.0)
    open_one = nearest(open_path, "Open-substitute salience lift (SD)", 1.0)
    copy_one = nearest(copy_path, "Copying/reuse intensity", 1.0)
    governance_zero = nearest(governance_path, "Access-governance utility scale", 0.0)
    summary = pd.DataFrame(
        [
            {"Counterfactual": "Paid upgrade prices +10%", "Adoption change": price_110["Adoption change: BLP point"], "Revenue/use change": price_110["Revenue change: BLP point"]},
            {"Counterfactual": "Remove conditional free-access and learning utility", "Adoption change": trial_zero["Adoption change"], "Revenue/use change": trial_zero["Revenue change"]},
            {"Counterfactual": "Low-disclosure APIs improve disclosure by 1 SD", "Adoption change": disclosure_one["Adoption change"], "Revenue/use change": disclosure_one["Revenue change"]},
            {"Counterfactual": "Open-substitute salience rises by 1 SD", "Adoption change": open_one["Adoption change"], "Revenue/use change": open_one["Revenue change"]},
            {"Counterfactual": "Copying/reuse intensity equals 1", "Adoption change": np.nan, "Revenue/use change": copy_one["Downstream use above platform subscriptions"]},
            {"Counterfactual": "Remove conditional access-governance utility", "Adoption change": governance_zero["Adoption change"], "Revenue/use change": governance_zero["Revenue change"]},
        ]
    )
    save_table("counterfactual_summary", summary)
    return summary, {
        "price10_adoption": float(price_110["Adoption change: BLP point"]),
        "price10_adoption_low": float(min(price_110["Adoption change: AR low"], price_110["Adoption change: AR high"])),
        "price10_adoption_high": float(max(price_110["Adoption change: AR low"], price_110["Adoption change: AR high"])),
        "price10_revenue": float(price_110["Revenue change: BLP point"]),
        "trial_remove": float(trial_zero["Adoption change"]),
        "disclosure_lift": float(disclosure_one["Adoption change"]),
        "open_lift": float(open_one["Adoption change"]),
        "copy_one": float(copy_one["Downstream use above platform subscriptions"]),
        "governance_remove": float(governance_zero["Adoption change"]),
        "outside_price_min": float(outside_path.iloc[:, 1].min()),
        "outside_price_max": float(outside_path.iloc[:, 1].max()),
    }


def write_report(
    audit: pd.DataFrame,
    fundamental_summary: pd.DataFrame,
    markets: pd.DataFrame,
    fundamental_key: dict[str, float],
    contract: pd.DataFrame,
    adoption: pd.DataFrame,
    adoption_key: dict[str, float],
    trial: pd.DataFrame,
    trial_key: dict[str, float],
    plan: pd.DataFrame,
    plan_key: dict[str, float],
    supply: pd.DataFrame,
    supply_key: dict[str, float],
    search_ranking: pd.DataFrame,
    search_iv: pd.DataFrame,
    search_key: dict[str, float],
    external: pd.DataFrame,
    external_key: dict[str, float],
    price: pd.DataFrame,
    price_key: dict[str, float],
    blp: pd.DataFrame,
    diagnostics: pd.DataFrame,
    blp_key: dict[str, float],
    counterfactual: pd.DataFrame,
    counterfactual_key: dict[str, float],
) -> Path:
    price_display = price[
        ["Instrument set", "Price coefficient", "Clustered SE", "First-stage chi-square", "Overidentification p-value"]
    ].copy()
    price_display["Price coefficient (SE)"] = price_display.apply(
        lambda row: f"{row['Price coefficient']:.3f} ({row['Clustered SE']:.3f})", axis=1
    )
    price_display = price_display[
        ["Instrument set", "Price coefficient (SE)", "First-stage chi-square", "Overidentification p-value"]
    ]
    report = rf"""# Data Access Contracts, Search, and Substitution in an API Marketplace

# Data

{markdown_table(audit)}

{markdown_table(fundamental_summary)}

{markdown_table(markets)}

{markdown_table(contract[["Contract fact", "Percent", "Denominator"]])}

The empirical unit changes with the mechanism. The product universe contains {fundamental_key['api_count']:,.0f} APIs supplied by {fundamental_key['owner_count']:,.0f} owners in {fundamental_key['market_count']:,.0f} use-case markets. {100 * fundamental_key['free_share']:.1f} percent offer a public free plan, while {100 * fundamental_key['positive_price_share']:.1f} percent expose a positive paid-upgrade price. The median positive upgrade price is USD {fundamental_key['median_positive_price']:.2f} per month. Contract versioning uses plan records and identifies the price-quota schedule from variation within an API. Platform allocation compares products inside the same query and sort. Schema-overlap pairs and external open-data matches measure substitution. Public GitHub repositories provide an outcome outside RapidAPI with which to validate whether platform subscriptions capture broader adoption.

Platform subscriptions are cumulative. The main adoption outcome converts the stock into an exposure-adjusted flow,

$$
q_j^A=\frac{{q_j^P+0.5}}{{\operatorname{{max}}(\mathrm{{age}}_j,0.25)}},
$$

where $q_j^P$ is the platform subscription count. The age floor prevents recently listed APIs from receiving arbitrarily large annualized flows. Stock subscriptions and age controls remain useful robustness checks, but the flow outcome is closer to the static choice object used in demand estimation.

# Reduced Form

The first set of regressions estimates

$$
g(E[q_j^A\mid X_j])=\beta_F F_j+\beta_P\log(1+p_j)+\beta_U(F_j\times U_j)+X_j'\gamma+\mu_m,
$$

where $F_j$ denotes a free plan, $U_j$ is ex ante uncertainty, and $\mu_m$ is a use-case-market fixed effect. The OLS outcome is $\log q_j^A$; PPML uses the subscription count with log age as an exposure offset. The owner-fixed-effect specification compares products sold by the same provider and absorbs persistent seller ability and brand reputation. Rating votes and aggregate search exposure are excluded from the core equation because they can be consequences of adoption.

{markdown_table(adoption)}

The three columns do not support a single mechanical interpretation of free access. In the PPML specification, the free-plan coefficient is {adoption_key['ppml_free']:.3f} with a clustered standard error of {adoption_key['ppml_free_se']:.3f}; it is imprecise once the highly skewed count outcome and owner-level dependence are respected. Within multi-product owners, the coefficient is {adoption_key['owner_fe_free']:.3f} ({adoption_key['owner_fe_free_se']:.3f}). This comparison removes persistent seller heterogeneity, but plan choice can still respond to product-specific demand. The result therefore establishes a strong within-seller association, not a randomized free-trial effect.

Data scope and reliability are more stable. Their owner-fixed-effect coefficients are {adoption_key['owner_fe_scope']:.3f} and {adoption_key['owner_fe_reliability']:.3f}. Broader data increase the set of potential downstream tasks, while reliability determines whether the data can be embedded in a production workflow. The uncertainty interaction is not stable enough to claim that the cross section alone identifies Bayesian learning from trial use. A dynamic design would require plan changes followed by subscription flows.

{markdown_table(trial)}

The trial table maps the theory into progressively richer proxies. The first row uses a linear free-by-uncertainty interaction. The second lets free quota, disclosure, and reliability determine signal precision. The third computes the reduction in posterior variance implied by a normal-signal experiment. Its owner-fixed-effect coefficient is {trial_key['bayes_learning_index_fe']:.3f} ({trial_key['bayes_learning_index_fe_se']:.3f}). These proxies clarify what the learning mechanism requires, but they remain functions of seller-chosen contracts and public information. They discipline the structural decomposition without turning a static cross section into a trial experiment.

# Versioning

For paid public plans, the within-API equation is

$$
\log p_{{jk}}=\eta_j+\rho\log Q_{{jk}}+C_{{jk}}'\psi+\varepsilon_{{jk}},
$$

where $Q_{{jk}}$ is the call allowance and $\eta_j$ absorbs the data source, owner, product quality, and API-level demand. Standard errors are clustered by API.

{markdown_table(plan)}

The quota elasticity is {plan_key['quota_beta']:.3f} ({plan_key['quota_se']:.3f}). Because it is below one, a 1 percent increase in calls is associated with a {plan_key['quota_beta']:.3f} percent increase in the monthly fee and therefore a lower unit price. Among adjacent menu options, {100 * plan_key['monotone']:.1f} percent pair a larger quota with a weakly higher price, while only {100 * plan_key['violations']:.2f} percent lower the total price as quota rises. This is the cleanest evidence in the data: APIs sell versioned access rights with quantity discounts, not physical units with constant marginal production cost.

# Search

{markdown_table(search_ranking)}

The query-by-sort regression asks which APIs receive a better rank among products shown for the same search. Subscription stock, reliability, scope, free access, and price all predict visibility. In particular, the coefficient on log subscriptions is {search_key['rank_q']:.3f}. This documents a feedback mechanism: platform ranking rewards signals associated with prior adoption, so raw exposure cannot be treated as exogenous demand variation.

{markdown_table(search_iv)}

As an auxiliary design, relevance-sort exposure is instrumented with alphabetical-sort exposure while controlling for name length, initial letter, owner size, use-case market, and product characteristics. The estimate is {search_key['iv_exposure']:.3f} ({search_key['iv_exposure_se']:.3f}); the first-stage chi-square is {search_key['iv_first_stage']:.2f}, but the partial $R^2$ is only {search_key['iv_partial_r2']:.3f}. The design is informative about consideration, although it remains vulnerable to persistent naming choices and to the mismatch between current rank and cumulative subscriptions. It should support the platform mechanism rather than carry the paper's main causal claim.

# External Diffusion

{markdown_table(external)}

Only {external_key['positive']} APIs have a matched public GitHub repository, so this outcome is sparse. Conditional on market and product attributes, log platform subscriptions predict external code adoption with a coefficient of {external_key['subscription_beta']:.3f} ({external_key['subscription_se']:.3f}). Data scope also predicts external diffusion. These results validate that the marketplace measure contains adoption information, while also showing that an account-level subscription is not the final unit of data use.

# Demand

For developer or organization $i$, API $j$, and use-case market $m$, utility is

$$
u_{{ijm}}=x_j'\beta-\alpha p_j+\beta_FF_j+\beta_U(F_jU_j)+\sigma_p p_j\nu_i+\xi_j+\epsilon_{{ijm}}.
$$

The observed share is constructed from exposure-adjusted adoption and a baseline inside share of 0.20. The model integrates over the random price coefficient and inverts shares using the BLP contraction. Price is endogenous. Differentiation instruments use local distances in scope, complexity, disclosure, and reliability. Seller instruments use other-market pricing. Governance instruments use overage prices and the number and type of contractual limits.

{markdown_table(price_display)}

The instrument comparison is itself a result. Differentiation IVs produce a price coefficient of {price_key['diff_price']:.3f} ({price_key['diff_price_se']:.3f}) and are weak in this cross section. Owner other-market instruments produce {price_key['owner_price']:.3f} ({price_key['owner_price_se']:.3f}), indicating that seller ability or brand quality contaminates the exclusion restriction. Overage price alone yields {price_key['overage_price']:.3f} ({price_key['overage_price_se']:.3f}); its weak-IV-robust Anderson-Rubin 95 percent set is [{price_key['ar_low']:.1f}, {price_key['ar_high']:.1f}]. Combining overage price and the number of limits gives {price_key['contract_price']:.3f}, but the overidentification p-value is {price_key['contract_overid_p']:.3f}. Contract instruments have useful first stages and the theoretically expected sign, yet buyers may care directly about overage and limits. The report therefore treats them as conditional supply shifters and carries the AR set into counterfactual bounds.

{markdown_table(blp)}

{markdown_table(diagnostics)}

The entry-price diagnostic sets price to zero whenever a free plan exists. It produces a price coefficient of {blp_key['entry_price']:.3f} ({blp_key['entry_price_se']:.3f}), confirming that entry price is inseparable from endogenous free-plan choice. The full-market specification uses {blp_key['full_products']:,} products with an observed positive paid-upgrade price and conditions on the free tier, quotas, plan count, versioning, and observed quality. Its mean price coefficient is {blp_key['preferred_price']:.3f} ({blp_key['preferred_price_se']:.3f}). Excluding the broad residual use-case market leaves {blp_key['classified_products']:,} products and produces {blp_key['classified_price']:.3f} ({blp_key['classified_price_se']:.3f}). In the cleaner sample of {blp_key['paid_only_products']:,} APIs with no free entry tier, a homogeneous IV logit produces {blp_key['paid_only_price']:.3f} ({blp_key['paid_only_price_se']:.3f}); this smaller sample has too few independent moments to estimate another random coefficient. The paid-entry estimate shows how much the full-market coefficient relies on free APIs exposing buyers to later upgrade prices. The estimated full-market random-price standard deviation is {blp_key['price_sigma']:.3f} with a standard error of {blp_key['price_sigma_se']:.3f}. The classified-market random coefficient provides a market-definition diagnostic. If it and the full-market coefficient remain on the boundary, the aggregate cross section identifies a homogeneous-logit mean response rather than random-coefficient dispersion.

# Supply

The platform reports plan prices and quotas but not plan-specific take-up. A conventional multiproduct supply inversion would require the share of subscribers choosing each paid plan. Using total API subscriptions as paid-plan quantity would overstate revenue for approximately {fundamental_key['api_count'] * fundamental_key['free_share']:,.0f} APIs with free access and would generate spurious markups. The supply evidence therefore comes from the within-API menu schedule rather than from a claimed point estimate of marginal cost.

This distinction reflects the economics of data. Replication is nearly costless, but reliable access is not: rate limiting, monitoring, cleaning, compute, legal compliance, and service guarantees create usage-governance costs. The plan regression identifies how sellers price the boundary of access. It does not separately identify accounting marginal cost and information rent.

{markdown_table(supply)}

The public cloud calibration prices only the gateway service needed to deliver calls. The first-tier benchmark is USD {supply_key['gateway_cost_per_million']:.2f} per million requests. At that benchmark, the median implied gateway cost is {100 * supply_key['median_cost_share']:.3f} percent of the plan fee and the P90 is {100 * supply_key['p90_cost_share']:.3f} percent. This small delivery component is consistent with nonrival replication, while leaving data acquisition, cleaning, compute-intensive responses, support, and legal risk outside the calibration. The continuous cost path therefore measures how far service costs can move before posted plan prices cease to cover this narrow variable-cost component; it is not a marginal-cost estimate.

![Service-cost calibration](../figures/nonrival_supply_cost_path.png)

# Counterfactuals

{markdown_table(counterfactual)}

![Price counterfactual](../figures/counterfactual_price_path.png)

![Mechanism counterfactuals](../figures/counterfactual_mechanism_paths.png)

![Monetization counterfactuals](../figures/counterfactual_monetization_paths.png)

All counterfactuals are continuous paths. A 10 percent increase in paid-upgrade prices changes adoption by {counterfactual_key['price10_adoption']:.2f} percent at the BLP point estimate. Carrying the AR set through demand gives a range from {counterfactual_key['price10_adoption_low']:.2f} to {counterfactual_key['price10_adoption_high']:.2f} percent. The corresponding seller-revenue proxy changes by {counterfactual_key['price10_revenue']:.2f} percent under a 25 percent free-to-paid conversion calibration. The price graph makes the identifying uncertainty visible rather than hiding it in a single elasticity.

Removing the estimated trial-information utility changes adoption by {counterfactual_key['trial_remove']:.2f} percent. Raising disclosure by one standard deviation for the bottom disclosure quartile changes adoption by {counterfactual_key['disclosure_lift']:.2f} percent. Increasing open-substitute salience by one standard deviation changes adoption by {counterfactual_key['open_lift']:.2f} percent. These are conditional structural exercises because free access, disclosure, and substitute availability are not randomly assigned.

The copying path changes the mapping from platform subscriptions to downstream use without changing the observed number of contracts:

$$
q_j^D=q_j^P\left[1+\kappa\left(0.2+0.8R_j\right)\right],
$$

where $R_j$ ranks scope, schema replicability, and external code diffusion. At $\kappa=1$, downstream use exceeds platform subscriptions by {counterfactual_key['copy_one']:.2f} percent. The level is calibrated, not estimated. Its purpose is to show why welfare conclusions for a nonrival and shareable data good cannot be read directly from account counts.

# Interpretation

The full data support a coherent paper about the organization of data access. Sellers version a nonrival object through quotas, overage fees, rate limits, endpoint restrictions, approval, and named developers. The platform determines consideration through search ranking. Buyers value scope and reliability, and public code adoption confirms that marketplace subscriptions track broader technological use. BLP organizes substitution and price counterfactuals, while the reduced-form modules identify which parts of the mechanism are strongly supported.

The evidence is strongest for menu versioning and quantity discounts, followed by within-owner adoption differences and query-level ranking allocation. Price elasticity is conditional on governance instruments; random-coefficient heterogeneity is not point identified. Free-trial learning and copying levels require dynamic subscriptions or buyer-side usage data for a causal point estimate. Those boundaries sharpen the paper: the contribution is an IO account of data access contracts and measured downstream reuse, with explicit separation between identified results, weakly identified demand parameters, and calibrated welfare objects.
"""
    path = REPORT / "full_data_blp_analysis.md"
    path.write_text(report, encoding="utf-8")
    return path


def main() -> None:
    global SNAPSHOT_DATE
    parser = argparse.ArgumentParser(description="Run the full static RapidAPI reduced-form and BLP analysis.")
    parser.add_argument("--skip-blp", action="store_true", help="Run reduced forms only.")
    parser.add_argument("--snapshot-date", default=None, help="ISO date used to construct API age.")
    args = parser.parse_args()
    if args.snapshot_date:
        SNAPSHOT_DATE = pd.Timestamp(args.snapshot_date, tz="UTC")
    ensure_dirs()
    api = load_api_data()
    api.to_csv(DATA / "full_api_analysis_panel.csv", index=False)
    audit = sample_audit(api)
    fundamental_summary, markets, fundamental_key = fundamental_analysis(api)
    contract, contract_key = contract_descriptives(api)
    adoption, adoption_key = adoption_reduced_form(api)
    trial, trial_key = trial_learning_reduced_form(api)
    reduced_form_stability(api)
    adoption_specification_curve(api)
    plan, _, plan_key = plan_versioning()
    supply, _, supply_key = nonrival_supply_calibration()
    search_ranking, search_iv, search_key = search_analysis(api)
    external, external_key = external_diffusion(api)
    price, _, price_key, diff, price_sample = price_identification(api)
    if args.skip_blp:
        print("Reduced-form analysis complete. BLP was skipped.")
        return
    blp, diagnostics, blp_key, _, preferred_data = solve_blp(price_sample, diff)
    counterfactual, counterfactual_key = counterfactuals(preferred_data, blp_key, price_key)
    report = write_report(
        audit,
        fundamental_summary,
        markets,
        fundamental_key,
        contract,
        adoption,
        adoption_key,
        trial,
        trial_key,
        plan,
        plan_key,
        supply,
        supply_key,
        search_ranking,
        search_iv,
        search_key,
        external,
        external_key,
        price,
        price_key,
        blp,
        diagnostics,
        blp_key,
        counterfactual,
        counterfactual_key,
    )
    summary = {
        "fundamentals": fundamental_key,
        "contract": contract_key,
        "adoption": adoption_key,
        "trial": trial_key,
        "plan": plan_key,
        "supply": supply_key,
        "search": search_key,
        "external": external_key,
        "price": price_key,
        "blp": blp_key,
        "counterfactual": counterfactual_key,
    }
    (OUT / "analysis_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Full analysis complete: {report}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the full Chinese circulation manuscript from authoritative results."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "rapidapi_io_static" / "full_results"
TABLES = RESULTS / "tables"
REPORT = RESULTS / "report"
OUTPUT = REPORT / "data_access_contracts_submission_zh.md"


def load(name: str) -> pd.DataFrame:
    return pd.read_csv(TABLES / f"{name}.csv", low_memory=False)


def integer(value: float) -> str:
    return f"{int(round(float(value))):,}"


def markdown_table(frame: pd.DataFrame, digits: int = 3) -> str:
    clean = frame.copy()
    for column in clean.columns:
        if pd.api.types.is_numeric_dtype(clean[column]):
            clean[column] = clean[column].map(
                lambda value: "" if pd.isna(value) else f"{value:.{digits}f}"
            )
        else:
            clean[column] = clean[column].fillna("").astype(str)
    columns = [str(column) for column in clean.columns]
    rows = [
        "| " + " | ".join(columns) + " |",
        "|" + "|".join(["---"] + ["---:"] * (len(columns) - 1)) + "|",
    ]
    for record in clean.to_dict("records"):
        values = [str(record[column]).replace("|", "\\|") for column in clean.columns]
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join(rows)


def render(template: str, context: dict[str, object]) -> str:
    text = template
    for key, value in context.items():
        text = text.replace(f"[[{key}]]", str(value))
    unresolved = sorted(
        token.split("]]", 1)[0]
        for token in text.split("[[")[1:]
        if "]]" in token
    )
    if unresolved:
        raise RuntimeError(f"Unresolved manuscript placeholders: {unresolved}")
    return text


def main() -> None:
    summary = json.loads((RESULTS / "analysis_summary.json").read_text(encoding="utf-8"))
    audit = load("sample_audit")
    fundamentals = load("fundamental_market_structure")
    contracts = load("contract_descriptive_facts")
    adoption = load("reduced_form_adoption")
    trial = load("trial_learning_identification")
    plan = load("plan_versioning_fe")
    search = load("search_exposure_iv")
    external = load("external_diffusion_validation")
    price = load("price_identification")
    blp = load("blp_estimates")
    diagnostics = load("blp_diagnostics")
    supply = load("nonrival_supply_calibration")
    counterfactual = load("counterfactual_summary")
    conversion = load("counterfactual_conversion_path")
    market_size = load("counterfactual_market_size_path")

    f = summary["fundamentals"]
    a = summary["adoption"]
    t = summary["trial"]
    p = summary["plan"]
    s = summary["search"]
    pr = summary["price"]
    b = summary["blp"]
    cf = summary["counterfactual"]
    sp = summary["supply"]

    sample_table = audit.rename(
        columns={"Object": "对象", "Rows or count": "行数或数量", "API coverage": "API 覆盖"}
    )
    contract_table = contracts.rename(
        columns={"Contract fact": "合约事实", "Percent": "比例（%）", "Denominator": "分母"}
    )
    market_table = fundamentals.rename(
        columns={
            "Use-case market": "用途市场",
            "APIs": "API 数",
            "Owners": "卖家数",
            "Platform subscriptions": "平台订阅",
            "Free-plan share": "免费占比",
            "Positive-upgrade-price share": "有升级价占比",
            "Median upgrade price": "升级价中位数",
            "Product adoption HHI": "产品 HHI",
            "Owner adoption HHI": "卖家 HHI",
            "Top-four product share": "前四产品份额",
        }
    )
    adoption_table = adoption.rename(
        columns={
            "Variable": "变量",
            "Log adoption flow": "采用流量 OLS",
            "PPML with age exposure": "PPML",
            "Owner and market FE": "卖家与市场固定效应",
        }
    )
    trial_table = trial.rename(
        columns={
            "Learning proxy": "学习代理",
            "Log adoption flow": "采用流量 OLS",
            "PPML with age exposure": "PPML",
            "Owner and market FE": "卖家与市场固定效应",
        }
    )
    plan_table = plan.rename(
        columns={"Variable": "变量", "Within-API log monthly price": "API 内月费对数"}
    )
    price_table = price.rename(
        columns={
            "Instrument set": "工具变量组",
            "Price coefficient": "价格系数",
            "Clustered SE": "聚类标准误",
            "p-value": "p 值",
            "First-stage chi-square": "第一阶段卡方",
            "Partial R-squared": "偏 $R^2$",
            "Overidentification p-value": "过度识别 p 值",
        }
    )
    diagnostic_table = diagnostics.rename(
        columns={
            "Specification": "规格",
            "Products": "产品数",
            "Markets": "市场数",
            "Converged": "收敛",
            "Objective": "目标函数",
            "Price coefficient": "价格系数",
            "Random price SD": "随机价格系数标准差",
        }
    )
    selected_parameters = [
        "Minimum paid price / 100",
        "Free plan",
        "Calibrated Bayesian learning value",
        "Log free quota",
        "Log maximum paid quota",
        "Data scope",
        "Disclosure",
        "Reliability",
        "Log public plan count",
        "Versioning",
        "Named-developer restriction",
        "Open-data substitute score",
        "Best schema overlap",
        "Random-coefficient SD: prices",
    ]
    selected_blp = blp.loc[blp["Parameter"].isin(selected_parameters)].rename(
        columns={"Specification": "规格", "Parameter": "参数", "Estimate": "估计值", "SE": "标准误"}
    )
    counterfactual_table = counterfactual.rename(
        columns={
            "Counterfactual": "反事实",
            "Adoption change": "采用变化（%）",
            "Revenue/use change": "收入或使用变化（%）",
        }
    )

    public_reference = fundamentals.loc[
        fundamentals["Use-case market"].eq("public_reference")
    ].iloc[0]
    other_market = fundamentals.loc[fundamentals["Use-case market"].eq("other")].iloc[0]
    named_developer_share = contracts.loc[
        contracts["Contract fact"].eq("Public plans restricted to named developers"),
        "Percent",
    ].iloc[0]

    context: dict[str, object] = {
        "api_count": integer(f["api_count"]),
        "owner_count": integer(f["owner_count"]),
        "plan_count": integer(audit.loc[audit["Object"].eq("Plan contracts"), "Rows or count"].iloc[0]),
        "endpoint_count": integer(audit.loc[audit["Object"].eq("Endpoint schemas"), "Rows or count"].iloc[0]),
        "ranking_n": integer(s["ranking_n"]),
        "query_cells": integer(s["query_cells"]),
        "free_share": f"{100 * f['free_share']:.1f}",
        "positive_price_share": f"{100 * f['positive_price_share']:.1f}",
        "quota_10": f"{10 * p['quota_beta']:.2f}",
        "unit_discount_10": f"{10 * (1 - p['quota_beta']):.2f}",
        "contract_price": f"{pr['contract_price']:.3f}",
        "contract_price_se": f"{pr['contract_price_se']:.3f}",
        "contract_first_stage": f"{pr['contract_first_stage']:.2f}",
        "contract_partial_r2": f"{float(price.loc[price['Instrument set'].eq('Overage price + number of limits'), 'Partial R-squared'].iloc[0]):.3f}",
        "liml_price": f"{pr['liml_price']:.3f}",
        "overage_price": f"{pr['overage_price']:.3f}",
        "overage_price_se": f"{pr['overage_price_se']:.3f}",
        "ar_low": f"{pr['ar_low']:.1f}",
        "ar_high": f"{pr['ar_high']:.1f}",
        "blp_price": f"{b['preferred_price']:.3f}",
        "blp_price_se": f"{b['preferred_price_se']:.3f}",
        "blp_sigma": f"{b['price_sigma']:.3f}",
        "blp_sigma_se": f"{b['price_sigma_se']:.3f}",
        "classified_price": f"{b['classified_price']:.3f}",
        "classified_price_se": f"{b['classified_price_se']:.3f}",
        "paid_price": f"{b['paid_only_price']:.3f}",
        "paid_price_se": f"{b['paid_only_price_se']:.3f}",
        "full_products": integer(b["full_products"]),
        "classified_products": integer(b["classified_products"]),
        "paid_products": integer(b["paid_only_products"]),
        "price10_point": f"{cf['price10_adoption']:.2f}",
        "price10_low": f"{cf['price10_adoption_low']:.2f}",
        "price10_high": f"{cf['price10_adoption_high']:.2f}",
        "price10_revenue": f"{cf['price10_revenue']:.2f}",
        "copy_one": f"{cf['copy_one']:.2f}",
        "trial_remove": f"{cf['trial_remove']:.2f}",
        "disclosure_lift": f"{cf['disclosure_lift']:.2f}",
        "open_lift": f"{cf['open_lift']:.2f}",
        "governance_remove": f"{cf['governance_remove']:.2f}",
        "gateway_cost": f"{sp['gateway_cost_per_million']:.2f}",
        "supply_n": integer(sp["plan_n"]),
        "median_cost_share": f"{100 * sp['median_cost_share']:.3f}",
        "p90_cost_share": f"{100 * sp['p90_cost_share']:.3f}",
        "positive_margin_share": f"{100 * sp['positive_margin_share']:.2f}",
        "ols_free": f"{a['ols_free']:.3f}",
        "ppml_free": f"{a['ppml_free']:.3f}",
        "owner_free": f"{a['owner_fe_free']:.3f}",
        "owner_reliability": f"{a['owner_fe_reliability']:.3f}",
        "owner_reliability_pct": f"{100 * (math.exp(a['owner_fe_reliability']) - 1):.1f}",
        "trial_ols": f"{t['trial_learning_ols']:.3f}",
        "search_iv": f"{s['iv_exposure']:.3f}",
        "search_iv_se": f"{s['iv_exposure_se']:.3f}",
        "search_iv_fs": f"{s['iv_first_stage']:.2f}",
        "search_iv_r2": f"{s['iv_partial_r2']:.3f}",
        "external_beta": f"{summary['external']['subscription_beta']:.3f}",
        "external_se": f"{summary['external']['subscription_se']:.3f}",
        "ols_price": f"{pr['ols_price']:.3f}",
        "diff_price": f"{pr['diff_price']:.3f}",
        "owner_price": f"{pr['owner_price']:.3f}",
        "copy_price_min": f"{pr['copy_price_min']:.3f}",
        "copy_price_max": f"{pr['copy_price_max']:.3f}",
        "monotone": f"{100 * p['monotone']:.2f}",
        "violations": f"{100 * p['violations']:.2f}",
        "named_developer_share": f"{named_developer_share:.3f}",
        "public_reference_hhi": f"{public_reference['Product adoption HHI']:.3f}",
        "public_reference_top4": f"{100 * public_reference['Top-four product share']:.1f}",
        "other_hhi": f"{other_market['Product adoption HHI']:.3f}",
        "zero_conversion": f"{float(conversion.iloc[0, 1]):.1f}",
        "full_conversion": f"{float(conversion.iloc[-1, 1]):.1f}",
        "outside_min": f"{float(market_size.iloc[:, 1].min()):.2f}",
        "outside_max": f"{float(market_size.iloc[:, 1].max()):.2f}",
        "sample_table": markdown_table(sample_table, 0),
        "contract_table": markdown_table(contract_table, 2),
        "market_table": markdown_table(market_table, 3),
        "adoption_table": markdown_table(adoption_table, 3),
        "trial_table": markdown_table(trial_table, 3),
        "plan_table": markdown_table(plan_table, 3),
        "search_table": markdown_table(search.rename(columns={"Statistic": "统计量", "Value": "数值"}), 3),
        "external_table": markdown_table(
            external.rename(columns={"Variable": "变量", "Any public GitHub repository": "存在公开 GitHub 仓库"}),
            3,
        ),
        "price_table": markdown_table(price_table, 3),
        "diagnostic_table": markdown_table(diagnostic_table, 3),
        "blp_table": markdown_table(selected_blp, 3),
        "supply_table": markdown_table(
            supply.rename(columns={"Calibration statistic": "校准统计量", "Value": "数值"}),
            4,
        ),
        "counterfactual_table": markdown_table(counterfactual_table, 3),
    }

    template = r"""---
title: "数据访问合约、版本化与平台需求：来自 API 市场的产业组织证据"
lang: zh-CN
date: ""
---

# 摘要

数据可以被重复使用和近乎无限量复制；数据交易仍需要排他性的访问控制、用量计量和持续服务。本文研究一个大型 API 市场如何借助免费层、分级套餐、调用额度、超额费、速率限制、端点权限和搜索排序组织数据商品交易。数据覆盖 [[api_count]] 个 Data 类 API、[[plan_count]] 个计划版本、[[endpoint_count]] 个端点以及 [[ranking_n]] 条搜索展示。本文先用采用回归、卖家固定效应、计划固定效应、搜索排序工具变量和站外代码采用构造机制证据，再估计静态差异化产品需求。价格识别同时报告 OLS、差异化产品工具、卖家跨市场工具、合同计量工具、LIML、Anderson--Rubin 集和违反排除限制的敏感性。

结果显示，[[free_share]]% 的 API 设有免费层，同时 [[positive_price_share]]% 具有正的升级价格。计划内估计表明，额度提高 10% 与月费提高约 [[quota_10]]% 相联系，单位额度价格随套餐扩大而下降。可靠性和数据范围与采用稳定正相关；免费层与采用的正相关在 OLS 和卖家内比较中存在，但贝叶斯学习代理在不同估计量中的符号并不稳定，静态截面不能把免费层的作用归结为因果学习。合同计量工具得到 [[contract_price]] 的价格系数，弱工具稳健的 95% 接受集为 $[ [[ar_low]], [[ar_high]] ]$。随机系数 BLP 的平均价格反应为 [[blp_price]]，但标准误为 [[blp_price_se]]，价格异质性的精确识别有限。连续反事实显示，升级价格统一提高 10% 时，采用变化的 AR 区间为 [[price10_low]]% 至 [[price10_high]]%。复制校准表明，当复用强度取 1 时，下游使用可比平台账户数高 [[copy_one]]%。文章的主要贡献是把数据的非竞争供给、可排他访问、质量学习、版本化和可复制使用同时纳入一个可估计的静态产业组织框架，并明确区分点识别、集合识别和校准对象。

**关键词：** 数据商品；API 市场；版本化；差异化产品需求；平台排序；弱识别

**JEL 分类号：** L11，L15，D83，D86

# 引言

数据交易面临一个基本组织问题。同一数据对象能够被多个买家同时使用，卖给一个买家不会物理耗尽可供他人使用的副本。非竞争性扩大了数据共享的潜在收益，也削弱了传统单位商品与单位销量之间的一一对应。市场若要对数据收费，需要在技术上创造排他性：认证账户、限制调用、划定端点、设置速率、记录超额使用，并通过合同约束转售与复制。数据 API 正是这种制度安排。买家购买持续访问权，卖家保留底层数据和更新过程，平台负责发现、鉴权、计量和部分治理。

这一市场同时包含三类产业组织问题。第一，数据质量、覆盖和任务匹配在购买前难以完全观察。免费额度和文档可以产生信号，也可以直接满足低强度需求，二者对付费转化的作用不同。第二，卖家面对异质的使用强度与支付意愿，通过 BASIC、PRO、ULTRA、MEGA 等菜单对买家进行筛选。套餐把月费、额度、超额费、速率和端点权限组合起来，价格并非一个独立于合约的标量。第三，平台排序决定产品是否进入考虑集。累计订阅又可能影响排序，位置和质量由此共同决定观察到的采用。

本文构建 Data 类 API 的静态产品研究宇宙。产品层有 [[api_count]] 个 API 和 [[owner_count]] 个卖家；计划、端点和搜索展示分别构成合约层、技术层和考虑集层。外部数据补充公开 GitHub 采用、开放数据替代、竞争平台候选、卖家域名与国家、数字监管和云服务成本。多层数据使需求、菜单、排序和非竞争供给能够在同一框架内讨论。

实证分析按识别强度分层。采用回归用于建立产品属性与采用之间的条件相关；同一卖家多产品比较吸收卖家能力与品牌；同一 API 多计划比较吸收底层数据质量；字母排序曝光为相关性排序提供平台考虑集的辅助工具。价格需求同时使用多个工具组并检验其失败方式。差异化产品工具和卖家跨市场价格工具在本样本中给出反常正价格系数，提示质量与卖家能力仍进入排除项。合同计量工具给出负系数和可用第一阶段，但超额费与限制数量可能直接影响效用，因此本文把其视为条件工具，使用 Anderson--Rubin 集与局部违反排除限制的路径刻画不确定性。

本文得到四组结果。其一，免费入口和正升级价高度共存，数据市场的零价格主要位于进入层，收入来自后续访问版本。其二，额度与价格之间呈显著次线性关系，符合低复制成本下的数量折扣和版本化筛选。其三，可靠性是最稳定的采用关联变量；免费层的平均关联明显，但“免费试用提供学习”这一更窄机制没有得到跨估计量一致支持。其四，价格反应的均值可以被约束在负区间内，随机系数分散程度仍很不精确。反事实因而以连续路径和识别区间呈现。

本文的理论贡献落在“数据对象”和“数据访问服务”的分离。底层数据复制近乎非竞争，API 网关、实时计算、清洗、监控和合规则具有正的服务成本。合约通过额度与访问规则在人为稀缺和真实服务成本之间划界。进一步地，平台订阅账户只观察到合约入口；买家把结果复制给同事、客户或模型管线后，下游使用量可能显著高于账户数。把这一数量楔子加入需求与福利映射后，数据商品的采用、收入和社会使用不再由同一个 $q$ 表示。

# 文献

## 差异化需求

本文的需求侧建立在 Berry（1994）、Berry、Levinsohn and Pakes（1995）以及 Nevo（2001）的市场份额反演框架上。BLP 把产品特征、价格和未观察质量共同放入均值效用，并用工具变量处理价格与未观察质量的相关。Nevo 展示了如何把需求、所有权和边际成本连接为产业均衡。本文沿用需求反演和随机系数扩展，但没有计划层选择人数，无法在卖家多套餐层面恢复传统加价方程。供给证据来自 API 内价格菜单和公开服务成本校准。

价格工具的表现直接吸收 Gandhi and Houde（2019）与 Reynaert and Verboven（2014）的识别教训。差异化工具需要由外生产品位置驱动替代模式；最优工具也不能修复原始成本排除变量的失效。本文报告每组工具对应的价格系数、第一阶段和过度识别，并把弱工具稳健推断置于点估计之前。

## 平台与信息商品

Rochet and Tirole（2003，2006）、Armstrong（2006）以及 Parker and Van Alstyne（2005）说明平台通过价格结构和跨边网络效应协调不同用户群。Hagiu and Wright（2015）强调多边平台与垂直组织的边界。RapidAPI 同时提供搜索、认证、计量和支付，具备平台组织的关键功能；平台费率和买方微观行为未公开，本文集中估计平台排序对考虑集的作用。Dinerstein et al.（2018）和 Ursu（2018）表明排序位置与产品相关性内生，可信识别通常依赖实验或制度变化。本文的字母排序只提供辅助准实验，解释力度相应收敛。

信息商品研究解释了为什么低复制成本会伴随版本化和捆绑。Varian（1997）、Shapiro and Varian（1999）、Bakos and Brynjolfsson（1999）以及 Sundararajan（2004）讨论了质量降级、套餐和非线性价格。Bergemann、Bonatti and Smolin（2018）进一步把信息内容本身作为可设计、可定价的对象。API 套餐的质量维度表现为可调用数量、调用强度、可用端点、服务保障与访问资格，适合检验信息商品版本化在数据访问市场中的具体形式。

## 数据经济学

Jones and Tonetti（2020）把非竞争性置于数据配置的中心：广泛使用同一数据可能产生规模收益，排他产权也可能造成使用不足。Acemoglu et al.（2022）和 Bergemann、Bonatti and Gan（2022）说明，一个人的数据能够透露其他人的信息，市场价格未必内生化隐私和信息外部性。Farboodi and Veldkamp（2026）把数据视为可积累、可交易并持续产生价值的资产。本文研究的交易对象更接近企业、地理、价格、社交和公开记录的数据访问，无法识别个人隐私损失；这些文献仍说明数据价格与社会价值之间不能机械等同。

数据市场的实证研究多集中于分类、商业模式与挂牌价格。Stahl et al.（2016）建立数据市场分类，Azcoitia、Laoutaris and Lutu（2022）测量商业数据市场的价格差异。本文向前推进两步：把 API 的产品、套餐、端点和排序连接为静态需求系统；把复制使用和非竞争供给显式写进数量与供给映射。数据商品特征由此进入模型的约束、识别与反事实。

## 贡献

第一，本文提供一个访问权产业组织框架。数据的非竞争性位于供给技术，API 的排他性位于合约技术，二者共同决定免费层、版本化和计量。第二，模型将试用的学习价值、直接消费价值和复制风险同时纳入免费额度选择，允许免费层对付费需求产生方向不定的总效应。第三，本文区分平台采用 $q^P$ 与下游使用 $q^D$，使复制成为可审计的数量楔子。第四，识别结果本身构成贡献：常用 BLP 和卖家工具在该市场产生反常符号，合同工具提供负价格反应但排除限制仍需敏感性分析，随机系数异质性在静态聚合数据中较弱。

# 市场与数据

## 平台机制

每个 API 对应一个底层数据产品与接口集合。卖家可以发布多个计划，计划包含月费、调用额度、超额单价、速率限制、可访问端点、审批和指定开发者。买家先通过目录或搜索看到 API，再查阅文档和质量指标，选择免费或付费计划。平台统一密钥和计量使数据访问可排他，也让卖家可以在不转移底层数据所有权的情况下重复销售。

平台公开的累计订阅数是账户采用存量。本文用 API 年龄构造平均采用流量，降低老产品机械积累更多订阅的影响。该变量仍不等于调用量、付费计划销量或最终使用人数。搜索表包含 [[ranking_n]] 条已执行查询结果和 [[query_cells]] 个查询单元，是设计型曝光面板，不代表所有可能关键词。

## 样本

[[sample_table]]

产品主表对本轮发现的 [[api_count]] 个唯一 API 全覆盖。计划和详情字段覆盖 7,958 个当前可访问详情的 API；4 个目录记录在 slug 与 API ID 查询下都返回 `NOT_FOUND`，在产品主表中保留为下架状态。端点表覆盖 7,881 个 API。外部面板为每个 API 保留一行，具体字段按来源实际覆盖。

## 合约事实

[[contract_table]]

免费层覆盖率为 [[free_share]]%，正升级价格覆盖率为 [[positive_price_share]]%，两者共存说明进入价格与升级价格是不同对象。39.2% 的 API 使用超额合约，72.4% 设有速率限制。指定开发者计划极少，其回归系数容易受到定制客户选择影响。

## 市场结构

[[market_table]]

用途市场的集中度差异很大。`public_reference` 的产品采用 HHI 为 [[public_reference_hhi]]，前四产品占 [[public_reference_top4]]%；`other` 市场的 HHI 仅为 [[other_hhi]]。市场定义会影响替代集合和随机系数识别，结构估计同时报告含 `other` 的十市场样本与排除该残余类别的九市场样本。

![用途市场规模与集中度](../figures/fundamental_market_structure.png)

# 模型

## 环境

市场 $m$ 中有数据 API $j=1,\ldots,J_m$，API 由卖家 $f(j)$ 提供。底层数据对象 $D_j$ 的生产、采集和清洗需要固定成本 $F_j$。数据副本可以同时服务多个买家，复制的技术边际成本取零；每次 API 交付仍产生网关、计算、监控和治理成本 $c_j^s(h)$，其中 $h$ 为调用强度。卖家通过认证和合约使访问可排他。

API $j$ 提供计划集合 $K_j$。计划 $k$ 写为

$$
T_{jk}=\left(p_{jk},Q_{jk},o_{jk},\ell_{jk},E_{jk},A_{jk}\right),
$$

其中 $p$ 是月费，$Q$ 是包含额度，$o$ 是超额单价，$\ell$ 是速率约束，$E$ 是端点集合，$A$ 是审批或指定开发者限制。买方 $i$ 的类型包含使用强度 $h_i$、支付敏感度 $\alpha_i$、任务匹配 $\theta_{ij}$ 与治理成本 $\chi_i$。

## 试用

购买前，买方对任务匹配只有先验：

$$
\theta_{ij}\sim N(\mu_{ij},\tau_{ij}^2).
$$

免费额度 $Q_{j0}$ 产生信号 $y_{ij}=\theta_{ij}+\varepsilon_{ij}$，噪声方差 $\sigma_j^2(Q_{j0},d_j,r_j)$ 随额度、披露 $d_j$ 和可靠性 $r_j$ 改变。正态更新给出

$$
E(\theta_{ij}\mid y_{ij})=
\frac{\sigma_j^{-2}\mu_{ij}+\tau_{ij}^{-2}y_{ij}}
{\sigma_j^{-2}+\tau_{ij}^{-2}},\qquad
\operatorname{Var}(\theta_{ij}\mid y_{ij})=
\left(\tau_{ij}^{-2}+\sigma_j^{-2}\right)^{-1}.
$$

试用的选择价值记为 $\Omega_{ij}$，等于获得信号后最优购买价值与先验下价值之差。免费层还提供直接消费 $C_i(Q_{j0})$。低强度买方可能长期停留在免费层；较大的免费额度也提高服务成本和结果外传机会。卖家的免费额度条件可写成

$$
\gamma\frac{\partial \Omega_j}{\partial Q_{j0}}
+\frac{\partial M_j}{\partial Q_{j0}}
=c_j^{s\prime}(Q_{j0})+
\frac{\partial L_j}{\partial Q_{j0}}+
\frac{\partial H_j}{\partial Q_{j0}},
$$

其中 $M$ 是获客与转化收益，$L$ 是付费蚕食，$H$ 是复制或合规风险。边际信号价值递减而右侧成本上升时，最优免费额度位于内部。静态采用无法分别识别这些项，免费与不确定性的交互只检验与学习机制一致的异质相关。

## 买方选择

给定 API，买方在计划间选择：

$$
v_{ijk}=x_j'\beta+\theta_{ij}+g(h_i,Q_{jk},o_{jk},\ell_{jk},E_{jk})
-\alpha_i p_{jk}-\chi_i A_{jk}+\gamma\Omega_{ij}+\epsilon_{ijk}.
$$

API 的间接效用为 $V_{ij}=\max_{k\in K_j}v_{ijk}$。额度和端点扩大使用集合，速率和审批限制会降低部分买方效用；对高合规需求买方，访问限制也可能是质量承诺，因此其平均符号取决于选择进入。

## 需求

结构估计在产品层使用升级价格。存在免费层时，零价格是进入条件，最低正月费是升级价格。将二者混成单一价格会把免费设计内生性装入价格系数。均值效用写为

$$
\delta_{jm}=x_j'\beta-\bar\alpha p_j+\xi_j,
$$

其中 $x_j$ 包括免费层、免费和付费额度、数据范围、接入复杂度、披露、可靠性、计划数、版本化、访问限制、开放替代、schema 重复度和年龄。价格随机系数为

$$
\alpha_i=\bar\alpha+\sigma_\alpha\nu_i,\qquad \nu_i\sim N(0,1).
$$

产品份额满足

$$
s_{jm}(\theta)=\int
\frac{\exp(\delta_{jm}-\sigma_\alpha\nu_i p_j)}
{1+\sum_{r\in m}\exp(\delta_{rm}-\sigma_\alpha\nu_i p_r)}d\Phi(\nu_i).
$$

市场份额由年龄调整采用流量构造，基准假定各用途市场合计内部份额为 20%，并在 5% 至 50% 之间连续变化。未观察的付费升级选择意味着该模型识别“API 采用对所见升级合约的条件反应”，不能解释为付费计划份额系统。

## 供给

若计划选择人数 $N_{jk}$ 和调用量 $h_{ijk}$ 可见，卖家利润为

$$
\Pi_f=\sum_{j\in f}\sum_{k\in K_j}
\left[N_{jk}p_{jk}+\sum_i o_{jk}(h_{ijk}-Q_{jk})_+
-\sum_i c_j^s(h_{ijk})-G_j(A_{jk})\right]-\sum_{j\in f}F_j.
$$

非竞争性体现在同一 $D_j$ 可进入所有买家的生产过程，服务成本和治理成本仍随调用增长。平台未公开 $N_{jk}$，用 API 总订阅代替会把免费用户误计为付费销量。本文不做计划层加价反演，改用同一 API 内菜单回归和云网关成本路径识别供给约束。

## 排序与复制

买方只在考虑集 $\mathcal C_i$ 中选择。产品进入考虑集的概率受相关性排序 $r_{jq}$、字母排序和查询固定效应影响。相关性排序可能使用累计采用和未观察质量，位置效应需要工具或实验变化。

平台观察账户采用 $q_j^P$。若账户把结果嵌入内部系统或传递给第三方，下游使用为

$$
q_j^D=q_j^P\left[1+\kappa\left(0.2+0.8R_j\right)\right],
$$

其中 $R_j$ 是范围、schema 可复制性和站外代码扩散的排序，$\kappa\geq0$ 是复用强度。该式不把复制量当作已观测事实，只定义从账户采用到总使用的可审计校准。

## 推论

模型给出四个经验推论。其一，若高用量类型具有更高支付意愿，菜单月费随额度增加，但价格弹性可低于 1，表现为单位额度折扣。其二，免费层对采用的总效应包含学习、直接消费、蚕食和复制风险，平均符号与因果学习效应不能互换。其三，排序改变考虑集时，外生位置改善会提高采用。其四，平台账户数低估下游使用时，价格弹性可以相对稳定，总使用和福利水平却随 $\kappa$ 显著变化。

# 识别

## 采用

基准 OLS 使用对数年龄调整采用流量，控制市场固定效应、价格、额度、范围、复杂度、披露、可靠性、版本化、访问限制、开放替代、schema 相似和卖家规模。PPML 将累计订阅作为计数结果并把产品年龄作为 exposure。卖家与市场固定效应规格只使用至少两个产品的卖家，比较同一卖家内部的产品差异。三者的误差结构与样本不同，跨规格稳定性比单一显著性更重要。

## 菜单

计划回归使用同一 API 的公开正价计划，因变量为月费对数，解释变量为额度、超额费、审批、推荐、端点限制、速率限制和指定开发者。API 固定效应吸收底层数据、卖家、文档和总体声誉。该回归识别卖家如何给同一数据对象的访问版本定价。

## 排序

搜索机制先检验相关性名次和展示频率的关系。采用方程以相关性排序曝光为内生变量，以同一查询下的字母排序曝光为工具，并控制查询结构与产品特征。排除限制要求字母顺序只通过位置影响考虑集。产品命名可能与专业化或品牌相关，因此结果被解释为辅助证据。

## 价格

价格与未观察质量相关。本文比较四类工具：BLP 产品特征和局部差异工具、卖家其他用途市场的价格、套餐超额费以及限制数量。前两类分别可能受内生定位和卖家能力污染。合同工具利用同一 API 菜单的计量结构，但超额价格与限制数量可能直接影响买方预期支付和灵活性。矩条件为

$$
E[Z_j\xi_j]=0.
$$

本文报告 2SLS、LIML、第一阶段、过度识别、工具平衡、逐市场剔除、Anderson--Rubin 集和 Conley 型直接效用路径。过度识别不拒绝不能证明排除限制成立。

# Reduced Form

## 采用结果

[[adoption_table]]

免费层在 OLS 中的系数为 [[ols_free]]，在卖家固定效应中为 [[owner_free]]，PPML 为 [[ppml_free]] 且标准误较大。免费产品的累计采用明显更高；这种差异既可能来自试用，也可能来自零价直接消费、卖家选择免费策略以及平台排序反馈。卖家内系数更大说明卖家固定能力无法解释全部相关，仍无法排除同一卖家把免费层配置给更有增长潜力的产品。

可靠性的系数在三种估计量中均为正，卖家内估计为 [[owner_reliability]]。若指数按一个标准差变化，其条件采用差异约为 [[owner_reliability_pct]]%。数据范围在 PPML 和卖家内估计中为正；复杂度在跨产品 OLS 中为负，卖家内精度不足。规格曲线显示，可靠性的正号对采用流量、存量、正价格样本和头部剔除较稳定，范围对存量规格不稳定。

![采用规格曲线](../figures/adoption_specification_curve.png)

逐用途市场剔除后，免费、范围和可靠性的主要方向保持；该检验降低单一大市场驱动结果的担忧。

![采用回归逐市场剔除](../figures/reduced_form_leave_one_market_out.png)

## 试用结果

[[trial_table]]

免费与事前不确定性的交互在 OLS 中为 [[trial_ols]]，PPML 和卖家内估计均不精确。信号精度代理在 OLS 和卖家内规格中为负；贝叶斯方差缩减在卖家内规格中也为负。三组结果没有形成“免费额度通过更精确信号提高采用”的一致排序。静态数据支持免费层与采用相关，尚不足以确定学习、直接消费和选择进入各占多少。后文的试用反事实因此是条件效用分解。

## 版本化结果

[[plan_table]]

额度系数意味着额度提高 10% 与月费提高约 [[quota_10]]% 相联系，单位额度价格约下降 [[unit_discount_10]]%。[[monotone]]% 的相邻菜单满足价格单调，严格违反价格随额度上升的比较只占 [[violations]]%。次线性价格斜率与低复制成本下的数量折扣一致，也可能反映高档计划在端点、支持或质量上存在未完全观察的差异。

正超额费与较低月费相联系，表明卖家在固定费和边际使用费之间替代。审批计划的月费更高，符合合规筛选或定制服务。指定开发者计划的系数为负，但该类计划只占公开计划的 [[named_developer_share]]%，不能据此推断一般治理折价。

## 排序与站外采用

相关性排序中，靠前位置与更高采用同现。以字母排序曝光为工具，相关性曝光的采用系数为 [[search_iv]]，标准误 [[search_iv_se]]，p 值接近 0.062；第一阶段卡方为 [[search_iv_fs]]，偏 $R^2$ 为 [[search_iv_r2]]。证据表明考虑集分配具有经济重要性，排除限制仍依赖产品命名与需求在控制变量后不相关。

[[search_table]]

公开 GitHub 仓库匹配只有 80 个正结果，是站外采用的下界。控制市场后，平台订阅对任一公开仓库匹配的系数为 [[external_beta]]，标准误 [[external_se]]；范围、披露、可靠性和 schema 重叠也呈正相关。平台订阅因此具有外部技术采用含义，同时不能替代真实调用和内部私有代码。

[[external_table]]

# 结构估计

## 价格识别

[[price_table]]

OLS 价格系数为 [[ols_price]]，接近零。差异化工具得到 [[diff_price]]，卖家跨市场工具得到 [[owner_price]]，二者的正号与需求向下倾斜不一致。前者第一阶段偏 $R^2$ 很低，后者受到卖家质量与品牌能力的污染。这些结果说明，扩大工具数量会增强第一阶段，却不会自动改善排除限制。

超额费单独作为工具得到 [[overage_price]]（[[overage_price_se]]）；与限制数量合用得到 [[contract_price]]（[[contract_price_se]]），LIML 为 [[liml_price]]。第一阶段卡方为 [[contract_first_stage]]，偏 $R^2$ 为 [[contract_partial_r2]]。逐一剔除用途市场后系数始终为负；复制强度从 0 到 4 时，价格系数只从 [[copy_price_min]] 变到 [[copy_price_max]]。

Anderson--Rubin 95% 接受集为 $[ [[ar_low]], [[ar_high]] ]$，没有触及预设网格边界。集合排除了零价格反应，也保留了相当宽的经济幅度。

![价格系数的 Anderson--Rubin 接受集](../figures/anderson_rubin_price_identification.png)

免费产品的实际入口价格为零，升级价格对未来付费的重要性未知。将免费 API 的升级价格权重从 0 连续提高到 1，价格系数从高度不精确的 -14.32 移动到 -3.11，第一阶段同步增强。价格定义本身是结构解释的一部分。

![免费入口与升级价格定义敏感性](../figures/price_definition_sensitivity.png)

Conley 路径允许超额费工具对效用存在直接影响。直接效用效应略低于 -0.10 时，价格系数接近零并改变符号；这一临界值不大。合同工具的负价格反应应理解为条件识别，AR 区间没有覆盖排除限制错误的不确定性。

![合同工具排除限制敏感性](../figures/price_plausibly_exogenous_path.png)

## BLP 结果

[[diagnostic_table]]

[[blp_table]]

基准 BLP 使用 [[full_products]] 个观察到正升级价格的产品。平均价格系数为 [[blp_price]]，标准误 [[blp_price_se]]；随机价格系数标准差为 [[blp_sigma]]，标准误 [[blp_sigma_se]]。均值位于 AR 接受集内，随机系数的精度很低。排除残余用途市场后，价格系数为 [[classified_price]]（[[classified_price_se]]），随机标准差接近零。[[paid_products]] 个无免费入口产品的同质 IV logit 给出 [[paid_price]]（[[paid_price_se]]）。

这些差异揭示了静态数据的识别边界。聚合截面能约束平均价格反应的负区间，难以单独识别价格偏好的分散程度。无免费入口样本较小，价格系数不精确；全市场样本更多，升级价格与免费入口的未来转化映射需要校准。后续反事实把 AR 区间作为主要不确定性带，BLP 均值只提供中心路径。

可靠性、最大付费额度和计划数量在 BLP 中为正。开放替代得分与 schema 重叠为负，方向符合可替代性增强降低单个商业 API 的相对效用。版本化系数为负，可能反映菜单复杂度、成熟产品选择更复杂菜单，或未观察的买方筛选；计划层结果确认价格随版本提升，产品层版本数量的系数不宜解释为菜单本身降低福利。

# 供给

[[supply_table]]

公开云网关基准为每百万次请求 [[gateway_cost]] 美元。在 [[supply_n]] 个具有有限正额度的公开付费计划中，网关成本占月费的中位数为 [[median_cost_share]]%，P90 为 [[p90_cost_share]]%；[[positive_margin_share]]% 的计划月费高于这一狭义成本。该比例支持数据复制成本很低的供给特征，同时没有计入数据采购、清洗、实时计算、支持和法律风险。

![非竞争数据的服务成本路径](../figures/nonrival_supply_cost_path.png)

供给分析的经济含义在于分开三类成本。底层数据创建成本主要是固定成本；复制数据对象的技术边际成本接近零；交付和治理成本随请求增长。套餐额度既筛选买方，也限制服务负载和外传。没有计划层销量时，无法区分信息租、服务加价和固定成本回收，云成本路径只给出狭义服务成本下界。

# 反事实

[[counterfactual_table]]

## 价格

升级价格提高 10% 时，BLP 中心路径的采用变化为 [[price10_point]]%，AR 识别区间对应 [[price10_low]]% 至 [[price10_high]]%。在免费用户转付费率取 25% 的校准下，收入代理变化为 [[price10_revenue]]%。中心路径位于需求弹性较高的区域，因此统一涨价降低收入；这一结论随价格系数和转化率改变。

![升级价格连续反事实](../figures/counterfactual_price_path.png)

市场总内部份额从 5% 提高到 50% 时，10% 涨价造成的采用变化位于 [[outside_min]]% 与 [[outside_max]]% 之间。外部选择规模影响反事实幅度，没有改变涨价降低采用的方向。

## 试用、披露与替代

把免费入口和贝叶斯学习项同时缩放至零，采用在条件结构模型中变化 [[trial_remove]]%。该幅度混合了免费直接消费、选择进入和学习，reduced form 没有支持将其解释为纯学习效应。低披露四分位产品的披露提高一个标准差，采用变化为 [[disclosure_lift]]%；开放替代显著度提高一个标准差，商业 API 采用变化为 [[open_lift]]%。开放替代匹配依赖文本与目录覆盖，幅度更适合作为替代压力的量级检验。

![试用、披露、开放替代与复制路径](../figures/counterfactual_mechanism_paths.png)

## 复制与治理

当 $\kappa=1$ 时，下游使用比平台订阅高 [[copy_one]]%；$\kappa$ 从 0 到 4 的整条路径展示账户采用对总使用的低估。复制不会机械改变平台观察到的合同数，却会放大数据的社会使用和潜在外部性。价格 IV 在同一复制路径上的变化很小，说明相对份额价格系数对这一特定测量修正较稳健；使用水平与福利仍高度依赖 $\kappa$。

移除条件访问治理效用时，采用变化为 [[governance_remove]]%。这一大幅结果主要来自少量指定开发者或限制计划的正效用系数，反映定制客户选择和未观察质量的可能性很高。它是压力测试，不构成取消治理的政策预测。

免费转付费率从 0 调到 1 时，相对 25% 基准的收入变化从 [[zero_conversion]]% 到 [[full_conversion]]%。平台未公开这一转化率，收入水平和最优价格不能脱离连续转化路径报告。

![转付费、治理、市场规模与服务成本路径](../figures/counterfactual_monetization_paths.png)

# 讨论

本文最强的证据来自菜单版本化。计划固定效应把比较限制在同一底层数据对象，额度与价格的次线性关系、超额费与固定费的替代以及高单调率共同说明卖家在设计访问边界。该结果直接对应数据商品的非竞争性：卖家不需要为每份数据重新生产，却需要控制调用规模并筛选用途。

采用侧最稳定的结果是可靠性。数据价值依赖更新、成功调用和任务匹配，服务失败会破坏数据投入的下游流程。范围也与采用正相关，但存量规格中的符号变化表明，广覆盖产品可能更年轻或面对更拥挤的用途市场。免费层的强平均相关不能替代对学习机制的识别；贝叶斯代理的结果要求未来使用升级、留存或逐期调用数据。

价格识别展示了结构研究中值得保留的失败。标准差异化工具和卖家跨市场工具在该平台上给出正价格系数，说明产品定位、卖家能力和价格共同决定需求。合同工具提供负反应和非弱的联合第一阶段，排除限制又容易被预期超额支付直接破坏。AR 集、Conley 路径和市场规模路径共同界定了可信结论：平均需求向下倾斜具有支持，精确随机异质性和点福利仍弱。

数据商品的数量问题也改变结果解释。平台订阅只记录账户，无法观察企业内部复用、向客户交付的衍生输出以及复制给其他主体的结果。复制强度不影响非竞争数据能否继续供给，却影响平台份额与社会使用之间的比例。缺少买方和调用微观数据时，福利应以路径呈现，避免把账户数当作最终消费单位。

本文仍有四项限制。第一，静态截面不能处理免费层推出、价格调整和采用反馈的时间顺序。第二，计划层销量不可见，无法估计套餐选择和卖家一阶条件。第三，搜索工具缺少平台随机实验，字母排序可能通过命名策略与需求相联系。第四，开放替代、竞争平台与 GitHub 只覆盖公开来源，空匹配是缺失和不存在的混合。上述边界决定本文可以讨论访问合同的组织与条件需求，不能给出完整平台最优费率或隐私外部性的福利总量。

# 买方微观验证

# 动态升级与留存

# 结论

API 市场把非竞争的数据对象转化为可排他的访问合约。免费层降低进入门槛，套餐用额度和超额费筛选使用强度，访问限制处理服务与治理风险，平台排序决定考虑集。全量静态数据表明，免费入口与正升级价普遍共存，可靠性与采用稳定相关，同一 API 内月费对额度的弹性显著低于 1。价格需求的均值可以被约束在负区间内，随机系数分散程度和免费转付费仍缺乏精确识别。

从产业组织角度看，数据商品的关键不只在于低复制成本。市场必须通过技术和合同创造排他性，又无法完全阻止买方内部复用和结果外传。供给可以近乎无限，观察到的账户数量却只是总使用的下界。把非竞争供给、合约计量和复制楔子同时纳入模型，能够解释为什么数据市场大量使用免费入口、版本化和使用限制，也说明价格、采用和福利需要以不同数量口径衡量。

# 参考文献

Acemoglu, D., A. Makhdoumi, A. Malekian, and A. Ozdaglar. 2022. “Too Much Data: Prices and Inefficiencies in Data Markets.” *American Economic Journal: Microeconomics* 14(4): 218--256.

Armstrong, M. 2006. “Competition in Two-Sided Markets.” *RAND Journal of Economics* 37(3): 668--691.

Azcoitia, S. A., N. Laoutaris, and A. Lutu. 2022. “Measuring the Price of Data in Commercial Data Marketplaces.” In *Data Economy Workshop*.

Bakos, Y., and E. Brynjolfsson. 1999. “Bundling Information Goods: Pricing, Profits, and Efficiency.” *Management Science* 45(12): 1613--1630.

Bergemann, D., A. Bonatti, and T. Gan. 2022. “The Economics of Social Data.” *RAND Journal of Economics* 53(2): 263--296.

Bergemann, D., A. Bonatti, and A. Smolin. 2018. “The Design and Price of Information.” *American Economic Review* 108(1): 1--48.

Berry, S. 1994. “Estimating Discrete-Choice Models of Product Differentiation.” *RAND Journal of Economics* 25(2): 242--262.

Berry, S., J. Levinsohn, and A. Pakes. 1995. “Automobile Prices in Market Equilibrium.” *Econometrica* 63(4): 841--890.

Dinerstein, M., L. Einav, J. Levin, and N. Sundaresan. 2018. “Consumer Price Search and Platform Design in Internet Commerce.” *American Economic Review* 108(7): 1820--1859.

Farboodi, M., and L. Veldkamp. 2026. “A Model of the Data Economy.” *Review of Economic Studies*.

Gandhi, A., and J.-F. Houde. 2019. “Measuring Substitution Patterns in Differentiated-Products Industries.” NBER Working Paper 26375.

Goldfarb, A., and C. Tucker. 2019. “Digital Economics.” *Journal of Economic Literature* 57(1): 3--43.

Hagiu, A., and J. Wright. 2015. “Multi-Sided Platforms.” *International Journal of Industrial Organization* 43: 162--174.

Jones, C. I., and C. Tonetti. 2020. “Nonrivalry and the Economics of Data.” *American Economic Review* 110(9): 2819--2858.

Nevo, A. 2001. “Measuring Market Power in the Ready-to-Eat Cereal Industry.” *Econometrica* 69(2): 307--342.

Parker, G. G., and M. W. Van Alstyne. 2005. “Two-Sided Network Effects: A Theory of Information Product Design.” *Management Science* 51(10): 1494--1504.

Reynaert, M., and F. Verboven. 2014. “Improving the Performance of Random Coefficients Demand Models: The Role of Optimal Instruments.” *Journal of Econometrics* 179(1): 83--98.

Rochet, J.-C., and J. Tirole. 2003. “Platform Competition in Two-Sided Markets.” *Journal of the European Economic Association* 1(4): 990--1029.

Rochet, J.-C., and J. Tirole. 2006. “Two-Sided Markets: A Progress Report.” *RAND Journal of Economics* 37(3): 645--667.

Shapiro, C., and H. R. Varian. 1999. *Information Rules: A Strategic Guide to the Network Economy*. Boston: Harvard Business School Press.

Stahl, F., F. Schomm, G. Vossen, and L. Vomfell. 2016. “A Classification Framework for Data Marketplaces.” *Vietnam Journal of Computer Science* 3: 137--143.

Sundararajan, A. 2004. “Nonlinear Pricing of Information Goods.” *Management Science* 50(12): 1660--1673.

Ursu, R. M. 2018. “The Power of Rankings: Quantifying the Effect of Rankings on Online Consumer Search and Purchase Decisions.” *Marketing Science* 37(4): 530--552.

Varian, H. R. 1997. “Versioning Information Goods.” In *University of Michigan Conference on Internet Publishing and Beyond*.
"""

    REPORT.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(template, context), encoding="utf-8")
    print(f"Manuscript: {OUTPUT}")


if __name__ == "__main__":
    main()

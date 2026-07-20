#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pandas as pd

from build_raw_variable_dictionary import empirical_use, infer_meaning, variable_role


ROOT = Path(__file__).resolve().parents[2]
CRAWL = ROOT / "rapidapi_crawl"
MERGED = CRAWL / "data_merged"
EXTERNAL = CRAWL / "data_external"


TABLE_META = {
    "rapidapi_merged_api_master.csv": (
        "API 产品",
        "api_id",
        "RapidAPI Data 类静态产品全集；每个 API 一行。",
        "主需求、产品特征、卖家、菜单摘要与结构估计变量；外部字段按 api_id 连接外部 API 面板。",
    ),
    "rapidapi_merged_plan_contracts.csv": (
        "API-计划-版本",
        "api_id + plan_id + version_id",
        "Data 类 API 的公开、隐藏和限制计划；每个计划版本一行。",
        "价格、额度、超额费、速率限制、端点限制与开发者权限。",
    ),
    "rapidapi_merged_endpoint_schema.csv": (
        "API-端点",
        "api_id + endpoint_id",
        "已公开端点的技术结构；每个 endpoint 一行。",
        "数据范围、接入复杂度、参数、payload 与 schema 替代关系。",
    ),
    "rapidapi_merged_search_exposure.csv": (
        "查询-排序-API",
        "query_id + replica_index + search_rank + api_id",
        "已执行搜索单元的完整结果页快照；每次展示一行。",
        "平台考虑集、排序、曝光与搜索工具变量。",
    ),
    "rapidapi_merged_marketplace_listings.csv": (
        "入口列表-API",
        "listing_source + rank/page + api_id",
        "发现页与一般搜索入口的去重挂牌记录。",
        "覆盖核验和入口列表稳健性，不替代搜索曝光表。",
    ),
    "rapidapi_external_enriched_panel.csv": (
        "API 产品",
        "api_id",
        "与主表一一对应的外部静态补充面板。",
        "GitHub 采用、开放替代、竞争平台、owner 地域、监管与宏观变量。",
    ),
    "schema_overlap_pairs.csv": (
        "API 产品对",
        "api_id_left + api_id_right",
        "同用途市场内构造的候选产品对。",
        "技术替代性、复制性与局部竞争集合。",
    ),
    "external_code_repositories.csv": (
        "API-公开代码仓库",
        "api_id + repository identifier",
        "可核验的公开代码引用匹配；不覆盖私有仓库。",
        "平台外采用验证和公开复用下界。",
    ),
    "external_open_substitutes.csv": (
        "API 产品",
        "api_id",
        "每个 API 的开放数据匹配汇总；每个 API 一行。",
        "零价替代品得分和候选覆盖，已并入外部 API 面板。",
    ),
    "open_data_candidates.csv": (
        "API-开放数据候选",
        "api_id + open_source + candidate_key",
        "API 与开放数据目录候选的长表。",
        "复核零价替代品匹配与语义接近度。",
    ),
    "competitor_matches.csv": (
        "RapidAPI 产品-竞争平台候选",
        "api_id + competitor candidate",
        "跨平台候选匹配；当前高质量覆盖较低。",
        "竞争平台产品与价格的辅助验证。",
    ),
    "owner_domain_enrichment.csv": (
        "卖家/组织",
        "owner identifier or slug",
        "owner 网站域名及地域推断。",
        "组织身份、国家和外部实体合并。",
    ),
    "owner_legal_entity_summary.csv": (
        "卖家/法律实体候选",
        "owner identifier",
        "owner 与法律实体候选的汇总。",
        "高置信法律身份和国家核验。",
    ),
    "oecd_digital_stri.csv": (
        "国家-年份",
        "country + year",
        "OECD 数字服务贸易限制指标。",
        "数字监管环境；仅用于有国家匹配的 owner。",
    ),
    "world_bank_digital_macro.csv": (
        "国家-年份",
        "country + year",
        "世界银行数字与宏观指标。",
        "国际环境控制和描述统计。",
    ),
    "cloud_api_costs.csv": (
        "云服务-计费项目",
        "provider + service + item",
        "公开云计算、网关和传输价格。",
        "服务成本校准，不用于直接反演 API 边际成本。",
    ),
}


DIRECT_MEANINGS = {
    "api_id": "RapidAPI API 产品唯一标识，是 API 层表的主键和跨表合并键。",
    "plan_id": "价格计划唯一标识；通常与 api_id、version_id 联合识别计划版本。",
    "version_id": "API 或价格计划版本标识；平台对极少数计划不返回该字段，此时以 api_id + plan_id 稳定识别。",
    "endpoint_id": "API endpoint 唯一标识。",
    "owner_id": "API 提供者或组织唯一标识。",
    "query_id": "搜索查询单元唯一标识。",
    "search_rank": "API 在给定查询和排序方式中的展示名次，数值越小位置越高。",
    "subscriptions_count": "抓取时平台展示的累计订阅账户数；是采用代理，不等于调用量或真实使用人数。",
    "min_paid_price": "API 所有公开付费计划中的最低正月费，单位通常为美元/月。",
    "has_free_plan": "API 是否存在公开免费计划的 0/1 指示变量。",
    "open_best_score": "API 与开放数据候选的最高语义匹配得分。",
    "schema_overlap_best": "API 与同市场其他产品的最高 endpoint/schema 相似度。",
    "github_repository_count": "匹配到的公开代码仓库数量；未观察私有代码，因此是外部采用下界。",
    "digital_stri": "owner 所在国最近可得 OECD Digital STRI 指标。",
    "spotlights_count": "API 关联的公开 spotlight 推广记录数量。",
    "allowed_plan_developers_count": "该计划明确列入 allowedPlanDevelopers 的开发者数量。",
    "health_total": "公开 healthcheckAnalytics 返回的检查总数。",
    "health_successful": "公开 healthcheckAnalytics 返回的成功检查数。",
    "health_failed": "公开 healthcheckAnalytics 返回的失败检查数。",
    "detail_lookup_terminal": "slug 与 API ID 两种详情查询均返回 NOT_FOUND 的指示变量，表示目录记录存在但当前详情已下架。",
    "detail_lookup_mode": "详情字段的查询路径：slug_owner 为常规查询，api_id_fallback 为 API ID 回退查询。",
}


CORE_HANDOFF = [
    "rapidapi_merged_api_master.csv",
    "rapidapi_merged_plan_contracts.csv",
    "rapidapi_merged_endpoint_schema.csv",
    "rapidapi_merged_search_exposure.csv",
    "rapidapi_external_enriched_panel.csv",
]


def read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return next(csv.reader(handle))


def count_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def load_meanings() -> dict[tuple[str, str], str]:
    meanings: dict[tuple[str, str], str] = {}
    path = MERGED / "rapidapi_merged_variable_dictionary.csv"
    if path.exists():
        frame = pd.read_csv(path, low_memory=False).fillna("")
        for row in frame.to_dict("records"):
            if row.get("meaning_cn"):
                meanings[(str(row["table"]), str(row["variable"]))] = str(row["meaning_cn"])
    external_dictionary = EXTERNAL / "external_variable_dictionary.csv"
    if external_dictionary.exists():
        frame = pd.read_csv(external_dictionary, low_memory=False).fillna("")
        for row in frame.to_dict("records"):
            if row.get("definition"):
                meanings[("rapidapi_external_enriched_panel.csv", str(row["variable"]))] = str(row["definition"])
    return meanings


def table_paths() -> list[Path]:
    paths = []
    for name in TABLE_META:
        candidate = MERGED / name
        if not candidate.exists():
            candidate = EXTERNAL / name
        if candidate.exists():
            paths.append(candidate)
    return paths


def source_label(path: Path) -> str:
    if path.parent == MERGED:
        return "RapidAPI 公开页面/GraphQL 字段及其合并构造"
    return "公开外部来源；具体 URL 和匹配字段见该表 source/url 列"


def build_dictionary(paths: list[Path]) -> pd.DataFrame:
    meanings = load_meanings()
    rows: list[dict[str, Any]] = []
    for path in paths:
        frame = pd.read_csv(path, low_memory=False)
        total_rows = count_rows(path)
        for variable in read_header(path):
            series = frame[variable] if variable in frame else pd.Series(dtype=object)
            meaning = DIRECT_MEANINGS.get(variable) or meanings.get((path.name, variable))
            if not meaning:
                meaning = infer_meaning(path, variable)
            role = variable_role(variable)
            use = empirical_use(variable)
            rows.append(
                {
                    "table": path.name,
                    "level": TABLE_META[path.name][0],
                    "variable": variable,
                    "dtype": str(series.dtype),
                    "nonmissing_share": float(series.notna().mean()) if len(series) else None,
                    "unique_values": int(series.nunique(dropna=True)) if len(series) else 0,
                    "table_rows": total_rows,
                    "meaning_cn": meaning,
                    "role": role,
                    "empirical_use_cn": use,
                    "source": source_label(path),
                }
            )
    return pd.DataFrame(rows)


def write_dictionary_markdown(dictionary: pd.DataFrame) -> Path:
    lines = [
        "# 全部变量说明",
        "",
        "本文档覆盖当前保留的、重要且不重复的研究表。非缺失率和唯一值数量按整张表计算；具体回归仍应在对应估计样本中重新报告覆盖率。",
        "",
    ]
    for table, group in dictionary.groupby("table", sort=False):
        meta = TABLE_META[table]
        lines.extend(
            [
                f"## {table}",
                "",
                f"观察单位：{meta[0]}。主键：`{meta[1]}`。{meta[2]}",
                "",
                "| 变量 | 类型 | 非缺失率 | 含义 | 经验用途 |",
                "|---|---|---:|---|---|",
            ]
        )
        for row in group.to_dict("records"):
            meaning = str(row["meaning_cn"]).replace("|", "\\|")
            use = str(row["empirical_use_cn"]).replace("|", "\\|")
            share = row["nonmissing_share"]
            share_text = "" if pd.isna(share) else f"{100 * share:.1f}%"
            lines.append(
                f"| `{row['variable']}` | `{row['dtype']}` | {share_text} | {meaning} | {use} |"
            )
        lines.append("")
    path = MERGED / "ALL_VARIABLES_ZH.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_handoff(paths: list[Path]) -> Path:
    path_by_name = {path.name: path for path in paths}
    master = pd.read_csv(path_by_name["rapidapi_merged_api_master.csv"], usecols=["api_id"])
    master_ids = set(master["api_id"].dropna().astype(str))
    plans = pd.read_csv(
        path_by_name["rapidapi_merged_plan_contracts.csv"],
        usecols=["api_id", "plan_id", "version_id"],
        low_memory=False,
    )
    missing_plan_version = (
        plans["version_id"].isna()
        | plans["version_id"].astype("string").str.strip().eq("")
    )
    missing_plan_version_count = int(missing_plan_version.sum())
    missing_plan_version_duplicates = int(
        plans.loc[missing_plan_version].duplicated(["api_id", "plan_id"], keep=False).sum()
    )
    missing_plan_version_note = (
        f"平台对 {missing_plan_version_count:,} 行计划未返回可选的 `version_id`，"
        f"这部分以 `api_id + plan_id` 稳定识别；备用键重复 {missing_plan_version_duplicates:,} 行。"
    )

    def api_coverage(path: Path) -> tuple[int, int]:
        header = read_header(path)
        key = "api_id" if "api_id" in header else "api_id_left" if "api_id_left" in header else ""
        if not key:
            return 0, 0
        values = pd.read_csv(path, usecols=[key], low_memory=False)[key].dropna().astype(str)
        ids = set(values)
        return len(ids & master_ids), len(ids - master_ids)

    lines = [
        "# 数据交接说明",
        "",
        "## 交付范围",
        "",
        "当前交付保留静态研究所需的重要、不重复数据。RapidAPI 原始响应已被规范化到五张主表；外部数据先汇总为一张 API 层面板，产品对、代码仓库和候选匹配等无法无损压缩的关系表另存。平台订阅是账户采用代理，不能解释为 API 调用量、付费计划销量或真实下游使用人数。",
        "",
        "## 全量口径",
        "",
        "- **产品研究宇宙全量**：`rapidapi_merged_api_master.csv` 含本轮 Data 类目录发现的 7,962 个唯一 API，主键无缺失、无重复。",
        "- **可达详情全量**：计划与机制字段覆盖 7,958 个当前可由 slug 或 API ID 访问详情的 API；另 4 个目录记录在两种详情查询下均由平台返回 `NOT_FOUND`，作为下架状态保留在产品主表。",
        "- **公开端点全量**：endpoint 表保留详情接口返回的全部公开 endpoint，覆盖 7,881 个 API；无公开 endpoint 或接口未返回 endpoint 的产品不会被虚构为零端点。",
        "- **设计型曝光面板**：搜索表完整保留已经执行的查询、重复轮次与排序结果，但不代表所有可能关键词的搜索宇宙。表中另有 234 个历史曝光 API 不在当前产品截面，应在主分析中以内连接当前主表。",
        "- **匹配型外部数据**：外部面板对 7,962 个 API 均保留一行，GitHub、开放数据、竞争平台、owner 国家和法律实体等字段按来源实际覆盖，空值不能解释为不存在。",
        "",
        "## 主表",
        "",
        "| 文件 | 观察单位 | 行数 | 列数 | 当前 API 覆盖 | 主键 | 覆盖与用途 |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for path in paths:
        level, key, coverage, use = TABLE_META[path.name]
        current_apis, historical_apis = api_coverage(path)
        coverage_text = f"{current_apis:,}" if current_apis else "-"
        if historical_apis:
            coverage_text += f"（另有历史 {historical_apis:,}）"
        lines.append(
            f"| `{path.name}` | {level} | {count_rows(path):,} | {len(read_header(path)):,} | {coverage_text} | `{key}` | {coverage}{use} |"
        )
    lines.extend(
        [
            "",
            "## 合作者最小包",
            "",
            "1. `rapidapi_merged_api_master.csv`：产品、卖家、菜单摘要和主要结构变量；产品层研究宇宙。",
            "2. `rapidapi_merged_plan_contracts.csv`：计划价格、额度和访问合同。",
            "3. `rapidapi_merged_endpoint_schema.csv`：接口范围、复杂度和 schema。",
            "4. `rapidapi_merged_search_exposure.csv`：搜索排序与曝光面板。",
            "5. `rapidapi_external_enriched_panel.csv`：外部采用、替代品、地域、监管和宏观合并；字段覆盖按来源不同。",
            "6. `schema_overlap_pairs.csv`：产品对替代关系；仅在研究局部竞争和复制性时传递。",
            "7. `ALL_VARIABLES_ZH.md` 与 `all_retained_variables_dictionary.csv`：完整变量解释。",
            "8. `rapidapi_merged_validation.json` 与 `rapidapi_merged_table_manifest.csv`：主键和行数审计。",
            "",
            "## 合并关系",
            "",
            "- API 层表以 `api_id` 一对一合并。",
            f"- 计划表以 `api_id` 多对一连接 API 主表；常规唯一键为 `api_id + plan_id + version_id`。{missing_plan_version_note}",
            "- endpoint 表以 `api_id` 多对一连接主表；endpoint 唯一键为 `api_id + endpoint_id`。",
            "- 搜索表以 `api_id` 多对一连接主表；固定效应单元由查询词与排序方式共同定义。",
            "- schema 产品对分别用 `api_id_left`、`api_id_right` 两次连接 API 主表。",
            "",
            "## 识别边界",
            "",
            "- 这是静态截面。当前计划、排序和累计订阅之间存在同时性。",
            "- 约 89% 的 API 有免费计划，最低正月费是升级价格，不是免费用户的即时入口价格。",
            "- 平台没有公开计划层选择人数和免费转付费率，因此不能可靠反演计划层加价或边际成本。",
            "- 公开 GitHub 匹配忽略私有仓库和组织内代码，只能作为站外复用下界。",
            "- owner 国家、竞争平台匹配和法律实体覆盖低于主表，不应作为全样本主识别来源。",
            "- API 返回内容采样需要合法的 RapidAPI 调用凭证；当前未把返回内容当作已观察变量。",
        ]
    )
    path = MERGED / "DATA_HANDOFF_ZH.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_merged_readme(paths: list[Path]) -> Path:
    selected = [path for path in paths if path.name.startswith("rapidapi_merged_")]
    lines = [
        "# RapidAPI Data 合并表说明",
        "",
        "本目录保留五个不可互相无损替代的实证层级：API、plan、endpoint/schema、search exposure 与 marketplace listing。API 层外部字段位于 `../data_external/rapidapi_external_enriched_panel.csv`。",
        "",
        "| 表 | 观察单位 | 行数 | 列数 | 主键 |",
        "|---|---|---:|---:|---|",
    ]
    for path in selected:
        level, key, _, _ = TABLE_META[path.name]
        lines.append(f"| `{path.name}` | {level} | {count_rows(path):,} | {len(read_header(path)):,} | `{key}` |")
    lines.extend(
        [
            "",
            "主分析通常只需 API、plan、endpoint、search exposure 与外部 API 面板。Marketplace listing 用于目录覆盖核验；schema 产品对与逐条外部匹配只在相应机制或人工复核中使用。完整口径、覆盖与识别边界见 `DATA_HANDOFF_ZH.md`。",
        ]
    )
    path = MERGED / "README.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    paths = table_paths()
    dictionary = build_dictionary(paths)
    csv_path = MERGED / "all_retained_variables_dictionary.csv"
    dictionary.to_csv(csv_path, index=False)
    md_path = write_dictionary_markdown(dictionary)
    handoff_path = write_handoff(paths)
    readme_path = write_merged_readme(paths)
    print(f"dictionary: {csv_path} ({len(dictionary):,} rows)")
    print(f"markdown: {md_path}")
    print(f"handoff: {handoff_path}")
    print(f"readme: {readme_path}")


if __name__ == "__main__":
    main()

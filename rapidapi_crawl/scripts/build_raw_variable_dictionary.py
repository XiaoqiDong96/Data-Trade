#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT_CSV = DATA / "rapidapi_raw_variable_dictionary_full.csv"
OUT_MD = DATA / "rapidapi_raw_variable_dictionary_full.md"
OUT_INVENTORY = DATA / "rapidapi_raw_table_inventory.csv"

EXCLUDE_NAMES = {
    "rapidapi_raw_variable_dictionary_full.csv",
    "rapidapi_raw_table_inventory.csv",
}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def safe_read_csv(path: Path, nrows: int | None = None) -> pd.DataFrame:
    return pd.read_csv(path, low_memory=False, nrows=nrows)


def count_rows(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            n = sum(1 for _ in f)
        return max(n - 1, 0)
    except OSError:
        return 0


def table_level(path: Path) -> str:
    name = path.name.lower()
    if "exposure" in name:
        return "Search exposure / marketplace visibility"
    if "discovery" in name or "search_data_apis" in name or "categories" in name:
        return "Discovery / search listing"
    if "plan_limit" in name or "billing_limits" in name or "limit_endpoint" in name:
        return "Plan limit / quota"
    if "billing_plans" in name or "plan_enriched" in name or "panel_data_plan" in name:
        return "Plan / pricing contract"
    if "endpoint_params" in name:
        return "Endpoint parameter"
    if "endpoints" in name:
        return "Endpoint"
    if "payload" in name:
        return "Payload / schema"
    if "owner" in name:
        return "Provider / owner"
    if "healthcheck" in name:
        return "Reliability / healthcheck"
    if "spotlight" in name:
        return "Platform promotion / spotlight"
    if "allowed_plan_developers" in name or "access_restrictions" in name:
        return "Access restriction"
    if "api_model" in name or "api_enriched" in name or "details_data_apis" in name:
        return "API / product"
    if "tag" in name:
        return "Tag / taxonomy"
    return "Other"


def variable_role(col: str) -> str:
    c = col.lower()
    if c.endswith("_id") or c in {"id", "api_id", "plan_id", "version_id", "endpoint_id", "owner_id"}:
        return "Identifier / merge key"
    if "slug" in c or c in {"name", "title", "api_name", "plan_name", "owner_name"}:
        return "Display / naming"
    if "price" in c or "pricing" in c or "currency" in c or "period" in c:
        return "Price / monetization"
    if "quota" in c or "limit" in c or "ratelimit" in c or "rate_limit" in c or "overage" in c:
        return "Usage limit / metering"
    if "endpoint" in c or "route" in c or "method" in c or "param" in c or "payload" in c or "schema" in c:
        return "API technical interface"
    if "subscription" in c or "popularity" in c or "rank" in c or "exposure" in c or "spotlight" in c:
        return "Demand / visibility"
    if "rating" in c or "latency" in c or "success" in c or "service" in c or "health" in c:
        return "Quality / reliability"
    if "approval" in c or "allowed" in c or "restricted" in c or "visibility" in c or "hidden" in c:
        return "Access control / visibility"
    if "created" in c or "updated" in c or "date" in c or "time" in c or c.endswith("_at"):
        return "Time"
    if "json" in c or "raw" in c:
        return "Raw JSON / provenance"
    if "description" in c or "readme" in c or "terms" in c or "docs" in c or "spec" in c:
        return "Disclosure / documentation"
    return "Other"


def load_existing_dictionaries() -> tuple[dict[tuple[str, str], str], dict[str, str], dict[tuple[str, str], str], dict[str, str]]:
    exact: dict[tuple[str, str], str] = {}
    by_col: dict[str, str] = {}
    use_exact: dict[tuple[str, str], str] = {}
    use_by_col: dict[str, str] = {}

    panel = DATA / "rapidapi_panel_Data_variable_dictionary.csv"
    if panel.exists():
        df = pd.read_csv(panel, low_memory=False)
        for _, r in df.iterrows():
            table = clean_text(r.get("table"))
            col = clean_text(r.get("column"))
            meaning = clean_text(r.get("meaning_cn")) or clean_text(r.get("meaning"))
            use = clean_text(r.get("empirical_use"))
            if table and col and meaning:
                exact[(table, col)] = meaning
                by_col.setdefault(col, meaning)
            if table and col and use:
                use_exact[(table, col)] = use
                use_by_col.setdefault(col, use)

    static = DATA / "rapidapi_static_Data_variable_dictionary.csv"
    if static.exists():
        df = pd.read_csv(static, low_memory=False)
        for _, r in df.iterrows():
            table = clean_text(r.get("table"))
            col = clean_text(r.get("column"))
            meaning = clean_text(r.get("meaning"))
            if table and col and meaning:
                exact[(table, col)] = meaning
                by_col.setdefault(col, meaning)

    additional = DATA / "rapidapi_additional_Data_variable_dictionary.csv"
    if additional.exists():
        df = pd.read_csv(additional, low_memory=False)
        for _, r in df.iterrows():
            table = clean_text(r.get("file"))
            col = clean_text(r.get("variable"))
            meaning = clean_text(r.get("meaning"))
            if table and col and meaning:
                exact[(table, col)] = meaning
                by_col.setdefault(col, meaning)

    return exact, by_col, use_exact, use_by_col


def infer_meaning(path: Path, col: str) -> str:
    c = col.lower()
    level = table_level(path)

    direct = {
        "api_id": "API 唯一标识，用于连接 API、plan、endpoint、owner、exposure 等表。",
        "plan_id": "价格计划唯一标识，用于连接计划、额度限制和访问控制信息。",
        "version_id": "计划或 API 版本标识，通常用于连接当前计划版本或 playground 版本。",
        "endpoint_id": "endpoint 唯一标识，用于连接 endpoint、参数、payload 和计费限制。",
        "owner_id": "API 提供者或组织的唯一标识。",
        "raw_file": "抓取时保存的原始响应文件路径，用于数据溯源和复核。",
        "category": "RapidAPI 分类或本文抓取类别。",
        "categoryid": "RapidAPI 分类 ID。",
        "pricing": "RapidAPI 页面展示的定价类型或计划定价类型。",
        "currency": "价格货币。",
        "period": "计费周期，如 monthly/yearly。",
        "subscriptions_count": "平台展示的订阅数量，是采用/需求的主要代理变量。",
        "rating": "平台展示评分原始值。",
        "rating_votes": "评分投票数量，刻画声誉信息量。",
        "avg_latency": "平均延迟，刻画接口响应速度。",
        "avg_success_rate": "平均成功率，刻画接口可靠性。",
        "avg_service_level": "平均服务水平，刻画平台质量信号。",
        "readme_len": "README 文本长度，刻画信息披露程度。",
        "long_description_len": "长描述文本长度，刻画产品介绍和信息披露。",
        "created_at": "API 创建时间原始时间戳。",
        "updated_at": "API 最近更新时间原始时间戳。",
    }
    if c in direct:
        return direct[c]

    if c.endswith("_id"):
        return f"{col} 对应的唯一标识或外键，用于在 {level} 相关表之间连接。"
    if "slug" in c:
        return f"{col} 是 URL 或平台内部使用的 slug/短名称。"
    if c.endswith("_name") or c in {"name", "title"}:
        return f"{col} 是平台展示名称或标题。"
    if "description" in c:
        return f"{col} 是描述文本或描述文本长度，用于刻画信息披露。"
    if "json" in c:
        return f"{col} 保存原始 JSON 或嵌套对象字符串，用于溯源和进一步解析。"
    if c.startswith("is_") or c.startswith("has_") or c in {"hidden", "recommended"}:
        return f"{col} 是布尔指示变量，表示对应状态是否存在。"
    if "price" in c:
        return f"{col} 是价格、标准化价格或价格摘要变量。"
    if "quota" in c:
        return f"{col} 是调用额度或额度统计变量，刻画 plan 的使用包大小。"
    if "overage" in c:
        return f"{col} 是超额调用价格或超额费摘要变量。"
    if "limit" in c:
        return f"{col} 是调用限制、rate limit、hard/soft limit 或限制条目统计变量。"
    if "approval" in c:
        return f"{col} 表示购买或使用该计划是否需要卖家审批。"
    if "visibility" in c or "hidden" in c:
        return f"{col} 表示 API 或 plan 的公开/隐藏/可见性状态。"
    if "endpoint" in c:
        return f"{col} 与 endpoint 数量、endpoint 映射或 endpoint 级限制有关。"
    if "method" in c:
        return f"{col} 是 HTTP 方法，如 GET、POST、PUT、DELETE。"
    if "route" in c:
        return f"{col} 是 endpoint 路径或路径复杂度变量。"
    if "param" in c:
        return f"{col} 是参数数量、参数属性或参数说明变量，刻画接入复杂度。"
    if "payload" in c:
        return f"{col} 是请求/响应 payload 或 schema 字段信息。"
    if "schema" in c:
        return f"{col} 与 schema 是否存在、schema 行数或 schema 覆盖有关。"
    if "auth" in c or "security" in c:
        return f"{col} 描述认证方式或安全规则，刻画接入门槛。"
    if "health" in c:
        return f"{col} 是 healthcheck 统计变量，刻画接口运行可靠性。"
    if "spotlight" in c:
        return f"{col} 是 spotlight 展示/推广记录变量，刻画平台展示资源。"
    if "exposure" in c or "search_" in c or "rank" in c or "page" in c:
        return f"{col} 是搜索曝光、排序或搜索页位置变量，刻画平台可见性。"
    if "owner" in c or "parent_org" in c:
        return f"{col} 描述 API 提供者、owner 或父组织信息。"
    if "tag" in c:
        return f"{col} 是标签或标签定义变量，用于产品分类和文本特征。"
    if "count" in c or c.endswith("_n"):
        return f"{col} 是数量统计变量。"
    if "len" in c:
        return f"{col} 是文本长度或对象长度变量。"
    if "url" in c or "thumbnail" in c or "website" in c:
        return f"{col} 是 URL、图片或外部链接变量。"
    if "source" in c:
        return f"{col} 记录数据来源、发现来源或抓取来源。"
    return f"{col} 是 {level} 表中的原始字段；当前没有人工字典解释，含义按字段名和所在表推断。"


def empirical_use(col: str) -> str:
    role = variable_role(col)
    if role == "Identifier / merge key":
        return "作为主键或外键连接多层表，不建议直接作为解释变量。"
    if role == "Price / monetization":
        return "用于构造价格、免费计划、版本化菜单和供给侧定价变量。"
    if role == "Usage limit / metering":
        return "用于构造调用额度、访问治理、计量强度和版本化变量。"
    if role == "API technical interface":
        return "用于构造数据范围、接入复杂度、endpoint 限制和接口技术特征。"
    if role == "Demand / visibility":
        return "用于构造采用、声誉、搜索曝光和平台展示变量。"
    if role == "Quality / reliability":
        return "用于构造可靠性、服务质量和购买前质量信号。"
    if role == "Access control / visibility":
        return "用于刻画公开性、审批、隐藏计划、restricted access 和交易摩擦。"
    if role == "Disclosure / documentation":
        return "用于构造披露、可验证性和信息质量变量。"
    if role == "Provider / owner":
        return "用于 owner 层聚合、多产品企业、owner 固定效应和跨市场工具变量。"
    if role == "Time":
        return "用于构造 API 年龄、更新频率或动态面板变量。"
    if role == "Raw JSON / provenance":
        return "用于溯源、复核和必要时重新解析嵌套字段。"
    return "备用控制变量、文本分类或数据审计变量。"


def sample_values(df: pd.DataFrame, col: str, max_items: int = 3) -> str:
    if col not in df.columns:
        return ""
    s = df[col].dropna()
    if s.empty:
        return ""
    vals = []
    for v in s.astype(str).head(50):
        v = clean_text(v)
        if not v or v in vals:
            continue
        if len(v) > 80:
            v = v[:77] + "..."
        vals.append(v)
        if len(vals) >= max_items:
            break
    return " | ".join(vals)


def build() -> tuple[pd.DataFrame, pd.DataFrame]:
    exact, by_col, use_exact, use_by_col = load_existing_dictionaries()
    rows: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    files = sorted(DATA.rglob("*.csv"))
    for path in files:
        if path.name in EXCLUDE_NAMES or "variable_dictionary" in path.name:
            continue
        rel = path.relative_to(DATA).as_posix()
        try:
            df_head = safe_read_csv(path, nrows=1000)
            cols = list(df_head.columns)
            row_count = count_rows(path)
            status = "ok"
        except Exception as exc:
            cols = []
            row_count = 0
            status = f"empty_or_unreadable: {exc}"
            df_head = pd.DataFrame()

        level = table_level(path)
        inventory.append(
            {
                "file": rel,
                "level": level,
                "rows": row_count,
                "columns": len(cols),
                "status": status,
            }
        )
        if not cols:
            continue

        # Full read is acceptable for current CSV sizes; fallback to sampled counts.
        try:
            df = safe_read_csv(path)
        except Exception:
            df = df_head

        for col in cols:
            s = df[col] if col in df.columns else df_head[col]
            non_missing = int(s.notna().sum())
            unique = int(s.nunique(dropna=True)) if non_missing else 0
            dtype = str(s.dtype)
            missing_rate = 1 - non_missing / len(s) if len(s) else math.nan
            table_name = path.name
            source_meaning = (
                exact.get((table_name, col))
                or exact.get((rel, col))
                or by_col.get(col)
                or ""
            )
            meaning = source_meaning or infer_meaning(path, col)
            use = (
                use_exact.get((table_name, col))
                or use_exact.get((rel, col))
                or use_by_col.get(col)
                or empirical_use(col)
            )
            rows.append(
                {
                    "file": rel,
                    "table": table_name,
                    "level": level,
                    "rows_in_file": int(len(df)),
                    "columns_in_file": len(cols),
                    "variable": col,
                    "dtype": dtype,
                    "non_missing": non_missing,
                    "missing_rate": round(missing_rate, 4) if np_isfinite(missing_rate) else "",
                    "unique_values": unique,
                    "role": variable_role(col),
                    "meaning_cn": meaning,
                    "empirical_use": use,
                    "example_values": sample_values(df, col),
                    "source_dictionary_meaning": source_meaning,
                }
            )
    return pd.DataFrame(rows), pd.DataFrame(inventory)


def np_isfinite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except Exception:
        return False


def write_markdown(dictionary: pd.DataFrame, inventory: pd.DataFrame) -> None:
    lines: list[str] = []
    lines.append("# RapidAPI Data 原始变量全量解释文档")
    lines.append("")
    lines.append("本文件覆盖 `rapidapi_crawl/data/**/*.csv` 中的原始抓取和整理表。已有人工字典优先使用；没有人工解释的字段，按字段名、所在表和变量角色推断。")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(f"- 数据表数量：{len(inventory)}")
    lines.append(f"- 可读字段总数：{len(dictionary)}")
    lines.append(f"- 有人工字典解释字段：{int(dictionary['source_dictionary_meaning'].astype(str).ne('').sum())}")
    lines.append(f"- 输出 CSV：`{OUT_CSV.relative_to(ROOT).as_posix()}`")
    lines.append(f"- 表清单 CSV：`{OUT_INVENTORY.relative_to(ROOT).as_posix()}`")
    lines.append("")
    lines.append("## 表清单")
    lines.append("")
    lines.append("| file | level | rows | columns | status |")
    lines.append("|---|---|---:|---:|---|")
    for _, r in inventory.iterrows():
        lines.append(
            f"| `{r['file']}` | {r['level']} | {int(r['rows'])} | {int(r['columns'])} | {clean_text(r['status'])} |"
        )
    lines.append("")
    lines.append("## 变量字典")
    lines.append("")
    for file, group in dictionary.groupby("file", sort=False):
        meta = inventory[inventory["file"] == file].iloc[0].to_dict()
        lines.append(f"### `{file}`")
        lines.append("")
        lines.append(f"- 层级：{meta['level']}")
        lines.append(f"- 行数：{int(meta['rows'])}")
        lines.append(f"- 列数：{int(meta['columns'])}")
        lines.append("")
        lines.append("| variable | dtype | non-missing | unique | role | meaning_cn | empirical_use | examples |")
        lines.append("|---|---|---:|---:|---|---|---|---|")
        for _, r in group.iterrows():
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{clean_text(r['variable'])}`",
                        clean_text(r["dtype"]),
                        str(int(r["non_missing"])),
                        str(int(r["unique_values"])),
                        clean_text(r["role"]),
                        clean_text(r["meaning_cn"]),
                        clean_text(r["empirical_use"]),
                        clean_text(r["example_values"]),
                    ]
                )
                + " |"
            )
        lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    dictionary, inventory = build()
    dictionary.to_csv(OUT_CSV, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)
    inventory.to_csv(OUT_INVENTORY, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)
    write_markdown(dictionary, inventory)
    print(
        {
            "tables": int(len(inventory)),
            "variables": int(len(dictionary)),
            "manual_meanings": int(dictionary["source_dictionary_meaning"].astype(str).ne("").sum()),
            "csv": str(OUT_CSV),
            "markdown": str(OUT_MD),
            "inventory": str(OUT_INVENTORY),
        }
    )


if __name__ == "__main__":
    main()

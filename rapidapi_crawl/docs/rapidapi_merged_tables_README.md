# RapidAPI Data 合并表说明

本目录把当前 Data 类目静态截面的多张 CSV 合并为较少的研究交付表。
合并时保留了主要实证层级：API、plan、endpoint/schema、search exposure、marketplace listing。
大型原始 schema/body JSON 没有嵌入合并表，而是转成计数、类型、状态码、长度和名称列表等变量；需要逐条复核时回到 `rapidapi_crawl/raw/graphql/`。

## 表清单

| 表 | 层级 | 行数 | 列数 | 主键 | 说明 |
|---|---|---:|---:|---|---|
| `rapidapi_merged_api_master.csv` | API | 6974 | 317 | api_id | One row per API. Includes structural sample indicators and compact API-level aggregates from lower-level technical tables. |
| `rapidapi_merged_plan_contracts.csv` | Plan / pricing contract | 23293 | 191 | api_id + plan_id + version_id | One row per API plan. Limit, feature, access-control, allowed-developer, and endpoint-coverage details are compacted to plan-level columns. |
| `rapidapi_merged_endpoint_schema.csv` | Endpoint / schema | 42652 | 53 | api_id + endpoint_id | One row per endpoint. Parameter, payload/schema, billing-item, and plan-limit mappings are compacted to endpoint-level columns. Large raw body/schema JSON fields are summarized rather than embedded. |
| `rapidapi_merged_search_exposure.csv` | Search exposure | 231256 | 61 | query_id + replica_index + search_rank + api_id | Long table at the search-result level. Keeps ranking variation and adds compact query/facet/API feature columns. |
| `rapidapi_merged_marketplace_listings.csv` | Marketplace listing | 7934 | 38 | listing_source + rank/page + api_id | Unified listing table for discovery and search-list API rows. Use search_exposure for the richer repeated ranking panel. |

## 使用建议

1. 描述统计、reduced form 和结构模型优先使用 `rapidapi_merged_api_master.csv` 与 `rapidapi_merged_plan_contracts.csv`。
2. 技术复杂度、接口范围和 schema 机制使用 `rapidapi_merged_endpoint_schema.csv`。
3. 搜索排序、曝光和平台可见性机制使用 `rapidapi_merged_search_exposure.csv`。
4. `rapidapi_merged_marketplace_listings.csv` 用于补充 discovery/search listing 入口的覆盖情况。

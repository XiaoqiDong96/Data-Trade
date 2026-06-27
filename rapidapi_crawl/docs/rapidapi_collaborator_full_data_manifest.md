# RapidAPI Data 全量数据交付清单

本清单用于给合作者说明哪些文件属于当前研究的全量数据。这里的“全量”指当前最新一次 Data 类目静态截面的完整抓取、整理和建模输入，不包括论文结果表、旧版本重复表和空审计表。2026-06-18 已清理早期镜像、空表、旧模型版本和浏览器临时捕获文件。

## 交付原则

优先使用 `rapidapi_crawl/data/` 根目录下的文件。早期镜像 `rapidapi_crawl/data/api平台数据/` 的 API 数量和计划数量少于当前版本，已从工作区删除，不作为主交付数据。

`rapidapi_io_static/tables/` 是回归、结构估计和反事实结果表，属于论文输出，不是原始全量数据。合作者若要复现分析，应优先拿 `rapidapi_crawl/data/` 的全量表和 `rapidapi_io_static/data/commodity_*.csv` 的建模输入。

## 一、必须交付的主数据

这些文件可以支撑大多数描述统计、reduced form 和静态 IO 结构模型。

| 文件 | 行数 | 列数 | 层级 | 含义 |
|---|---:|---:|---|---|
| `rapidapi_crawl/data/rapidapi_static_Data_api_model_panel_plus.csv` | 6,974 | 126 | API | 当前最完整的 API 层静态主表，一行一个 API，整合产品属性、所有者、质量、订阅量、计划菜单、技术复杂度、健康检查、曝光和推广等变量。 |
| `rapidapi_crawl/data/rapidapi_static_Data_plan_enriched.csv` | 23,293 | 157 | Plan | 当前最完整的价格计划表，一行一个 API-plan，包含价格、周期、免费/付费、可见性、审批、推荐、限制摘要、技术和 API 层合并变量。 |
| `rapidapi_crawl/data/rapidapi_panel_Data_plan_limit.csv` | 25,044 | 71 | Plan-limit | 调用额度和计费限制面板，一行一个计划中的一种额度/限制，用于版本化、免费试用、quota 和 overage 定价分析。 |
| `rapidapi_crawl/data/rapidapi_static_Data_plan_limit_endpoint_panel.csv` | 44,894 | 12 | Plan-limit-endpoint | 计划、限制和 endpoint 的展开表，用于识别套餐覆盖哪些接口、限制是否作用于全部 endpoint。 |
| `rapidapi_crawl/data/rapidapi_search_Data_exposure_panel.csv` | 231,256 | 38 | Search exposure | 搜索排序曝光面板，一行一个搜索/筛选组合中的 API 展示位置，用于市场可见性、排序曝光和注意力分配分析。 |
| `rapidapi_crawl/data/rapidapi_search_Data_exposure_api_summary.csv` | 7,003 | 25 | API exposure | API 层曝光汇总表，压缩搜索面板后的曝光次数、平均排名和覆盖范围。 |
| `rapidapi_crawl/data/rapidapi_static_Data_owners.csv` | 3,884 | 23 | Owner | 供给方/开发者主表，一行一个 owner，用于所有者固定效应、多产品供给和集中度分析。 |

## 二、建议同时交付的组件全量表

这些表保存主表背后的展开信息。合作者做机制解释、变量重构或稳健性检验时会用到。

| 文件 | 行数 | 列数 | 层级 | 含义 |
|---|---:|---:|---|---|
| `rapidapi_crawl/data/rapidapi_static_Data_endpoints.csv` | 42,652 | 23 | Endpoint | API endpoint 全量表，衡量产品功能范围、接口复杂度和访问边界。 |
| `rapidapi_crawl/data/rapidapi_static_Data_endpoint_params.csv` | 98,601 | 17 | Endpoint parameter | endpoint 参数全量表，用于构造复杂度、输入维度和使用门槛变量。 |
| `rapidapi_crawl/data/rapidapi_static_Data_payloads.csv` | 105,747 | 19 | Payload/schema | 请求或响应 payload/schema 表，用于度量数据结构复杂度和标准化程度。 |
| `rapidapi_crawl/data/rapidapi_static_Data_billing_item_endpoints.csv` | 12,849 | 14 | Billing item-endpoint | billing item 与 endpoint 的映射表，用于分析不同接口是否被差异化计费。 |
| `rapidapi_crawl/data/rapidapi_static_Data_plan_access_restrictions.csv` | 23,293 | 17 | Access restriction | 计划层访问限制表，用于审批、私有计划和访问治理分析。 |
| `rapidapi_crawl/data/rapidapi_static_Data_allowed_plan_developers.csv` | 297 | 12 | Restricted access | restricted plan 对开发者的展开表，用于研究定向授权和双边匹配。 |
| `rapidapi_crawl/data/rapidapi_static_Data_healthcheck.csv` | 6,974 | 14 | Reliability | API 健康检查结果表，用于可用性、服务质量和信息披露分析。 |
| `rapidapi_crawl/data/rapidapi_static_Data_spotlights.csv` | 1,218 | 20 | Platform promotion | 平台 spotlight 推广表，用于平台编辑推荐和非价格曝光机制分析。 |
| `rapidapi_crawl/data/rapidapi_static_Data_api_versions.csv` | 7,554 | 7 | API version | API 版本信息，用于版本活跃度和产品维护状态分析。 |
| `rapidapi_crawl/data/rapidapi_static_Data_auth.csv` | 552 | 14 | Auth | 认证方式表，用于接入门槛和治理成本变量。 |
| `rapidapi_crawl/data/rapidapi_static_Data_groups.csv` | 15,412 | 9 | Endpoint group | endpoint 分组表，用于功能模块数量和产品范围变量。 |
| `rapidapi_crawl/data/rapidapi_static_Data_playground_versions.csv` | 6,972 | 21 | Playground | playground 版本表，用于交互式测试和试用机制变量。 |
| `rapidapi_crawl/data/rapidapi_static_Data_public_dns.csv` | 6,971 | 7 | DNS | public DNS 信息，用于技术部署和可访问性补充变量。 |
| `rapidapi_crawl/data/rapidapi_static_Data_detail_extra_summary.csv` | 6,974 | 12 | API audit | detail 额外字段抓取汇总，用于判断补充字段覆盖情况。 |
| `rapidapi_crawl/data/rapidapi_static_Data_api_tags.csv` | 40 | 8 | Tag | API 标签展开表，用于分类和产品定位补充。 |
| `rapidapi_crawl/data/rapidapi_categories.csv` | 49 | 6 | Category | RapidAPI 类目清单。 |
| `rapidapi_crawl/data/rapidapi_tag_definitions.csv` | 3 | 7 | Tag definition | 标签定义清单。 |

## 三、原始抓取源表

这些表保留更接近平台接口返回的原始结构。合作者如果要检查清洗逻辑，应同时拿到这一组。

| 文件 | 行数 | 列数 | 层级 | 含义 |
|---|---:|---:|---|---|
| `rapidapi_crawl/data/rapidapi_details_Data_apis.csv` | 6,974 | 34 | API | 原始 API detail 表，当前 API 宇宙的核心源表。 |
| `rapidapi_crawl/data/rapidapi_details_Data_billing_plans.csv` | 23,293 | 29 | Plan | 原始 billing plan 表。 |
| `rapidapi_crawl/data/rapidapi_details_Data_billing_limits.csv` | 25,044 | 26 | Plan-limit | 原始 billing limit 表。 |
| `rapidapi_crawl/data/rapidapi_details_Data_billing_features.csv` | 8,564 | 11 | Billing feature | 原始 billing feature/功能说明表。 |
| `rapidapi_crawl/data/rapidapi_discovery_Data_apis.csv` | 8,393 | 28 | Discovery | discovery/listing 抓取到的 API 结果，包含重复展示和搜索入口信息。 |
| `rapidapi_crawl/data/rapidapi_discovery_Data_combos.csv` | 192 | 10 | Discovery combo | discovery 抓取组合清单。 |
| `rapidapi_crawl/data/rapidapi_search_Data_apis.csv` | 1,441 | 25 | Search API | 搜索入口返回的 API 结果表。 |
| `rapidapi_crawl/data/rapidapi_search_Data_exposure_combos.csv` | 657 | 8 | Search combo | 搜索排序曝光抓取使用的查询/筛选组合。 |
| `rapidapi_crawl/data/rapidapi_search_Data_exposure_facets.csv` | 119,926 | 10 | Search facet | 搜索曝光对应的 facet 展开表。 |
| `rapidapi_crawl/data/rapidapi_search_Data_facets.csv` | 54 | 3 | Search facet dictionary | 搜索 facet 字典。 |

## 四、静态模型直接输入

这些是论文当前静态 IO 模型使用的整理后输入。它们不是平台原始字段全量，但适合合作者快速复现回归、需求估计、供给端计算和反事实。

| 文件 | 行数 | 列数 | 层级 | 含义 |
|---|---:|---:|---|---|
| `rapidapi_io_static/data/commodity_api_static_features.csv` | 6,974 | 220 | API model features | 当前文章使用的 API 层完整特征表，包含构造变量和用于建模的转换变量。 |
| `rapidapi_io_static/data/commodity_menu_api_features.csv` | 6,969 | 31 | API menu features | API 层套餐菜单特征表，概括价格菜单、免费计划、版本化和 quota 结构。 |
| `rapidapi_io_static/data/commodity_static_sample.csv` | 5,360 | 220 | Structural sample | 当前结构需求估计样本。样本小于 6,974，因为需要可用于结构模型的非缺失价格、市场和份额变量。 |
| `rapidapi_io_static/data/commodity_static_supply.csv` | 5,360 | 229 | Supply/counterfactual sample | 在结构样本基础上加入供给侧边际成本、markup 和反事实所需变量。 |

## 五、变量解释和审计文档

这些文件建议和数据一起发。它们帮助合作者理解变量含义、数据来源和覆盖情况。

| 文件 | 含义 |
|---|---|
| `rapidapi_crawl/data/rapidapi_raw_variable_dictionary_full.csv` | 全部原始变量解释表，可筛选。 |
| `rapidapi_crawl/data/rapidapi_raw_variable_dictionary_full.md` | 全部原始变量解释文档，可直接阅读。 |
| `rapidapi_crawl/data/rapidapi_raw_table_inventory.csv` | 全部 CSV 表清单、行数、列数和表层级。 |
| `rapidapi_crawl/data/rapidapi_static_Data_variable_dictionary.csv` | 静态富化表变量字典。 |
| `rapidapi_crawl/data/rapidapi_panel_Data_variable_dictionary.csv` | plan 和 plan-limit 面板变量字典。 |
| `rapidapi_crawl/data/rapidapi_additional_Data_variable_dictionary.csv` | 补充抓取变量字典。 |
| `rapidapi_crawl/data/rapidapi_static_Data_crawl_audit.md` | 静态抓取审计。 |
| `rapidapi_crawl/data/rapidapi_static_Data_summary.json` | 静态抓取汇总。 |
| `rapidapi_crawl/data/rapidapi_search_Data_exposure_summary.json` | 搜索曝光抓取汇总。 |

## 六、不建议作为主数据交付的文件

| 文件或目录 | 原因 |
|---|---|
| `rapidapi_crawl/data/api平台数据/` | 早期镜像，API 行数为 6,898，少于当前主表 6,974；已删除。 |
| `rapidapi_crawl/data/rapidapi_static_Data_api_enriched.csv` | API 层基础富化表，已被 `rapidapi_static_Data_api_model_panel_plus.csv` 覆盖。可留作中间表。 |
| `rapidapi_crawl/data/rapidapi_static_Data_api_model_panel.csv` | `plus` 版本的较小版本，已被 `rapidapi_static_Data_api_model_panel_plus.csv` 覆盖。 |
| `rapidapi_crawl/data/rapidapi_panel_Data_plan.csv` | 可用，但计划层更完整的版本是 `rapidapi_static_Data_plan_enriched.csv`。 |
| `rapidapi_crawl/data/rapidapi_static_Data_assets.csv` | 空表，已删除。 |
| `rapidapi_crawl/data/rapidapi_static_Data_missing_owner.csv` | 空表，已删除。 |
| `rapidapi_crawl/data/rapidapi_static_Data_target_urls.csv` | 空表，已删除。 |
| `rapidapi_io_static/tables/data_access_*` 和 `rapidapi_io_static/tables/io_*` | 旧版论文结果表，已删除；当前保留 `commodity_*` 结果表。 |
| `rapidapi_io_static/data/data_access_*.csv` 和 `rapidapi_io_static/data/io_*.csv` | 早期模型版本或辅助版本，已删除；当前以 `commodity_*.csv` 为准。 |

## 推荐最小交付包

如果希望合作者尽快进入分析、少处理表连接，优先发送合并版：

1. `rapidapi_crawl/data_merged/rapidapi_merged_api_master.csv`
2. `rapidapi_crawl/data_merged/rapidapi_merged_plan_contracts.csv`
3. `rapidapi_crawl/data_merged/rapidapi_merged_endpoint_schema.csv`
4. `rapidapi_crawl/data_merged/rapidapi_merged_search_exposure.csv`
5. `rapidapi_crawl/data_merged/rapidapi_merged_marketplace_listings.csv`
6. `rapidapi_crawl/data_merged/rapidapi_merged_table_manifest.csv`
7. `rapidapi_crawl/data_merged/rapidapi_merged_variable_dictionary.csv`
8. `rapidapi_crawl/data_merged/README.md`

对应压缩包为 `handoff/rapidapi_merged_tables_20260618.zip`。

如果需要保留更多中间表连接细节，原始最小交付口径包含：

1. `rapidapi_crawl/data/rapidapi_static_Data_api_model_panel_plus.csv`
2. `rapidapi_crawl/data/rapidapi_static_Data_plan_enriched.csv`
3. `rapidapi_crawl/data/rapidapi_panel_Data_plan_limit.csv`
4. `rapidapi_crawl/data/rapidapi_static_Data_plan_limit_endpoint_panel.csv`
5. `rapidapi_crawl/data/rapidapi_search_Data_exposure_panel.csv`
6. `rapidapi_crawl/data/rapidapi_search_Data_exposure_api_summary.csv`
7. `rapidapi_crawl/data/rapidapi_static_Data_owners.csv`
8. `rapidapi_crawl/data/rapidapi_static_Data_endpoints.csv`
9. `rapidapi_crawl/data/rapidapi_static_Data_endpoint_params.csv`
10. `rapidapi_crawl/data/rapidapi_static_Data_payloads.csv`
11. `rapidapi_crawl/data/rapidapi_static_Data_healthcheck.csv`
12. `rapidapi_crawl/data/rapidapi_static_Data_spotlights.csv`
13. `rapidapi_io_static/data/commodity_api_static_features.csv`
14. `rapidapi_io_static/data/commodity_menu_api_features.csv`
15. `rapidapi_io_static/data/commodity_static_sample.csv`
16. `rapidapi_io_static/data/commodity_static_supply.csv`
17. `rapidapi_crawl/data/rapidapi_raw_variable_dictionary_full.csv`
18. `rapidapi_crawl/data/rapidapi_raw_table_inventory.csv`

## 推荐完整交付包

如果合作者需要完整复核抓取、清洗和变量构造，应发送：

1. `rapidapi_crawl/data/` 根目录下所有非空 `.csv` 和相关 `.json`/`.md` 说明文件。
2. `rapidapi_crawl/data_merged/` 合并表。
3. `rapidapi_io_static/data/commodity_*.csv`。
4. `rapidapi_io_static/report/rapidapi_data_commodity_io_article.md` 和 `rapidapi_io_static/report/rapidapi_data_commodity_io_article.pdf`。
5. 旧版 `rapidapi_crawl/data/api平台数据/` 已删除，不再发送。

## 清理记录

详细删除记录见 `rapidapi_crawl/data/rapidapi_cleanup_deleted_20260618.txt`。本次保留 `rapidapi_crawl/raw/graphql/`，因为它保存逐条原始 GraphQL 响应，后续如果需要重新抽取尚未入表的字段，仍有复核价值。

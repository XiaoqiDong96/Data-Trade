# RapidAPI Data Crawl

Public-only RapidAPI marketplace crawl for the `Data` category.

## Current Snapshot

- Crawl date: 2026-06-14
- Category: `Data`
- Reported category total from RapidAPI search: about `7087`
- Discovered unique public APIs: `6934`
- Detail records currently normalized: `6898` valid APIs from discovery union
- Detail coverage versus discovered APIs: `99.48%`
- Billing plans currently normalized: `23116`
- Billing limits currently normalized: `24867`
- Plan panel rows: `23116`
- Plan-limit panel rows: `24867`
- Public visible plan rows: `21086`
- Note: GraphQL details were expanded with public detail-page HTML/Next Flight recovery. The remaining `36` discovered records had no embedded API payload and are treated as index remnants or unavailable detail pages.

## Main Files

- `data/rapidapi_discovery_Data_apis.csv`
  - Unioned API listing table from many search windows.
  - Key fields: `api_id`, `name`, `slugifiedName`, `pricing`, `popularityScore`, `avgLatency`, `avgServiceLevel`, `avgSuccessRate`, `owner_*`, `discovery_sources`.
- `data/rapidapi_details_Data_apis.csv`
  - API detail table for the completed detail sample.
  - Key fields: `subscriptionsCount`, `rating`, `qualityScore`, `apiType`, `createdAt`, `updatedAt`.
- `data/rapidapi_details_Data_billing_plans.csv`
  - Plan-level price table.
  - Key fields: `plan_visibility`, `price`, `period`, `pricing`, `rateLimit_*`, `billinglimits_count`.
- `data/rapidapi_details_Data_billing_limits.csv`
  - Quota and overage table.
  - Key fields: `amount`, `period`, `overageprice`, `limitType`, `billingitem_name`.
- `data/rapidapi_panel_Data_plan.csv`
  - API x billing-plan panel for price/menu regressions.
  - Key fields: `plan_monthly_price`, `price_per_max_quota`, `subscriptions_count`, `rating`, `avg_success_rate`.
- `data/rapidapi_panel_Data_plan_limit.csv`
  - API x billing-plan x quota/limit panel for usage allowance and overage-price regressions.
  - Key fields: `limit_amount_num`, `limit_monthly_amount`, `limit_overage_price_num`, `limit_type`.
- `data/rapidapi_panel_Data_variable_dictionary.csv`
  - Chinese variable dictionary for every column in the two panel tables.
- `data/rapidapi_panel_Data_variable_dictionary.md`
  - Human-readable Markdown version of the Chinese variable dictionary.
- `data/rapidapi_panel_Data_report.md`
  - Panel construction note and recommended empirical sample.
- `data/rapidapi_crawl_audit_summary.json`
  - Row counts and coverage audit.
- `raw/graphql/`
  - Raw GraphQL JSON responses for reproducibility and resumability.

## Why Discovery Is Needed

RapidAPI `searchApis` reports the full category total but stops a single query around the first `1000` hits. The discovery crawler partitions by search term and sort field, then unions API ids:

- Blank query
- `a-z`
- `0-9`
- Common API/data terms
- Tail business/domain terms
- Sort fields: `ByRelevance`, `ByUpdatedAt`, `ByAlphabetical`

This reached `6934` unique APIs, about `97.8%` of the reported total.

## Resume Commands

Full public search window:

```bash
python3 rapidapi_crawl/scripts/rapidapi_crawler.py --category Data --first 100 --delay 0.25
```

Discovery windows:

```bash
python3 rapidapi_crawl/scripts/rapidapi_discovery_crawler.py --category Data --terms-mode letters --seed-existing --delay 0.2
python3 rapidapi_crawl/scripts/rapidapi_discovery_crawler.py --category Data --terms-file rapidapi_crawl/data/rapidapi_common_terms.txt --seed-existing --delay 0.2
python3 rapidapi_crawl/scripts/rapidapi_discovery_crawler.py --category Data --terms-file rapidapi_crawl/data/rapidapi_tail_terms.txt --seed-existing --delay 0.2
```

Continue detail crawl beyond the first 1000 APIs:

```bash
python3 rapidapi_crawl/scripts/rapidapi_crawler.py \
  --category Data \
  --skip-search \
  --details \
  --details-source discovery \
  --details-limit 0 \
  --details-delay 0.35
```

Use `--details-limit N` for chunks if you want staged runs. Existing raw detail JSON files are reused.

Normalize only existing raw detail JSON without making network requests:

```bash
python3 rapidapi_crawl/scripts/rapidapi_crawler.py \
  --category Data \
  --skip-search \
  --details \
  --details-source discovery \
  --details-limit 0 \
  --details-offline-only
```

Rebuild panel tables and the Chinese variable dictionary:

```bash
python3 rapidapi_crawl/scripts/build_rapidapi_panel.py --root rapidapi_crawl --category Data
```

Retry missing or rate-limited detail raw files more cautiously:

```bash
python3 rapidapi_crawl/scripts/rapidapi_detail_parallel.py \
  --root rapidapi_crawl \
  --category Data \
  --source discovery \
  --workers 1 \
  --delay 3 \
  --retry-errors
```

Recover detail records from public API detail HTML pages:

```bash
python3 rapidapi_crawl/scripts/rapidapi_detail_html.py \
  --root rapidapi_crawl \
  --category Data \
  --source discovery \
  --workers 4 \
  --delay 0.15 \
  --retry-errors
```

Static endpoint/docs enrichment:

```bash
python3 rapidapi_crawl/scripts/rapidapi_static_enrichment.py \
  --root rapidapi_crawl \
  --category Data \
  --kinds playground \
  --workers 1 \
  --delay 5 \
  --retry-errors

python3 rapidapi_crawl/scripts/rapidapi_static_enrichment.py \
  --root rapidapi_crawl \
  --category Data \
  --kinds billing_endpoints \
  --workers 1 \
  --delay 5 \
  --retry-errors

python3 rapidapi_crawl/scripts/rapidapi_static_enrichment.py \
  --root rapidapi_crawl \
  --category Data \
  --kinds owner \
  --workers 1 \
  --delay 5 \
  --retry-errors
```

The static enrichment script writes raw files under
`raw/graphql/static_Data/` and normalized CSVs under `data/`. When the gateway
returns `429`, stop and resume later with the same `--retry-errors` command;
valid raw files are skipped automatically.

Rebuild enriched model panels after static enrichment:

```bash
python3 rapidapi_crawl/scripts/build_static_enriched_panel.py --root rapidapi_crawl --category Data
```

## Empirical Model Mapping

- Product: `api_id`
- Firm/provider: `owner_slugifiedName` or `parent_org_slugifiedName`
- Market: RapidAPI category, currently `Data`
- Quality/reputation: `popularityScore`, `avgServiceLevel`, `avgSuccessRate`, `avgLatency`, `rating`, `subscriptionsCount`
- Posted price: `billing_plans.price`
- Usage quota: `billing_limits.amount`
- Overage fee: `billing_limits.overageprice`
- Hard/soft quota: `billing_limits.limitType`
- Rate limit: `billing_plans.rateLimit_amount`, `rateLimit_unitName`
- Endpoint/product complexity: `rapidapi_static_Data_endpoints.csv`,
  `rapidapi_static_Data_endpoint_params.csv`, and
  `rapidapi_static_Data_payloads.csv`
- Plan-to-endpoint scope:
  `rapidapi_static_Data_billing_item_endpoints.csv` connected to
  `billing_limits.billingitem_id`

## External Research Enrichment

The external pipeline adds public, non-RapidAPI evidence in identification
priority order:

1. exact API-host mentions in public GitHub repositories indexed by Sourcegraph;
2. candidate substitutes from Data.gov and the European Data Portal;
3. within-market endpoint and response-schema overlap;
4. competing products and public prices from other API marketplaces;
5. owner websites, RDAP domain records, Common Crawl presence, and GLEIF matches;
6. OECD Digital STRI, World Bank digital indicators, and public AWS API costs.

Run the complete resumable pipeline in the foreground:

```bash
bash rapidapi_crawl/scripts/run_external_research_enrichment.sh
```

Start it in a detached `screen` session with automatic retries:

```bash
bash rapidapi_crawl/scripts/start_external_research_background.sh
```

Raw responses are stored by source under `rapidapi_crawl/external_raw/` and
normalized research tables under `rapidapi_crawl/data_external/`. Both paths
are excluded from Git because they contain research data and machine-generated
outputs. Every normalized source includes a fetch timestamp, source URL, merge
key, and match score where matching is probabilistic.

Actual endpoint response sampling is optional. Set `RAPIDAPI_KEY` to sample at
most one parameter-free GET endpoint from each API with a free plan. The code
stores only field paths, sizes, status codes, and hashed scalar fingerprints;
it never stores response bodies or the API key.

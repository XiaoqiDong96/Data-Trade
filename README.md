# Data-Trade

Reproducible collection and estimation code for a static industrial-organization study of data products sold through API access contracts.

The repository contains source code only. Raw and processed data, row-level results, model outputs, reports, figures, logs, browser state, and literature PDFs are intentionally excluded.

## Research Pipeline

The code connects five empirical layers:

1. API products and sellers.
2. Plan menus, quotas, overage fees, rate limits, and access restrictions.
3. Endpoint schemas and documented payload structure.
4. Query-level marketplace exposure and ranking.
5. External adoption, open substitutes, competitor listings, owner entities, regulation, and service-cost benchmarks.

The analysis scripts produce fundamentals, reduced-form specifications, weak-instrument diagnostics, static differentiated-product demand estimates, supply-side cost calibration, and continuous counterfactual paths.

## Layout

```text
rapidapi_crawl/scripts/       Crawling, normalization, enrichment, validation, and merging
rapidapi_analysis/scripts/    Reduced-form build and optional Stata workflow
rapidapi_io_static/scripts/   Static IO estimation and manuscript generation
requirements.txt              Python dependencies
```

Generated files are written beneath `rapidapi_crawl/data*`, `rapidapi_io_static/full_results`, and `logs`. Those locations are ignored by Git.

## Setup

Python 3.11 or later is recommended.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Optional tools:

- GNU `screen` for detached crawls with automatic restart.
- Pandoc and XeLaTeX for PDF manuscript rendering.
- Stata for `rapidapi_analysis/scripts/run_reduced_form.do`.
- Zotero 7 for the optional library-organization scripts.

No API credential is stored in this repository. The public GraphQL collectors use the headers and session behavior implemented in the crawler; users remain responsible for source terms, rate limits, and applicable law.

## Full Collection

Create a fresh discovery and detail dataset:

```bash
python rapidapi_crawl/scripts/rapidapi_crawler.py \
  --root rapidapi_crawl \
  --category Data \
  --first 100 \
  --max-pages 0

python rapidapi_crawl/scripts/rapidapi_detail_parallel.py \
  --root rapidapi_crawl \
  --category Data \
  --workers 3 \
  --delay 0.7 \
  --retry-errors
```

The conservative end-to-end rebuild is available as:

```bash
bash rapidapi_crawl/scripts/run_conservative_complete_rebuild.sh
```

It writes run manifests and validation files alongside local outputs. A successful HTTP sample is not treated as proof of full coverage; the validators check stable keys, duplicate rows, missing identifiers, error responses, and critical feature coverage.

## Mechanism Completion

To fill health checks, access restrictions, allowed developers, spotlights, billing endpoints, and related static mechanisms:

```bash
bash rapidapi_crawl/scripts/start_mechanism_completion_background.sh
```

The launcher creates a detached `screen` supervisor. The supervisor resumes cached responses, retries failed requests, normalizes the mechanism tables, refreshes the merged baseline, and records terminally unavailable products separately from transient errors.

## Weekly Incremental Update

The weekly workflow compares current discovery with both the merged baseline and prior incremental history. Only previously unseen API identifiers receive the full detail and external-enrichment sequence.

```bash
bash rapidapi_crawl/scripts/start_weekly_incremental_background.sh
```

Useful conservative overrides:

```bash
DETAIL_WORKERS=1 STATIC_WORKERS=1 ADDITIONAL_WORKERS=1 \
DETAIL_DELAY=2 STATIC_DELAY=2 ADDITIONAL_DELAY=2 \
bash rapidapi_crawl/scripts/start_weekly_incremental_background.sh
```

Each run is written to `rapidapi_crawl/data_incremental/<run_id>/`. After validation, `promote_incremental_to_baseline.py` merges rows with stable keys, recomputes sample-standardized variables on the enlarged universe, atomically replaces the local baseline, and refreshes the handoff documentation. A failed run is retried by the supervisor and never publishes data to GitHub.

For repair of an already discovered run, use:

```bash
RUN_ID=<run_id> bash rapidapi_crawl/scripts/run_weekly_incremental_strict_recrawl.sh
```

## Data Handoff

After collection, rebuild the compact collaborator documentation:

```bash
python rapidapi_crawl/scripts/build_data_handoff_docs.py
```

The local handoff centers on five keyed tables: API products, plan contracts, endpoint schemas, search exposure, and marketplace listings. External variables remain in a one-row-per-API panel. Large pairwise schema tables are kept separate because they are only needed for local-competition measures.

## Estimation

Run the full static analysis:

```bash
python rapidapi_io_static/scripts/build_full_data_blp_analysis.py
```

Build the Chinese manuscript and PDF after estimation:

```bash
python rapidapi_io_static/scripts/build_data_commodity_io_article.py
```

To re-render an existing manuscript without rerunning estimation:

```bash
python rapidapi_io_static/scripts/build_data_commodity_io_article.py \
  --skip-analysis \
  --output rapidapi_io_static/full_results/report/article.pdf
```

The structural workflow reports conventional IV, LIML, Anderson-Rubin confidence sets, exclusion-restriction sensitivity, leave-one-market-out estimates, random-coefficient demand diagnostics, and continuous counterfactual curves. The scripts retain weak or failed specifications so that the report distinguishes point-identified, set-identified, and calibrated objects.

## Zotero Helpers

The JavaScript files in `rapidapi_io_static/scripts/zotero_*.js` are run from Zotero's **Tools > Developer > Run JavaScript** window. They create a stable economics and management collection tree, deduplicate project references, classify existing items, retrieve legally available full text through Zotero, and rebuild the missing-fulltext folder. They do not redistribute PDFs through this repository.

## Publication Boundary

GitHub publication is restricted to source code and documentation. Before any push, inspect the exact staged files and run the repository safety scanner:

```bash
python "$HOME/.codex/skills/safe-github-publish/scripts/audit_publish_tree.py" \
  --repo "$PWD" \
  --staged
```

Any finding blocks publication. Never add data, generated tables, figures, reports, logs, browser caches, credentials, or machine-specific configuration.

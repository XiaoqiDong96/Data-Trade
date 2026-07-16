# Full Static IO Analysis

This module estimates reduced-form adoption, within-API plan versioning,
query-level search allocation, external code diffusion, and static BLP demand
for the RapidAPI data marketplace snapshot.

## Inputs

The script expects the locally generated consolidated tables under:

- `rapidapi_crawl/data_merged/`
- `rapidapi_crawl/data_external/`

These research datasets are excluded from GitHub. The crawler and consolidation
scripts in this repository reconstruct them from public sources.

## Environment

Use Python 3.9 or later. NumPy is capped below version 2 because PyBLP 1.2 uses
matrix routines that are not reliable with the project's earlier NumPy 2.0
environment.

```bash
python3 -m venv .venv
.venv/bin/pip install -r rapidapi_io_static/requirements-full-analysis.txt
```

## Run

Run the complete analysis from the repository root:

```bash
.venv/bin/python rapidapi_io_static/scripts/build_full_data_blp_analysis.py
```

Use `--skip-blp` for a quick reduced-form and identification pass.

Generated tables, figures, the model panel, and the report are written under
`rapidapi_io_static/full_results/`. This directory is intentionally ignored by
Git because it contains derived research outputs.

## Empirical Scope

The strongest design is the plan-level regression with API fixed effects and
API-clustered standard errors. Adoption models compare market-FE, exposure-
adjusted PPML, and owner-FE estimates. Search allocation is estimated within
query-by-sort cells with two-way clustered standard errors; alphabetical-sort
exposure is an auxiliary instrument for relevance-sort exposure.

The structural module estimates full-market, entry-price, and paid-entry-only
BLP specifications. It reports conventional differentiation instruments,
seller instruments, contract-governance instruments, first-stage diagnostics,
overidentification tests, and an Anderson-Rubin price-coefficient set. Static
aggregate shares do not identify random-coefficient dispersion in this
snapshot, so counterfactuals report conditional point estimates and
weak-IV-robust price-response ranges rather than imposing heterogeneity.

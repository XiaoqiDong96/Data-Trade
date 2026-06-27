# RapidAPI Data Commodity IO Study

This repository contains code, documentation, and paper outputs for a study of API-based data products on RapidAPI. The project studies data APIs as information goods sold through access contracts, with emphasis on pricing menus, quota design, free trials, search exposure, quality disclosure, and static differentiated-products industrial organization.

The repository intentionally excludes platform data files. Raw crawl outputs, normalized CSV tables, merged research tables, GraphQL responses, and data handoff archives are not tracked in GitHub.

## Repository Structure

```text
rapidapi_crawl/
  scripts/                 # Crawling, normalization, enrichment, and consolidation scripts
  docs/                    # Data manifest and merged-table documentation

rapidapi_io_static/
  scripts/                 # Static IO article and model-building script
  report/                  # Current article draft and identification appendix
  tables/                  # Paper-ready Markdown result tables
  figures/                 # Paper figures

rapidapi_analysis/
  scripts/                 # Earlier reduced-form analysis scripts
  report/                  # Reduced-form report
  tables/                  # Markdown tables only, no CSV data
  figures/                 # Reduced-form figures

literature/
  README_literature_40.md  # Literature organization notes
  *.bib                    # BibTeX files for references

docs/
  *.md                     # Project notes and mechanism reports
```

## Data Policy

Data are kept outside the GitHub repository. The expected local layout, if data are available to a collaborator, is:

```text
rapidapi_crawl/data/
rapidapi_crawl/data_merged/
rapidapi_crawl/raw/
rapidapi_io_static/data/
handoff/
```

The preferred collaborator-facing data package is the merged-table package described in `rapidapi_crawl/docs/rapidapi_collaborator_full_data_manifest.md`. It is not included here.

## Main Pipeline

The current article pipeline is:

```bash
python rapidapi_crawl/scripts/build_consolidated_tables.py --root .
python rapidapi_io_static/scripts/build_data_commodity_io_article.py
```

The first command builds merged research tables from local data. The second command estimates the static empirical model, writes Markdown tables and figures, and exports the article draft.

The reduced-form pipeline is retained for reference:

```bash
python rapidapi_analysis/scripts/build_analysis.py
```

## Dependencies

Python dependencies are listed in `requirements.txt`. Stata scripts are included where used, but Stata is optional for the Python-only article pipeline.

## Notes

This is a research repository. Some scripts require local RapidAPI data files and will not run from a fresh clone until those files are supplied separately.

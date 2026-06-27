# Data-Trade

Code repository for the RapidAPI data-product industrial organization project.

This repository intentionally contains code only. Data, raw crawl outputs, merged tables, reports, figures, and literature PDFs are excluded.

## Structure

```text
rapidapi_crawl/scripts/       # Crawling, enrichment, normalization, and table-consolidation code
rapidapi_analysis/scripts/    # Reduced-form analysis scripts
rapidapi_io_static/scripts/   # Static IO model/article-building code
requirements.txt              # Python dependencies
```

## Data

Place private data locally in the expected project folders when running the scripts:

```text
rapidapi_crawl/data/
rapidapi_crawl/data_merged/
rapidapi_crawl/raw/
rapidapi_io_static/data/
```

These folders are intentionally git-ignored.

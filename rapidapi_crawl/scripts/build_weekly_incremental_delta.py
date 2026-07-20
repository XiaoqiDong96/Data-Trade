#!/usr/bin/env python3
"""Prepare and build weekly incremental RapidAPI tables.

The weekly job treats the current merged tables as the baseline.  It first
discovers the marketplace, keeps only API ids not already known, then builds a
run-specific set of delta tables whose columns match the current merged tables.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_consolidated_tables as consolidated  # noqa: E402


MERGED_TABLES = [
    "rapidapi_merged_api_master.csv",
    "rapidapi_merged_plan_contracts.csv",
    "rapidapi_merged_endpoint_schema.csv",
    "rapidapi_merged_search_exposure.csv",
    "rapidapi_merged_marketplace_listings.csv",
]


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def header(path: Path) -> list[str]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return next(csv.reader(f), [])


def unique_nonempty(values: Iterable[object]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if pd.isna(value):
            continue
        text = str(value).strip()
        if text and text.lower() != "nan" and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def known_ids(merged_dir: Path, history_dir: Path) -> set[str]:
    ids: set[str] = set()
    base = read_csv(merged_dir / "rapidapi_merged_api_master.csv")
    if "api_id" in base.columns:
        ids.update(unique_nonempty(base["api_id"]))
    known = read_csv(history_dir / "known_api_ids.csv")
    if "api_id" in known.columns:
        ids.update(unique_nonempty(known["api_id"]))
    return ids


def align_to_merged(df: pd.DataFrame, merged_dir: Path, table_name: str) -> pd.DataFrame:
    cols = header(merged_dir / table_name)
    if not cols:
        return df.copy()
    out = pd.DataFrame(index=df.index)
    for col in cols:
        out[col] = df[col] if col in df.columns else ""
    return out[cols]


def tag_source(df: pd.DataFrame, source: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "listing_source" not in out.columns:
        out["listing_source"] = source
    return out


def prepare(args: argparse.Namespace) -> None:
    work_root = Path(args.work_root)
    data_dir = work_root / "data"
    merged_dir = Path(args.merged_dir)
    history_dir = Path(args.history_dir)
    run_dir = Path(args.out_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    discovery = read_csv(data_dir / "rapidapi_discovery_Data_apis.csv")
    search = read_csv(data_dir / "rapidapi_search_Data_apis.csv")

    if not discovery.empty:
        shutil.copy2(
            data_dir / "rapidapi_discovery_Data_apis.csv",
            data_dir / "rapidapi_discovery_Data_all_apis.csv",
        )
    if not search.empty:
        shutil.copy2(
            data_dir / "rapidapi_search_Data_apis.csv",
            data_dir / "rapidapi_search_Data_all_apis.csv",
        )

    frames = [df for df in [tag_source(discovery, "discovery"), tag_source(search, "search")] if not df.empty]
    if frames:
        listings = pd.concat(frames, ignore_index=True, sort=False)
    else:
        listings = pd.DataFrame()

    known = known_ids(merged_dir, history_dir)
    if listings.empty or "api_id" not in listings.columns:
        new_listings = pd.DataFrame()
    else:
        listings = listings[listings["api_id"].notna()].copy()
        new_listings = listings[~listings["api_id"].astype(str).isin(known)].copy()
        new_listings = new_listings.drop_duplicates("api_id", keep="first")

    write_csv(data_dir / "rapidapi_discovery_Data_apis.csv", new_listings)
    write_csv(run_dir / "rapidapi_weekly_new_candidates.csv", new_listings)
    write_csv(run_dir / "rapidapi_weekly_new_marketplace_listings_raw.csv", listings[listings.get("api_id", pd.Series(dtype=str)).astype(str).isin(set(unique_nonempty(new_listings.get("api_id", pd.Series(dtype=str)))))].copy() if not listings.empty else pd.DataFrame())

    summary = {
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_known_api_ids": len(known),
        "discovery_rows": int(len(discovery)),
        "search_rows": int(len(search)),
        "candidate_listing_rows": int(len(listings)),
        "new_api_candidates": int(new_listings["api_id"].nunique()) if "api_id" in new_listings.columns else 0,
        "candidate_csv": str(data_dir / "rapidapi_discovery_Data_apis.csv"),
    }
    (run_dir / "rapidapi_weekly_prepare_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def first_existing(paths: list[Path]) -> pd.DataFrame:
    for path in paths:
        df = read_csv(path)
        if not df.empty:
            return df
    return pd.DataFrame()


def filter_new(df: pd.DataFrame, api_ids: set[str]) -> pd.DataFrame:
    if df.empty or "api_id" not in df.columns:
        return pd.DataFrame(columns=list(df.columns))
    return df[df["api_id"].astype(str).isin(api_ids)].copy()


def build_consolidated_delta_sources(work_root: Path, run_dir: Path) -> dict[str, pd.DataFrame]:
    """Build run-scoped tables with the same source joins as the full merge."""
    pseudo_root = run_dir / "_strict_consolidated_root"
    pseudo_out = run_dir / "_strict_consolidated_tables"
    if pseudo_root.exists():
        shutil.rmtree(pseudo_root)
    if pseudo_out.exists():
        shutil.rmtree(pseudo_out)
    (pseudo_root / "rapidapi_crawl").mkdir(parents=True, exist_ok=True)
    (pseudo_root / "rapidapi_io_static").mkdir(parents=True, exist_ok=True)
    data_link = pseudo_root / "rapidapi_crawl" / "data"
    model_link = pseudo_root / "rapidapi_io_static" / "data"
    data_link.symlink_to(work_root.resolve() / "data", target_is_directory=True)
    model_link.symlink_to(work_root.resolve() / "data", target_is_directory=True)
    pseudo_out.mkdir(parents=True, exist_ok=True)

    outputs: dict[str, pd.DataFrame] = {}
    for builder in [
        consolidated.build_api_master,
        consolidated.build_plan_contracts,
        consolidated.build_endpoint_schema,
        consolidated.build_search_exposure,
        consolidated.build_marketplace_listings,
    ]:
        path, _ = builder(pseudo_root, pseudo_out)
        outputs[path.name] = read_csv(path)
    return outputs


def build(args: argparse.Namespace) -> None:
    work_root = Path(args.work_root)
    data_dir = work_root / "data"
    merged_dir = Path(args.merged_dir)
    history_dir = Path(args.history_dir)
    run_dir = Path(args.out_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    details = read_csv(data_dir / "rapidapi_details_Data_apis.csv")
    candidate = read_csv(run_dir / "rapidapi_weekly_new_candidates.csv")
    valid_ids = set(unique_nonempty(details.get("api_id", pd.Series(dtype=str))))
    candidate_ids = set(unique_nonempty(candidate.get("api_id", pd.Series(dtype=str))))
    new_ids = valid_ids or candidate_ids

    source_outputs = build_consolidated_delta_sources(work_root, run_dir)
    listing_source = source_outputs.get("rapidapi_merged_marketplace_listings.csv", pd.DataFrame())
    if listing_source.empty:
        listing_source = read_csv(run_dir / "rapidapi_weekly_new_marketplace_listings_raw.csv")
    if listing_source.empty:
        listing_source = candidate

    outputs = {
        "rapidapi_merged_api_master.csv": filter_new(source_outputs.get("rapidapi_merged_api_master.csv", pd.DataFrame()), new_ids),
        "rapidapi_merged_plan_contracts.csv": filter_new(source_outputs.get("rapidapi_merged_plan_contracts.csv", pd.DataFrame()), new_ids),
        "rapidapi_merged_endpoint_schema.csv": filter_new(source_outputs.get("rapidapi_merged_endpoint_schema.csv", pd.DataFrame()), new_ids),
        "rapidapi_merged_search_exposure.csv": filter_new(source_outputs.get("rapidapi_merged_search_exposure.csv", pd.DataFrame()), new_ids),
        "rapidapi_merged_marketplace_listings.csv": filter_new(listing_source, new_ids),
    }

    manifest_rows: list[dict[str, object]] = []
    for table_name, df in outputs.items():
        aligned = align_to_merged(df, merged_dir, table_name)
        out_path = run_dir / table_name
        write_csv(out_path, aligned)
        key = {
            "rapidapi_merged_api_master.csv": ["api_id"],
            "rapidapi_merged_plan_contracts.csv": ["api_id", "plan_id", "version_id"],
            "rapidapi_merged_endpoint_schema.csv": ["api_id", "endpoint_id"],
            "rapidapi_merged_search_exposure.csv": ["query_id", "replica_index", "search_rank", "api_id"],
            "rapidapi_merged_marketplace_listings.csv": ["listing_source", "rank", "page", "api_id"],
        }[table_name]
        available_key = [c for c in key if c in aligned.columns]
        duplicate_rows = int(aligned.duplicated(available_key, keep=False).sum()) if available_key and not aligned.empty else 0
        manifest_rows.append(
            {
                "table": table_name,
                "rows": int(len(aligned)),
                "columns": int(len(aligned.columns)),
                "key_checked": "|".join(available_key),
                "duplicate_rows_on_key": duplicate_rows,
            }
        )

    manifest = pd.DataFrame(manifest_rows)
    write_csv(run_dir / "rapidapi_weekly_delta_manifest.csv", manifest)

    new_id_rows = pd.DataFrame({"api_id": sorted(new_ids)})
    write_csv(run_dir / "rapidapi_weekly_new_api_ids.csv", new_id_rows)

    known = pd.DataFrame({"api_id": sorted(known_ids(merged_dir, history_dir) | new_ids)})
    write_csv(history_dir / "known_api_ids.csv", known)

    summary = {
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_new_api_ids": len(candidate_ids),
        "valid_new_api_ids": len(valid_ids),
        "delta_tables": manifest_rows,
        "out_dir": str(run_dir),
    }
    (run_dir / "rapidapi_weekly_delta_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "build"])
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--merged-dir", default="rapidapi_crawl/data_merged")
    parser.add_argument("--history-dir", default="rapidapi_crawl/data_incremental")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare(args)
    else:
        build(args)


if __name__ == "__main__":
    main()

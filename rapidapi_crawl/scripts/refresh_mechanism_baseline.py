#!/usr/bin/env python3
"""Refresh consolidated tables after the public mechanism crawl completes."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import promote_incremental_to_baseline as promote  # noqa: E402

pd.set_option("future.no_silent_downcasting", True)


def clean_key(values: pd.Series) -> pd.Series:
    out = values.astype("string").str.strip()
    return out.mask(out.str.lower().isin(["", "nan", "none", "<na>"]))


def overwrite_by_key(
    base: pd.DataFrame,
    updates: pd.DataFrame,
    keys: list[str],
    columns: list[str],
) -> pd.DataFrame:
    if updates.empty:
        return base
    usable = [column for column in columns if column in updates.columns]
    if not usable:
        return base
    right = updates[[*keys, *usable]].drop_duplicates(keys, keep="last").copy()
    marker = "__mechanism_match__"
    right[marker] = 1
    renamed = {column: f"__new_{column}" for column in usable}
    right = right.rename(columns=renamed)
    out = base.merge(right, on=keys, how="left", validate="many_to_one")
    matched = out[marker].eq(1)
    for column in usable:
        new_column = renamed[column]
        if column not in out.columns:
            out[column] = out[new_column]
            continue
        replacement = out[new_column]
        if pd.api.types.is_object_dtype(replacement.dtype) and not pd.api.types.is_object_dtype(out[column].dtype):
            out[column] = out[column].astype("object")
        out[column] = out[column].where(~matched, replacement)
    return out.drop(columns=[marker, *renamed.values()])


def join_unique(values: pd.Series) -> str:
    return "|".join(dict.fromkeys(str(value).strip() for value in values.dropna() if str(value).strip()))


def refresh_api(master: pd.DataFrame, data_dir: Path) -> pd.DataFrame:
    health = promote.read_csv(data_dir / "rapidapi_static_Data_healthcheck.csv")
    health_columns = [
        "health_total",
        "health_failed",
        "health_successful",
        "health_failure_rate",
        "health_success_rate",
        "has_healthcheck_data",
        "health_error",
        "detail_lookup_error",
        "detail_lookup_terminal",
        "detail_lookup_mode",
        "fetched_at",
    ]
    master = overwrite_by_key(master, health, ["api_id"], health_columns)

    detail = promote.read_csv(data_dir / "rapidapi_static_Data_detail_extra_summary.csv")
    if not detail.empty:
        direct_columns = [
            "restricted_plans_count",
            "allowed_developers_total",
            "has_restricted_plan",
            "spotlights_count",
            "has_spotlight",
        ]
        detail_direct = detail[["api_id", *direct_columns]].rename(
            columns={"spotlights_count": "spotlights_count_y"}
        )
        master = overwrite_by_key(
            master,
            detail_direct,
            ["api_id"],
            ["restricted_plans_count", "allowed_developers_total", "has_restricted_plan", "spotlights_count_y", "has_spotlight"],
        )
        detail_prefixed = detail.rename(
            columns={column: f"detail_extra_{column}" for column in detail.columns if column not in ["api_id", "raw_file"]}
        )
        master = overwrite_by_key(
            master,
            detail_prefixed,
            ["api_id"],
            [column for column in detail_prefixed.columns if column != "api_id"],
        )

    spotlights = promote.read_csv(data_dir / "rapidapi_static_Data_spotlights.csv")
    if not spotlights.empty:
        spotlight_agg = spotlights.groupby("api_id", dropna=False).agg(
            spotlight_rows=("spotlight_id", "count"),
            spotlight_types=("spotlight_type", join_unique),
            spotlight_titles=("spotlight_title", join_unique),
        ).reset_index()
        master = overwrite_by_key(
            master,
            spotlight_agg,
            ["api_id"],
            ["spotlight_rows", "spotlight_types", "spotlight_titles"],
        )
    return master


def refresh_plans(plans: pd.DataFrame, data_dir: Path) -> pd.DataFrame:
    access = promote.read_csv(data_dir / "rapidapi_static_Data_plan_access_restrictions.csv")
    if not access.empty:
        rename = {
            column: f"access_{column}"
            for column in access.columns
            if column not in ["api_id", "plan_id", "plan_version_id", "raw_file", "api_slug", "api_name"]
        }
        access = access.rename(columns=rename)
        plans = overwrite_by_key(plans, access, ["api_id", "plan_id"], list(rename.values()))

    allowed = promote.read_csv(data_dir / "rapidapi_static_Data_allowed_plan_developers.csv")
    if not allowed.empty:
        allowed_agg = allowed.groupby(["api_id", "plan_id"], dropna=False).agg(
            allowed_developer_rows=("allowed_developer_index", "count"),
            allowed_developer_user_ids=("allowed_developer_user_id", join_unique),
        ).reset_index()
        plans = overwrite_by_key(
            plans,
            allowed_agg,
            ["api_id", "plan_id"],
            ["allowed_developer_rows", "allowed_developer_user_ids"],
        )
    return plans


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="rapidapi_crawl")
    parser.add_argument("--snapshot-date", default=None)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    merged_dir = root / "data_merged"
    external_dir = root / "data_external"
    data_dir = root / "data"
    snapshot_date = pd.Timestamp(args.snapshot_date or datetime.now(timezone.utc).date(), tz="UTC")

    core = {name: promote.read_csv(merged_dir / name) for name in promote.CORE_TABLES}
    before = {name: len(frame) for name, frame in core.items()}
    api = refresh_api(core["rapidapi_merged_api_master.csv"], data_dir)
    plans = refresh_plans(core["rapidapi_merged_plan_contracts.csv"], data_dir)
    api = promote.recompute_api_features(api, plans, snapshot_date)
    core["rapidapi_merged_api_master.csv"] = api
    core["rapidapi_merged_plan_contracts.csv"] = plans
    core["rapidapi_merged_search_exposure.csv"] = promote.refresh_embedded_api_features(
        core["rapidapi_merged_search_exposure.csv"], api
    )
    core["rapidapi_merged_marketplace_listings.csv"] = promote.refresh_embedded_api_features(
        core["rapidapi_merged_marketplace_listings.csv"], api
    )

    external = {
        name: promote.read_csv(external_dir / name)
        for name in promote.EXTERNAL_TABLES
        if (external_dir / name).exists()
    }
    external_panel = external.get("rapidapi_external_enriched_panel.csv", pd.DataFrame())
    if not external_panel.empty:
        identity_columns = [
            column for column in ["api_slug", "api_title", "owner_id", "owner_slug", "primary_type", "subscriptions_count", "website_url"]
            if column in api.columns
        ]
        external["rapidapi_external_enriched_panel.csv"] = overwrite_by_key(
            external_panel,
            api[["api_id", *identity_columns]],
            ["api_id"],
            identity_columns,
        )

    validation = promote.validate_bundle(core, external, set())
    unchanged_rows = all(len(core[name]) == before[name] for name in core)
    if not validation["valid"] or not unchanged_rows:
        raise SystemExit("Mechanism refresh validation failed before replacement")

    health = promote.read_csv(data_dir / "rapidapi_static_Data_healthcheck.csv")
    detail = promote.read_csv(data_dir / "rapidapi_static_Data_detail_extra_summary.csv")
    restrictions = promote.read_csv(data_dir / "rapidapi_static_Data_plan_access_restrictions.csv")
    allowed = promote.read_csv(data_dir / "rapidapi_static_Data_allowed_plan_developers.csv")
    spotlights = promote.read_csv(data_dir / "rapidapi_static_Data_spotlights.csv")
    manifest = {
        "refreshed_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_date": snapshot_date.isoformat(),
        "api_rows": len(api),
        "plan_rows": len(plans),
        "health_rows": len(health),
        "health_data_api_count": int(pd.to_numeric(health.get("has_healthcheck_data"), errors="coerce").fillna(0).sum()) if not health.empty else 0,
        "detail_response_api_count": int(detail["api_id"].nunique()) if not detail.empty else 0,
        "restriction_rows": len(restrictions),
        "restricted_api_count": int(pd.to_numeric(detail.get("has_restricted_plan"), errors="coerce").fillna(0).sum()) if not detail.empty else 0,
        "allowed_developer_rows": len(allowed),
        "spotlight_rows": len(spotlights),
        "spotlight_api_count": int(spotlights["api_id"].nunique()) if not spotlights.empty else 0,
        "rows_unchanged": unchanged_rows,
        "validation": validation,
    }

    temp_root = Path(tempfile.mkdtemp(prefix="mechanism_refresh_", dir=root))
    replacements: dict[Path, Path] = {}
    try:
        for name, frame in core.items():
            source = temp_root / "data_merged" / name
            promote.write_csv(frame, source)
            replacements[merged_dir / name] = source
        if "rapidapi_external_enriched_panel.csv" in external:
            source = temp_root / "data_external" / "rapidapi_external_enriched_panel.csv"
            promote.write_csv(external["rapidapi_external_enriched_panel.csv"], source)
            replacements[external_dir / "rapidapi_external_enriched_panel.csv"] = source
        validation_path = temp_root / "data_merged" / "rapidapi_merged_validation.json"
        validation_path.parent.mkdir(parents=True, exist_ok=True)
        validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
        replacements[merged_dir / "rapidapi_merged_validation.json"] = validation_path
        validation_rows = []
        for name, check in validation["checks"].items():
            validation_rows.append(
                {
                    "table": name,
                    **check,
                    "key": " + ".join(check["key"]),
                    "missing_key_columns": "|".join(check["missing_key_columns"]),
                }
            )
        validation_csv = temp_root / "data_merged" / "rapidapi_merged_validation.csv"
        promote.write_csv(pd.DataFrame(validation_rows), validation_csv)
        replacements[merged_dir / "rapidapi_merged_validation.csv"] = validation_csv
        promote.update_manifest(merged_dir, core, temp_root / "data_merged")
        replacements[merged_dir / "rapidapi_merged_table_manifest.csv"] = (
            temp_root / "data_merged" / "rapidapi_merged_table_manifest.csv"
        )
        promote.replace_bundle(replacements)
    finally:
        import shutil

        shutil.rmtree(temp_root, ignore_errors=True)

    manifest_path = data_dir / "rapidapi_mechanism_refresh_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fetch and normalize additional public RapidAPI market variables.

This script deliberately avoids owner/profile enrichment. It covers:

- API healthcheck analytics: total, failed, successful checks.
- Detail-level plan restrictions: allowedPlanDevelopers.
- Detail-level marketing/publicity records: spotlights.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rapidapi_crawler import RapidApiClient, read_json, safe_name, save_csv


HEALTHCHECK_QUERY = """query getApiHealthCheck($apiId: String!) {
  healthcheckAnalytics(apiId: $apiId) {
    total
    failed
    successful
  }
}"""


MECHANISM_QUERY = """query getApiStaticMechanisms(
  $apiId: String!,
  $apiOwnerSlug: String,
  $apiSlug: String
) {
  healthcheckAnalytics(apiId: $apiId) {
    total
    failed
    successful
  }
  apiBySlugifiedNameAndOwnerName(
    slugifiedName: $apiSlug,
    ownerName: $apiOwnerSlug
  ) {
    id
    name
    title
    slugifiedName
    owner { id name slugifiedName username }
    billingPlans {
      id
      name
      visibility
      hidden
      recommended
      shouldRequestApproval
      version { id }
      allowedPlanDevelopers { userId }
    }
    spotlights {
      id
      apiId
      type
      weight
      published
      status
      slugifiedName
      title
      description
      spotlightURL
      thumbnailURL
    }
  }
}"""


MECHANISM_BY_ID_QUERY = """query getApiStaticMechanismsById($apiId: ID!) {
  api(id: $apiId) {
    id
    name
    title
    slugifiedName
    owner { id name slugifiedName username }
    billingPlans {
      id
      name
      visibility
      hidden
      recommended
      shouldRequestApproval
      version { id }
      allowedPlanDevelopers { userId }
    }
    spotlights {
      id
      apiId
      type
      weight
      published
      status
      slugifiedName
      title
      description
      spotlightURL
      thumbnailURL
    }
  }
}"""


thread_state = threading.local()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    return re.sub(r"[\r\n\t]+", " ", str(value)).strip()


def json_compact(value: Any) -> str:
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def bool_int(value: Any) -> int:
    return int(bool(value))


def num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_api_targets(root: Path, category: str) -> list[dict[str, Any]]:
    suffix = safe_name(category)
    targets_by_id: dict[str, dict[str, Any]] = {}
    for path in [
        root / "data_merged" / "rapidapi_merged_api_master.csv",
        root / "data" / f"rapidapi_static_{suffix}_api_model_panel.csv",
    ]:
        rows = read_csv_rows(path)
        for row in rows:
            api_id = row.get("api_id")
            if not api_id:
                continue
            candidate = {
                "api_id": api_id,
                "api_slug": row.get("api_slug") or row.get("slugifiedName"),
                "api_name": row.get("api_name") or row.get("name"),
                "api_title": row.get("api_title") or row.get("title"),
                "owner_slug": row.get("owner_slug")
                or row.get("owner_username")
                or row.get("owner_profile_owner_slug"),
                "owner_name": row.get("owner_name") or row.get("owner_profile_owner_name"),
            }
            existing = targets_by_id.get(api_id)
            if existing is None:
                targets_by_id[api_id] = candidate
            else:
                for key, value in candidate.items():
                    if key != "api_id" and not existing.get(key) and value:
                        existing[key] = value

    raw_dir = root / "raw" / "graphql" / f"details_{suffix}"
    for raw_path in sorted(raw_dir.glob("*.json")):
        try:
            api = (read_json(raw_path).get("data") or {}).get("apiBySlugifiedNameAndOwnerName") or {}
        except Exception:
            continue
        api_id = api.get("id")
        if not api_id:
            continue
        owner = api.get("owner") or {}
        candidate = {
            "api_id": api_id,
            "api_slug": api.get("slugifiedName"),
            "api_name": api.get("name"),
            "api_title": api.get("title"),
            "owner_slug": owner.get("slugifiedName") or owner.get("username"),
            "owner_name": owner.get("name"),
        }
        existing = targets_by_id.get(api_id)
        if existing is None:
            targets_by_id[api_id] = candidate
        else:
            for key, value in candidate.items():
                if key != "api_id" and not existing.get(key) and value:
                    existing[key] = value
    return list(targets_by_id.values())


def get_thread_client(category: str) -> RapidApiClient:
    client = getattr(thread_state, "client", None)
    if client is None or getattr(thread_state, "category", None) != category:
        client = RapidApiClient(category)
        client.init()
        thread_state.client = client
        thread_state.category = category
    return client


def health_raw_path(root: Path, category: str, api_id: str) -> Path:
    return root / "raw" / "graphql" / f"additional_{safe_name(category)}" / "healthcheck" / f"{safe_name(api_id)}.json"


def mechanism_raw_path(root: Path, category: str, api_id: str) -> Path:
    return root / "raw" / "graphql" / f"additional_{safe_name(category)}" / "mechanisms" / f"{safe_name(api_id)}.json"


def should_fetch(path: Path, retry_errors: bool) -> bool:
    if not path.exists():
        return True
    if not retry_errors:
        return False
    try:
        data = read_json(path)
    except Exception:
        return True
    return "__error__" in data


def is_not_found_error(error: Any) -> bool:
    text = str(error).lower()
    return "not_found" in text and ("api not found" in text or "user not found" in text or " not found" in text)


def fetch_healthcheck_one(
    root: Path,
    category: str,
    target: dict[str, Any],
    delay: float,
    retry_errors: bool,
) -> dict[str, Any]:
    api_id = target["api_id"]
    raw_path = health_raw_path(root, category, api_id)
    if not should_fetch(raw_path, retry_errors):
        return {"api_id": api_id, "status": "skip"}

    client = get_thread_client(category)
    referer = "https://rapidapi.com/search/Data"
    try:
        data = client.graphql(HEALTHCHECK_QUERY, {"apiId": api_id}, "getApiHealthCheck", referer)
        data["__api_id"] = api_id
        data["__fetched_at"] = utc_now()
        atomic_write_json(raw_path, data)
        status = "ok"
    except Exception as exc:
        atomic_write_json(raw_path, {"__api_id": api_id, "__fetched_at": utc_now(), "__error__": str(exc)})
        status = "error"
    if delay:
        time.sleep(delay)
    return {"api_id": api_id, "status": status}


def fetch_healthchecks(
    root: Path,
    category: str,
    workers: int,
    delay: float,
    retry_errors: bool,
    limit: int,
) -> dict[str, int]:
    targets = load_api_targets(root, category)
    if limit:
        targets = targets[:limit]
    counts = {"ok": 0, "skip": 0, "error": 0}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [
            pool.submit(fetch_healthcheck_one, root, category, target, delay, retry_errors)
            for target in targets
        ]
        for idx, fut in enumerate(as_completed(futures), 1):
            result = fut.result()
            counts[result["status"]] = counts.get(result["status"], 0) + 1
            if idx % 100 == 0 or idx == len(futures):
                print(
                    f"healthcheck {idx}/{len(futures)} "
                    f"ok={counts.get('ok', 0)} skip={counts.get('skip', 0)} error={counts.get('error', 0)}",
                    flush=True,
                )
    return counts


def fetch_mechanism_one(
    root: Path,
    category: str,
    target: dict[str, Any],
    delay: float,
    retry_errors: bool,
) -> dict[str, Any]:
    api_id = target["api_id"]
    raw_path = mechanism_raw_path(root, category, api_id)
    if not should_fetch(raw_path, retry_errors):
        return {"api_id": api_id, "status": "skip"}

    owner_slug = target.get("owner_slug")
    api_slug = target.get("api_slug")
    if not owner_slug or not api_slug:
        atomic_write_json(
            raw_path,
            {
                "__api_id": api_id,
                "__fetched_at": utc_now(),
                "__error__": "missing owner_slug or api_slug",
            },
        )
        return {"api_id": api_id, "status": "error"}

    client = get_thread_client(category)
    referer = "https://rapidapi.com/search/Data"
    variables = {"apiId": api_id, "apiOwnerSlug": owner_slug, "apiSlug": api_slug}
    try:
        data = client.graphql(MECHANISM_QUERY, variables, "getApiStaticMechanisms", referer)
        data["__api_id"] = api_id
        data["__fetched_at"] = utc_now()
        data["__lookup_mode"] = "slug_owner"
        atomic_write_json(raw_path, data)
        status = "ok"
    except Exception as exc:
        if is_not_found_error(exc):
            detail_error = str(exc)
            try:
                detail_data = client.graphql(
                    MECHANISM_BY_ID_QUERY,
                    {"apiId": api_id},
                    "getApiStaticMechanismsById",
                    referer,
                )
                api = (detail_data.get("data") or {}).get("api")
                detail_error = "" if api else detail_error
            except Exception as id_exc:
                api = None
                detail_error = str(id_exc)

            try:
                health_data = client.graphql(
                    HEALTHCHECK_QUERY,
                    {"apiId": api_id},
                    "getApiHealthCheck",
                    referer,
                )
                health = (health_data.get("data") or {}).get("healthcheckAnalytics")
                health_error = ""
            except Exception as health_exc:
                health = None
                health_error = str(health_exc)

            payload = {
                "data": {
                    "healthcheckAnalytics": health,
                    "apiBySlugifiedNameAndOwnerName": api,
                },
                "__api_id": api_id,
                "__fetched_at": utc_now(),
                "__lookup_mode": "api_id_fallback",
                "__slug_lookup_error": str(exc),
            }
            if detail_error:
                payload["__terminal_detail_error"] = detail_error
            if health_error:
                payload["__health_error"] = health_error
            if detail_error and health_error:
                payload["__error__"] = f"detail={detail_error}; health={health_error}"
                status = "error"
            elif detail_error:
                status = "terminal"
            else:
                status = "ok"
            atomic_write_json(raw_path, payload)
        else:
            atomic_write_json(
                raw_path,
                {"__api_id": api_id, "__fetched_at": utc_now(), "__error__": str(exc), "variables": variables},
            )
            status = "error"
    if delay:
        time.sleep(delay)
    return {"api_id": api_id, "status": status}


def fetch_mechanisms(
    root: Path,
    category: str,
    workers: int,
    delay: float,
    retry_errors: bool,
    limit: int,
) -> dict[str, int]:
    targets = load_api_targets(root, category)
    if limit:
        targets = targets[:limit]
    counts = {"ok": 0, "skip": 0, "terminal": 0, "error": 0}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [
            pool.submit(fetch_mechanism_one, root, category, target, delay, retry_errors)
            for target in targets
        ]
        for idx, fut in enumerate(as_completed(futures), 1):
            result = fut.result()
            counts[result["status"]] = counts.get(result["status"], 0) + 1
            if idx % 100 == 0 or idx == len(futures):
                print(
                    f"mechanisms {idx}/{len(futures)} "
                    f"ok={counts.get('ok', 0)} skip={counts.get('skip', 0)} "
                    f"terminal={counts.get('terminal', 0)} error={counts.get('error', 0)}",
                    flush=True,
                )
    return counts


def normalize_healthchecks(root: Path, category: str, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    target_by_id = {row["api_id"]: row for row in targets}
    mechanism_dir = root / "raw" / "graphql" / f"additional_{safe_name(category)}" / "mechanisms"
    legacy_dir = root / "raw" / "graphql" / f"additional_{safe_name(category)}" / "healthcheck"
    raw_paths = sorted(mechanism_dir.glob("*.json"))
    if not raw_paths:
        raw_paths = sorted(legacy_dir.glob("*.json"))
    rows: list[dict[str, Any]] = []
    for raw_path in raw_paths:
        try:
            data = read_json(raw_path)
        except Exception as exc:
            rows.append({"raw_file": str(raw_path), "read_error": str(exc), "has_healthcheck_data": 0})
            continue
        api_id = data.get("__api_id") or raw_path.stem
        meta = target_by_id.get(api_id, {})
        health = (data.get("data") or {}).get("healthcheckAnalytics") or {}
        total = num(health.get("total"))
        failed = num(health.get("failed"))
        successful = num(health.get("successful"))
        failure_rate = failed / total if total and failed is not None else None
        success_rate = successful / total if total and successful is not None else None
        rows.append(
            {
                "api_id": api_id,
                "api_slug": meta.get("api_slug"),
                "api_name": meta.get("api_name"),
                "api_title": meta.get("api_title"),
                "owner_slug": meta.get("owner_slug"),
                "health_total": total,
                "health_failed": failed,
                "health_successful": successful,
                "health_failure_rate": failure_rate,
                "health_success_rate": success_rate,
                "has_healthcheck_data": bool_int(total is not None or failed is not None or successful is not None),
                "health_error": data.get("__health_error") or data.get("__error__"),
                "detail_lookup_error": data.get("__terminal_detail_error") or data.get("__slug_lookup_error"),
                "detail_lookup_terminal": bool_int(data.get("__terminal_detail_error")),
                "detail_lookup_mode": data.get("__lookup_mode"),
                "fetched_at": data.get("__fetched_at"),
                "raw_file": str(raw_path),
            }
        )
    return rows


def iter_detail_apis(root: Path, category: str):
    mechanism_dir = root / "raw" / "graphql" / f"additional_{safe_name(category)}" / "mechanisms"
    mechanism_paths = sorted(mechanism_dir.glob("*.json"))
    if mechanism_paths:
        for raw_path in mechanism_paths:
            try:
                api = (read_json(raw_path).get("data") or {}).get("apiBySlugifiedNameAndOwnerName") or {}
            except Exception:
                continue
            if api:
                yield raw_path, api
        return

    raw_dir = root / "raw" / "graphql" / f"details_{safe_name(category)}"
    for raw_path in sorted(raw_dir.glob("*.json")):
        try:
            api = (read_json(raw_path).get("data") or {}).get("apiBySlugifiedNameAndOwnerName") or {}
        except Exception:
            continue
        if api:
            yield raw_path, api


def normalize_detail_extras(root: Path, category: str) -> dict[str, list[dict[str, Any]]]:
    allowed_rows: list[dict[str, Any]] = []
    restriction_rows: list[dict[str, Any]] = []
    spotlight_rows: list[dict[str, Any]] = []
    api_summary: list[dict[str, Any]] = []

    for raw_path, api in iter_detail_apis(root, category):
        api_id = api.get("id")
        owner = api.get("owner") or {}
        spotlights = api.get("spotlights") or []
        restricted_plans = 0
        allowed_total = 0

        for plan in api.get("billingPlans") or []:
            allowed = plan.get("allowedPlanDevelopers")
            allowed_list = allowed if isinstance(allowed, list) else []
            version = plan.get("version") or {}
            allowed_count = len(allowed_list)
            restricted_plans += int(allowed_count > 0)
            allowed_total += allowed_count
            restriction_rows.append(
                {
                    "api_id": api_id,
                    "api_slug": api.get("slugifiedName"),
                    "api_name": api.get("name"),
                    "owner_id": owner.get("id"),
                    "owner_slug": owner.get("slugifiedName") or owner.get("username"),
                    "plan_id": plan.get("id"),
                    "plan_name": plan.get("name"),
                    "plan_version_id": version.get("id"),
                    "plan_visibility": plan.get("visibility"),
                    "plan_hidden": plan.get("hidden"),
                    "plan_recommended": plan.get("recommended"),
                    "should_request_approval": plan.get("shouldRequestApproval"),
                    "has_allowed_plan_developers_field": int("allowedPlanDevelopers" in plan),
                    "allowed_plan_developers_count": allowed_count,
                    "has_allowed_plan_developers": int(allowed_count > 0),
                    "allowed_plan_developers_json": json_compact(allowed_list),
                    "raw_file": str(raw_path),
                }
            )
            for idx, developer in enumerate(allowed_list, 1):
                developer_id = developer.get("userId") if isinstance(developer, dict) else developer
                allowed_rows.append(
                    {
                        "api_id": api_id,
                        "api_slug": api.get("slugifiedName"),
                        "api_name": api.get("name"),
                        "owner_id": owner.get("id"),
                        "owner_slug": owner.get("slugifiedName") or owner.get("username"),
                        "plan_id": plan.get("id"),
                        "plan_name": plan.get("name"),
                        "plan_version_id": version.get("id"),
                        "allowed_developer_index": idx,
                        "allowed_developer_user_id": developer_id,
                        "allowed_developer_json": json_compact(developer),
                        "raw_file": str(raw_path),
                    }
                )

        for idx, spotlight in enumerate(spotlights, 1):
            spotlight_rows.append(
                {
                    "api_id": api_id,
                    "api_slug": api.get("slugifiedName"),
                    "api_name": api.get("name"),
                    "owner_id": owner.get("id"),
                    "owner_slug": owner.get("slugifiedName") or owner.get("username"),
                    "spotlight_index": idx,
                    "spotlight_id": spotlight.get("id"),
                    "spotlight_api_id": spotlight.get("apiId"),
                    "spotlight_type": spotlight.get("type"),
                    "spotlight_weight": spotlight.get("weight"),
                    "spotlight_published": spotlight.get("published"),
                    "spotlight_status": spotlight.get("status"),
                    "spotlight_slug": spotlight.get("slugifiedName"),
                    "spotlight_title": clean_text(spotlight.get("title")),
                    "spotlight_title_len": len(spotlight.get("title") or ""),
                    "spotlight_description": clean_text(spotlight.get("description")),
                    "spotlight_description_len": len(spotlight.get("description") or ""),
                    "spotlight_url": spotlight.get("spotlightURL"),
                    "spotlight_thumbnail_url": spotlight.get("thumbnailURL"),
                    "raw_file": str(raw_path),
                }
            )

        api_summary.append(
            {
                "api_id": api_id,
                "api_slug": api.get("slugifiedName"),
                "api_name": api.get("name"),
                "owner_id": owner.get("id"),
                "owner_slug": owner.get("slugifiedName") or owner.get("username"),
                "plans_count": len(api.get("billingPlans") or []),
                "restricted_plans_count": restricted_plans,
                "allowed_developers_total": allowed_total,
                "has_restricted_plan": int(restricted_plans > 0),
                "spotlights_count": len(spotlights),
                "has_spotlight": int(len(spotlights) > 0),
                "raw_file": str(raw_path),
            }
        )

    return {
        "allowed_plan_developers": allowed_rows,
        "plan_access_restrictions": restriction_rows,
        "spotlights": spotlight_rows,
        "detail_extra_summary": api_summary,
    }


def write_outputs(root: Path, category: str, tables: dict[str, list[dict[str, Any]]], summary: dict[str, Any]) -> None:
    suffix = safe_name(category)
    data_dir = root / "data"
    for table_name, rows in tables.items():
        save_csv(data_dir / f"rapidapi_static_{suffix}_{table_name}.csv", rows)
    (data_dir / f"rapidapi_additional_{suffix}_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="rapidapi_crawl")
    ap.add_argument("--category", default="Data")
    ap.add_argument("--kinds", default="healthcheck,detail_extras")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--retry-errors", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--normalize-only", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    kinds = {kind.strip() for kind in args.kinds.split(",") if kind.strip()}
    targets = load_api_targets(root, args.category)
    summary: dict[str, Any] = {"category": args.category, "api_targets": len(targets)}

    if {"healthcheck", "detail_extras"} & kinds and not args.normalize_only:
        summary["mechanism_fetch"] = fetch_mechanisms(
            root=root,
            category=args.category,
            workers=args.workers,
            delay=args.delay,
            retry_errors=args.retry_errors,
            limit=args.limit,
        )

    tables: dict[str, list[dict[str, Any]]] = {}
    if "healthcheck" in kinds:
        tables["healthcheck"] = normalize_healthchecks(root, args.category, targets)
    if "detail_extras" in kinds:
        tables.update(normalize_detail_extras(root, args.category))

    for name, rows in tables.items():
        summary[f"{name}_rows"] = len(rows)
    write_outputs(root, args.category, tables, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

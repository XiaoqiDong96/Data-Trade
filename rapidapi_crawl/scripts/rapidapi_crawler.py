#!/usr/bin/env python3
"""Crawl public RapidAPI marketplace data for empirical work.

Public-only workflow:
1. Load the RapidAPI search page to initialize cookies.
2. Fetch `/gateway/csrf`.
3. Use `/gateway/graphql` with `csrf-token` and `rapid-client: hub-service`.
4. Save raw JSON and normalized CSV tables.

The crawler is resumable. Existing raw search/detail JSON files are reused.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

import requests


BASE = "https://rapidapi.com"
GRAPHQL_URL = f"{BASE}/gateway/graphql"
CSRF_URL = f"{BASE}/gateway/csrf"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)


SEARCH_QUERY = """query searchApis($searchApiWhereInput: SearchApiWhereInput!, $paginationInput: PaginationInput, $searchApiOrderByInput: SearchApiOrderByInput) {
  products: searchApis(where: $searchApiWhereInput, pagination: $paginationInput, orderBy: $searchApiOrderByInput) {
    nodes {
      id
      thumbnail
      name
      description
      slugifiedName
      pricing
      updatedAt
      categoryName
      isSavedApi
      title
      visibility
      category: categoryName
      apiCategory { name color }
      score { popularityScore avgLatency avgServiceLevel avgSuccessRate }
      version { tags { id status tagdefinition type value } }
      user: User {
        id
        username
        slugifiedName: username
        name
        type
        parents { id name slugifiedName type thumbnail }
      }
    }
    facets { category { key count } }
    pageInfo { endCursor hasNextPage hasPreviousPage startCursor }
    total
    queryID
    replicaIndex
  }
}"""


DETAIL_QUERY = """query getApiBySlugAndOwner($apiOwnerSlug: String, $apiSlug: String) {
  apiBySlugifiedNameAndOwnerName(slugifiedName: $apiSlug, ownerName: $apiOwnerSlug) {
    id
    name
    title
    description
    visibility
    slugifiedName
    pricing
    updatedAt
    category
    thumbnail
    isSavedApi
    categoryId
    apiCategory { name color }
    score { avgServiceLevel avgLatency avgSuccessRate popularityScore }
    gatewayIds
    createdAt
    status
    longDescription
    apiType
    allowedContext
    isCtxSubscriber
    subscriptionsCount
    websiteUrl
    quality { score }
    owner { id name slugifiedName type thumbnail username parents { id name slugifiedName type thumbnail } }
    versions { id name current createdAt versionStatus }
    version { id apiSubType tags { id status tagdefinition type value } }
    billingPlans {
      id
      name
      recommended
      visibility
      shouldRequestApproval
      requestApprovalQuestion
      hidden
      legalDocumentId
      legalAccountId
      version {
        id
        name
        period
        option
        price
        currency
        current
        billingPlanId: billingplan
        pricing
        localePrice { price symbol }
        billinglimits {
          id
          period
          amount
          unlimited
          overageprice
          item
          limitType
          overageLocalePrice { price symbol }
          billingitem { id name title description displayName type allEndpoints }
          tiersDefinitions { tiersType tiersArray { from to price } }
          priceVariants { id price }
        }
        enablebillingfeatures {
          id
          billingfeature
          type
          status
          note
          billingFeatureObject { id name description }
        }
        rateLimit { enabled unit unitName amount }
      }
    }
    billingItems { id name title description displayName type allEndpoints }
    billingFeatures { id name description type }
    rating { rating votes bestRating }
    documentation { readme { text } }
    termsOfService { id text name }
  }
}"""


def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return value[:180].strip("_") or "unknown"


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys: list[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                keys.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            clean = {
                key: re.sub(r"[\r\n\t]+", " ", value).strip() if isinstance(value, str) else value
                for key, value in row.items()
            }
            writer.writerow(clean)


class RapidApiClient:
    def __init__(self, category: str) -> None:
        self.category = category
        self.session = requests.Session()
        # macOS may expose a stale system proxy through urllib even when command-line
        # clients connect directly. RapidAPI is public, so use a direct session.
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": UA})
        self.csrf_token = ""

    def init(self) -> None:
        search_url = f"{BASE}/search/{self.category.lower()}"
        last_exc: Exception | None = None
        for attempt in range(1, 9):
            try:
                landing = self.session.get(search_url, timeout=30)
                if landing.status_code == 429:
                    retry_after = landing.headers.get("Retry-After")
                    try:
                        wait = float(retry_after) if retry_after else min(120.0, 5.0 * 2 ** (attempt - 1))
                    except ValueError:
                        wait = min(120.0, 5.0 * 2 ** (attempt - 1))
                    time.sleep(wait)
                    continue
                landing.raise_for_status()
                r = self.session.get(CSRF_URL, headers={"Referer": search_url}, timeout=30)
                if r.status_code == 429:
                    retry_after = r.headers.get("Retry-After")
                    try:
                        wait = float(retry_after) if retry_after else min(120.0, 5.0 * 2 ** (attempt - 1))
                    except ValueError:
                        wait = min(120.0, 5.0 * 2 ** (attempt - 1))
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                self.csrf_token = r.json()["csrfToken"]
                return
            except (requests.RequestException, KeyError, ValueError) as exc:
                last_exc = exc
                time.sleep(min(120.0, 3.0 * attempt))
        raise RuntimeError(f"CSRF initialization failed after retries: {last_exc}")

    def graphql(self, query: str, variables: dict[str, Any], operation: str, referer: str) -> dict[str, Any]:
        if not self.csrf_token:
            self.init()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": BASE,
            "Referer": referer,
            "csrf-token": self.csrf_token,
            "rapid-client": "hub-service",
            "x-correlation-id": str(uuid.uuid4()),
        }
        payload = {"query": query, "variables": variables, "operationName": operation}
        last_exc: Exception | None = None
        r = None
        for attempt in range(1, 9):
            try:
                r = self.session.post(GRAPHQL_URL, headers=headers, json=payload, timeout=60)
                if r.status_code in {419, 403}:
                    self.init()
                    headers["csrf-token"] = self.csrf_token
                    headers["x-correlation-id"] = str(uuid.uuid4())
                    r = self.session.post(GRAPHQL_URL, headers=headers, json=payload, timeout=60)
                if r.status_code == 429:
                    retry_after = r.headers.get("Retry-After")
                    try:
                        wait = float(retry_after) if retry_after else min(60.0, 5.0 * 2 ** (attempt - 1))
                    except ValueError:
                        wait = min(60.0, 5.0 * 2 ** (attempt - 1))
                    time.sleep(wait)
                    headers["x-correlation-id"] = str(uuid.uuid4())
                    continue
                break
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(1.5 * attempt)
        if r is None:
            raise RuntimeError(f"GraphQL request failed after retries: {last_exc}")
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            raise RuntimeError(json.dumps(data["errors"], ensure_ascii=False)[:2000])
        return data


def flatten_api(node: dict[str, Any], rank: int, page: int) -> dict[str, Any]:
    score = node.get("score") or {}
    user = node.get("user") or {}
    parents = user.get("parents") or []
    parent = parents[0] if parents else {}
    version = node.get("version") or {}
    tags = version.get("tags") or []
    return {
        "rank": rank,
        "page": page,
        "api_id": node.get("id"),
        "name": node.get("name"),
        "title": node.get("title"),
        "slugifiedName": node.get("slugifiedName"),
        "description": node.get("description"),
        "pricing": node.get("pricing"),
        "updatedAt": node.get("updatedAt"),
        "categoryName": node.get("categoryName") or node.get("category"),
        "visibility": node.get("visibility"),
        "thumbnail": node.get("thumbnail"),
        "popularityScore": score.get("popularityScore"),
        "avgLatency": score.get("avgLatency"),
        "avgServiceLevel": score.get("avgServiceLevel"),
        "avgSuccessRate": score.get("avgSuccessRate"),
        "owner_id": user.get("id"),
        "owner_username": user.get("username"),
        "owner_slugifiedName": user.get("slugifiedName") or user.get("username"),
        "owner_name": user.get("name"),
        "owner_type": user.get("type"),
        "parent_org_id": parent.get("id"),
        "parent_org_name": parent.get("name"),
        "parent_org_slugifiedName": parent.get("slugifiedName"),
        "tags": "|".join(str(t.get("value")) for t in tags if t.get("value") is not None),
    }


def crawl_search(
    client: RapidApiClient,
    root: Path,
    category: str,
    first: int,
    max_pages: int,
    delay: float,
) -> list[dict[str, Any]]:
    raw_dir = root / "raw" / "graphql" / f"search_{safe_name(category)}"
    rows: list[dict[str, Any]] = []
    facets_rows: list[dict[str, Any]] = []
    after = ""
    page = 0
    rank = 0
    total = None
    referer = f"{BASE}/search/{category}?sortBy=ByRelevance"

    while True:
        page += 1
        raw_path = raw_dir / f"page_{page:04d}.json"
        if raw_path.exists():
            data = read_json(raw_path)
        else:
            variables = {
                "paginationInput": {"first": first, "after": after},
                "searchApiOrderByInput": {"sortingFields": [{"fieldName": "ByRelevance", "by": "ASC"}]},
                "searchApiWhereInput": {"term": "", "categoryNames": [category], "tags": []},
            }
            data = client.graphql(SEARCH_QUERY, variables, "searchApis", referer)
            write_json(raw_path, data)
            time.sleep(delay)

        products = data["data"]["products"]
        nodes = products.get("nodes") or []
        total = products.get("total", total)
        for node in nodes:
            rank += 1
            rows.append(flatten_api(node, rank, page))

        if page == 1:
            for facet in (products.get("facets") or {}).get("category") or []:
                facets_rows.append({"facet": "category", "key": facet.get("key"), "count": facet.get("count")})

        page_info = products.get("pageInfo") or {}
        after = page_info.get("endCursor") or ""
        print(f"search page={page} rows={len(rows)} total={total} hasNext={page_info.get('hasNextPage')}", flush=True)
        if not page_info.get("hasNextPage"):
            break
        if max_pages and page >= max_pages:
            break

    save_csv(root / "data" / f"rapidapi_search_{safe_name(category)}_apis.csv", rows)
    if facets_rows:
        save_csv(root / "data" / f"rapidapi_search_{safe_name(category)}_facets.csv", facets_rows)
    write_json(
        root / "data" / f"rapidapi_search_{safe_name(category)}_summary.json",
        {"category": category, "rows": len(rows), "reported_total": total, "pages": page, "first": first},
    )
    return rows


def flatten_detail(api: dict[str, Any]) -> dict[str, Any]:
    score = api.get("score") or {}
    quality = api.get("quality") or {}
    owner = api.get("owner") or {}
    parents = owner.get("parents") or []
    parent = parents[0] if parents else {}
    rating = api.get("rating") or {}
    return {
        "api_id": api.get("id"),
        "name": api.get("name"),
        "slugifiedName": api.get("slugifiedName"),
        "pricing": api.get("pricing"),
        "category": api.get("category"),
        "categoryId": api.get("categoryId"),
        "visibility": api.get("visibility"),
        "status": api.get("status"),
        "apiType": api.get("apiType"),
        "createdAt": api.get("createdAt"),
        "updatedAt": api.get("updatedAt"),
        "subscriptionsCount": api.get("subscriptionsCount"),
        "websiteUrl": api.get("websiteUrl"),
        "qualityScore": quality.get("score"),
        "popularityScore": score.get("popularityScore"),
        "avgLatency": score.get("avgLatency"),
        "avgServiceLevel": score.get("avgServiceLevel"),
        "avgSuccessRate": score.get("avgSuccessRate"),
        "rating": rating.get("rating"),
        "ratingVotes": rating.get("votes"),
        "bestRating": rating.get("bestRating"),
        "owner_id": owner.get("id"),
        "owner_slugifiedName": owner.get("slugifiedName") or owner.get("username"),
        "owner_name": owner.get("name"),
        "owner_type": owner.get("type"),
        "parent_org_id": parent.get("id"),
        "parent_org_name": parent.get("name"),
        "parent_org_slugifiedName": parent.get("slugifiedName"),
        "billingPlans_count": len(api.get("billingPlans") or []),
        "billingItems_count": len(api.get("billingItems") or []),
        "billingFeatures_count": len(api.get("billingFeatures") or []),
        "versions_count": len(api.get("versions") or []),
        "longDescription_len": len(api.get("longDescription") or ""),
        "readme_len": len((((api.get("documentation") or {}).get("readme") or {}).get("text")) or ""),
    }


def normalize_billing(api: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    plan_rows: list[dict[str, Any]] = []
    limit_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    for plan in api.get("billingPlans") or []:
        version = plan.get("version") or {}
        rate = version.get("rateLimit") or {}
        plan_rows.append(
            {
                "api_id": api.get("id"),
                "api_slug": api.get("slugifiedName"),
                "owner_slugifiedName": (api.get("owner") or {}).get("slugifiedName"),
                "plan_id": plan.get("id"),
                "plan_name": plan.get("name"),
                "plan_visibility": plan.get("visibility"),
                "recommended": plan.get("recommended"),
                "hidden": plan.get("hidden"),
                "shouldRequestApproval": plan.get("shouldRequestApproval"),
                "requestApprovalQuestion": plan.get("requestApprovalQuestion"),
                "legalDocumentId": plan.get("legalDocumentId"),
                "legalAccountId": plan.get("legalAccountId"),
                "version_id": version.get("id"),
                "version_name": version.get("name"),
                "version_current": version.get("current"),
                "version_billingPlanId": version.get("billingPlanId"),
                "period": version.get("period"),
                "option": version.get("option"),
                "price": version.get("price"),
                "currency": version.get("currency"),
                "pricing": version.get("pricing"),
                "localePrice": (version.get("localePrice") or {}).get("price"),
                "localeSymbol": (version.get("localePrice") or {}).get("symbol"),
                "rateLimit_enabled": rate.get("enabled"),
                "rateLimit_unit": rate.get("unit"),
                "rateLimit_unitName": rate.get("unitName"),
                "rateLimit_amount": rate.get("amount"),
                "billinglimits_count": len(version.get("billinglimits") or []),
                "features_count": len(version.get("enablebillingfeatures") or []),
            }
        )
        for limit in version.get("billinglimits") or []:
            item = limit.get("billingitem") or {}
            limit_rows.append(
                {
                    "api_id": api.get("id"),
                    "api_slug": api.get("slugifiedName"),
                    "plan_id": plan.get("id"),
                    "plan_name": plan.get("name"),
                    "version_id": version.get("id"),
                    "limit_id": limit.get("id"),
                    "period": limit.get("period"),
                    "amount": limit.get("amount"),
                    "unlimited": limit.get("unlimited"),
                    "overageprice": limit.get("overageprice"),
                    "overageLocalePrice": (limit.get("overageLocalePrice") or {}).get("price"),
                    "overageLocaleSymbol": (limit.get("overageLocalePrice") or {}).get("symbol"),
                    "limitType": limit.get("limitType"),
                    "item": limit.get("item"),
                    "billingitem_id": item.get("id"),
                    "billingitem_name": item.get("name"),
                    "billingitem_title": item.get("title"),
                    "billingitem_description": item.get("description"),
                    "billingitem_displayName": item.get("displayName"),
                    "billingitem_type": item.get("type"),
                    "allEndpoints": item.get("allEndpoints"),
                    "tiersType": (limit.get("tiersDefinitions") or {}).get("tiersType"),
                    "tiersArray_count": len(((limit.get("tiersDefinitions") or {}).get("tiersArray")) or []),
                    "priceVariants_count": len(limit.get("priceVariants") or []),
                    "tiersDefinitions_json": json.dumps(limit.get("tiersDefinitions"), ensure_ascii=False),
                    "priceVariants_json": json.dumps(limit.get("priceVariants"), ensure_ascii=False),
                }
            )
        for feature in version.get("enablebillingfeatures") or []:
            obj = feature.get("billingFeatureObject") or {}
            feature_rows.append(
                {
                    "api_id": api.get("id"),
                    "api_slug": api.get("slugifiedName"),
                    "plan_id": plan.get("id"),
                    "version_id": version.get("id"),
                    "feature_id": feature.get("id"),
                    "billingfeature": feature.get("billingfeature"),
                    "type": feature.get("type"),
                    "status": feature.get("status"),
                    "note": feature.get("note"),
                    "feature_name": obj.get("name"),
                    "feature_description": obj.get("description"),
                }
            )
    return plan_rows, limit_rows, feature_rows


def crawl_details(
    client: RapidApiClient,
    root: Path,
    category: str,
    search_rows: list[dict[str, Any]],
    limit: int,
    delay: float,
    offline_only: bool = False,
) -> None:
    raw_dir = root / "raw" / "graphql" / f"details_{safe_name(category)}"
    detail_rows: list[dict[str, Any]] = []
    plan_rows: list[dict[str, Any]] = []
    limit_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    referer = f"{BASE}/search/{category}?sortBy=ByRelevance"
    targets = search_rows if limit == 0 else search_rows[:limit]

    for idx, row in enumerate(targets, 1):
        owner = row.get("owner_slugifiedName") or row.get("owner_username")
        slug = row.get("slugifiedName")
        raw_file = row.get("raw_file")
        if raw_file:
            raw_path = Path(raw_file)
        elif owner and slug:
            raw_path = raw_dir / f"{idx:05d}_{safe_name(str(owner))}__{safe_name(str(slug))}.json"
        else:
            continue
        if raw_path.exists():
            data = read_json(raw_path)
        elif offline_only:
            print(f"detail {idx}/{len(targets)} offline-skip owner={owner} slug={slug}", flush=True)
            continue
        else:
            variables = {"apiOwnerSlug": owner, "apiSlug": slug}
            try:
                data = client.graphql(DETAIL_QUERY, variables, "getApiBySlugAndOwner", referer)
            except Exception as exc:
                data = {"errors": [{"message": str(exc)}], "variables": variables}
            write_json(raw_path, data)
            time.sleep(delay)

        api = (data.get("data") or {}).get("apiBySlugifiedNameAndOwnerName")
        if not api:
            print(f"detail {idx}/{len(targets)} missing owner={owner} slug={slug}", flush=True)
            continue
        detail_rows.append(flatten_detail(api))
        plans, limits, features = normalize_billing(api)
        plan_rows.extend(plans)
        limit_rows.extend(limits)
        feature_rows.extend(features)
        if idx % 25 == 0 or idx == len(targets):
            print(
                f"detail {idx}/{len(targets)} apis={len(detail_rows)} plans={len(plan_rows)} limits={len(limit_rows)}",
                flush=True,
            )

    suffix = f"{safe_name(category)}"
    save_csv(root / "data" / f"rapidapi_details_{suffix}_apis.csv", detail_rows)
    save_csv(root / "data" / f"rapidapi_details_{suffix}_billing_plans.csv", plan_rows)
    save_csv(root / "data" / f"rapidapi_details_{suffix}_billing_limits.csv", limit_rows)
    save_csv(root / "data" / f"rapidapi_details_{suffix}_billing_features.csv", feature_rows)
    write_json(
        root / "data" / f"rapidapi_details_{suffix}_summary.json",
        {
            "category": category,
            "requested": len(targets),
            "apis": len(detail_rows),
            "billing_plans": len(plan_rows),
            "billing_limits": len(limit_rows),
            "billing_features": len(feature_rows),
        },
    )


def load_api_rows(root: Path, category: str, source: str) -> list[dict[str, Any]]:
    if source == "search":
        path = root / "data" / f"rapidapi_search_{safe_name(category)}_apis.csv"
    elif source == "discovery":
        path = root / "data" / f"rapidapi_discovery_{safe_name(category)}_apis.csv"
    elif source == "raw":
        raw_dir = root / "raw" / "graphql" / f"details_{safe_name(category)}"
        rows: list[dict[str, Any]] = []
        for raw_path in sorted(raw_dir.glob("*.json")):
            try:
                api = (read_json(raw_path).get("data") or {}).get("apiBySlugifiedNameAndOwnerName") or {}
            except Exception:
                continue
            if not api:
                continue
            owner = api.get("owner") or {}
            rows.append(
                {
                    "api_id": api.get("id"),
                    "owner_slugifiedName": owner.get("slugifiedName") or owner.get("username"),
                    "owner_username": owner.get("username"),
                    "slugifiedName": api.get("slugifiedName"),
                    "raw_file": str(raw_path),
                }
            )
        return rows
    else:
        raise ValueError(f"unknown details source: {source}")
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="rapidapi_crawl")
    ap.add_argument("--category", default="Data")
    ap.add_argument("--first", type=int, default=100)
    ap.add_argument("--max-pages", type=int, default=0, help="0 means all pages")
    ap.add_argument("--delay", type=float, default=0.25)
    ap.add_argument("--skip-search", action="store_true")
    ap.add_argument("--details", action="store_true")
    ap.add_argument("--details-source", choices=["search", "discovery", "raw"], default="search")
    ap.add_argument("--details-limit", type=int, default=0, help="0 means all listed APIs")
    ap.add_argument("--details-delay", type=float, default=0.35)
    ap.add_argument("--details-offline-only", action="store_true", help="Normalize existing raw detail JSON only; do not make new network requests.")
    args = ap.parse_args()

    if args.first < 1 or args.first > 100:
        raise SystemExit("--first must be between 1 and 100 for RapidAPI searchApis")

    root = Path(args.root)
    client = RapidApiClient(args.category)
    if not (args.skip_search and args.details and args.details_offline_only):
        client.init()

    if args.skip_search:
        rows = load_api_rows(root, args.category, args.details_source)
        if not rows:
            raise SystemExit(f"No {args.details_source} CSV found; run search/discovery first.")
    else:
        rows = crawl_search(client, root, args.category, args.first, args.max_pages, args.delay)

    if args.details:
        crawl_details(client, root, args.category, rows, args.details_limit, args.details_delay, args.details_offline_only)


if __name__ == "__main__":
    main()

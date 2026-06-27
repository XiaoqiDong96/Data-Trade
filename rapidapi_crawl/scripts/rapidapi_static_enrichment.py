#!/usr/bin/env python3
"""Fetch and normalize static RapidAPI enrichment data.

This script extends the base Data-category crawl with public static metadata
that is useful for empirical IO work:

- API version playground objects: endpoints, parameters, payloads, auth, assets.
- Billing-item to endpoint mappings: which quota/fee item covers which endpoint.
- Minimal owner/provider profiles: non-sensitive organization and portfolio fields.

The script is resumable. Existing raw JSON files are reused unless
``--retry-errors`` is set.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

from rapidapi_crawler import BASE, RapidApiClient, read_json, safe_name, save_csv, write_json


PLAYGROUND_QUERY = """query getApiVersionPlayground($apiVersionId: ID!) {
  apiVersion(apiVersionId: $apiVersionId) {
    id
    name
    spec
    apiVersionType
    assets(visible: true) {
      id
      filename
      title
      description
      visible
      fileSizeBytes
      createdAt
    }
    targetGroup {
      targetUrls { url }
    }
    endpoints(pagingArgs: { limit: -1 }) {
      id
      index
      createdAt
      group
      method
      name
      route
      description
      isGraphQL
      externalDocs {
        description
        url
      }
      params {
        parameters
      }
      responsePayloads {
        id
        name
        format
        body
        headers
        description
        type
        statusCode
        apiendpoint
        examples
        schema
      }
      requestPayloads {
        id
        name
        format
        body
        description
        type
        statusCode
        apiendpoint
        examples
        schema
      }
    }
    groups {
      id
      name
      index
      description
      externalDocs {
        description
        url
      }
    }
    publicdns {
      proxyMode
      address
      current
      id
    }
    accessControl {
      authentication {
        id
        authType
        description
        accessTokenUrl
        handleOauthTokenAtFrontend
        clientSecretRequired
        separator
        authorizationUrl
        requestTokenUrl
        grantType
        clientAuthentication
        authParams {
          id
          name
          description
        }
      }
    }
    payloads(pagingArgs: { limit: -1 }) {
      id
      name
      format
      body
      headers
      description
      type
      statusCode
      apiendpoint
      examples
      schema
    }
  }
}"""


BILLING_ENDPOINTS_QUERY = """query getApiBillingItemEndpoints($apiId: ID!) {
  api(id: $apiId) {
    id
    billingItems {
      id
      name
      title
      description
      displayName
      type
      allEndpoints
      billingItemEndpoints: billingitemendpoints {
        id
        apiEndpoint {
          id
          method
          name
          route
          description
        }
      }
    }
  }
}"""


OWNER_STATIC_QUERY = """query getEntityStatic($id: ID!) {
  entityById(id: $id) {
    id
    name
    slugifiedName
    username
    type
    thumbnail
    description
    bio
    parents {
      id
      name
      slugifiedName
      type
      thumbnail
    }
    publishedApisList {
      id
      name
      title
      slugifiedName
      pricing
      category
      visibility
      score {
        popularityScore
        avgLatency
        avgServiceLevel
        avgSuccessRate
      }
    }
  }
}"""


thread_state = threading.local()


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    text = re.sub(r"[\r\n\t]+", " ", text).strip()
    return text


def json_compact(value: Any) -> str:
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def bool_int(value: Any) -> int:
    return int(bool(value))


def route_depth(route: Any) -> int | None:
    if not route:
        return None
    parts = [part for part in str(route).split("/") if part and not part.startswith("{")]
    return len(parts)


def load_detail_targets(root: Path, category: str) -> list[dict[str, Any]]:
    raw_dir = root / "raw" / "graphql" / f"details_{safe_name(category)}"
    targets: list[dict[str, Any]] = []
    for path in sorted(raw_dir.glob("*.json")):
        try:
            api = (read_json(path).get("data") or {}).get("apiBySlugifiedNameAndOwnerName")
        except Exception:
            continue
        if not api:
            continue
        owner = api.get("owner") or {}
        parents = owner.get("parents") or []
        parent = parents[0] if parents else {}
        version = api.get("version") or {}
        targets.append(
            {
                "api_id": api.get("id"),
                "api_name": api.get("name"),
                "api_title": api.get("title"),
                "api_slug": api.get("slugifiedName"),
                "owner_id": owner.get("id"),
                "owner_slug": owner.get("slugifiedName") or owner.get("username"),
                "owner_type": owner.get("type"),
                "parent_org_id": parent.get("id"),
                "parent_org_slug": parent.get("slugifiedName"),
                "version_id": version.get("id"),
                "version_name": version.get("name"),
                "detail_raw_path": str(path),
            }
        )
    return targets


def client_for_thread(category: str) -> RapidApiClient:
    client = getattr(thread_state, "client", None)
    if client is None:
        client = RapidApiClient(category)
        client.init()
        thread_state.client = client
    return client


def raw_path_for(root: Path, category: str, kind: str, target: dict[str, Any]) -> Path:
    base = root / "raw" / "graphql" / f"static_{safe_name(category)}" / kind
    if kind == "owner":
        return base / f"owner_{safe_name(str(target.get('owner_id')))}.json"
    prefix = f"{safe_name(str(target.get('owner_slug')))}__{safe_name(str(target.get('api_slug')))}"
    return base / f"{prefix}.json"


def has_valid_payload(path: Path, kind: str) -> bool:
    if not path.exists():
        return False
    try:
        data = read_json(path)
    except Exception:
        return False
    data_root = data.get("data") or {}
    if kind == "playground":
        return bool(data_root.get("apiVersion"))
    if kind == "billing_endpoints":
        return bool(data_root.get("api"))
    if kind == "owner":
        return bool(data_root.get("entityById"))
    return False


def error_message(path: Path) -> str:
    if not path.exists():
        return "missing_file"
    try:
        data = read_json(path)
    except Exception as exc:
        return f"invalid_json: {exc}"
    errors = data.get("errors") or []
    if errors:
        first = errors[0] if isinstance(errors[0], dict) else {"message": str(errors[0])}
        return clean_text(first.get("message")) or "error_without_message"
    return "invalid_payload"


def write_missing_lists(root: Path, category: str, targets: list[dict[str, Any]]) -> dict[str, int]:
    suffix = safe_name(category)
    counts: dict[str, int] = {}
    for kind in ["playground", "billing_endpoints", "owner"]:
        rows = []
        source_targets = unique_owner_targets(targets) if kind == "owner" else targets
        for target in source_targets:
            raw_path = raw_path_for(root, category, kind, target)
            if has_valid_payload(raw_path, kind):
                continue
            rows.append(
                {
                    "api_id": target.get("api_id"),
                    "api_slug": target.get("api_slug"),
                    "api_name": target.get("api_name"),
                    "owner_id": target.get("owner_id"),
                    "owner_slug": target.get("owner_slug"),
                    "owner_type": target.get("owner_type"),
                    "parent_org_slug": target.get("parent_org_slug"),
                    "version_id": target.get("version_id"),
                    "missing_reason": error_message(raw_path),
                }
            )
        save_csv(root / "data" / f"rapidapi_static_{suffix}_missing_{kind}.csv", rows)
        counts[kind] = len(rows)
    return counts


def fetch_one(category: str, root: Path, kind: str, target: dict[str, Any], delay: float) -> tuple[str, bool, str]:
    if kind == "playground":
        variables = {"apiVersionId": target.get("version_id")}
        query = PLAYGROUND_QUERY
        operation = "getApiVersionPlayground"
        ok_path = "apiVersion"
    elif kind == "billing_endpoints":
        variables = {"apiId": target.get("api_id")}
        query = BILLING_ENDPOINTS_QUERY
        operation = "getApiBillingItemEndpoints"
        ok_path = "api"
    elif kind == "owner":
        variables = {"id": str(target.get("owner_id"))}
        query = OWNER_STATIC_QUERY
        operation = "getEntityStatic"
        ok_path = "entityById"
    else:
        raise ValueError(kind)

    raw_path = raw_path_for(root, category, kind, target)
    if any(value in (None, "") for value in variables.values()):
        atomic_write_json(raw_path, {"errors": [{"message": "missing required variable"}], "variables": variables})
        return str(raw_path), False, "missing variable"

    owner = target.get("owner_slug") or "unknown"
    slug = target.get("api_slug") or "unknown"
    referer = f"{BASE}/{owner}/api/{slug}"
    try:
        data = client_for_thread(category).graphql(query, variables, operation, referer)
        ok = bool((data.get("data") or {}).get(ok_path))
        if not ok:
            data = {"errors": [{"message": "empty response"}], "variables": variables, "response": data}
        atomic_write_json(raw_path, data)
        if delay:
            time.sleep(delay)
        return str(raw_path), ok, "ok" if ok else "empty"
    except Exception as exc:
        atomic_write_json(raw_path, {"errors": [{"message": str(exc)}], "variables": variables})
        if delay:
            time.sleep(delay)
        return str(raw_path), False, str(exc)[:220]


def unique_owner_targets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in targets:
        owner_id = row.get("owner_id")
        if owner_id and owner_id not in out:
            out[str(owner_id)] = row
    return list(out.values())


def fetch_kind(
    root: Path,
    category: str,
    kind: str,
    targets: list[dict[str, Any]],
    workers: int,
    delay: float,
    limit: int,
    retry_errors: bool,
) -> dict[str, Any]:
    if kind == "owner":
        targets = unique_owner_targets(targets)
    selected = []
    for target in targets:
        raw_path = raw_path_for(root, category, kind, target)
        needed = not has_valid_payload(raw_path, kind) if retry_errors else not raw_path.exists()
        if needed:
            selected.append(target)
    if limit:
        selected = selected[:limit]

    print(
        json.dumps(
            {
                "kind": kind,
                "source_targets": len(targets),
                "fetch_targets": len(selected),
                "workers": workers,
                "delay": delay,
                "retry_errors": retry_errors,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if not selected:
        return {"kind": kind, "attempted": 0, "ok": 0, "failed": 0}

    ok_count = 0
    fail_count = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch_one, category, root, kind, target, delay) for target in selected]
        for idx, future in enumerate(as_completed(futures), 1):
            path, ok, message = future.result()
            ok_count += int(ok)
            fail_count += int(not ok)
            if idx % 50 == 0 or idx == len(selected):
                print(
                    f"{kind} {idx}/{len(selected)} ok={ok_count} failed={fail_count} "
                    f"last={Path(path).name} msg={message}",
                    flush=True,
                )
    return {"kind": kind, "attempted": len(selected), "ok": ok_count, "failed": fail_count}


def iter_valid_detail_apis(root: Path, category: str) -> Iterable[tuple[Path, dict[str, Any]]]:
    raw_dir = root / "raw" / "graphql" / f"details_{safe_name(category)}"
    for path in sorted(raw_dir.glob("*.json")):
        try:
            api = (read_json(path).get("data") or {}).get("apiBySlugifiedNameAndOwnerName")
        except Exception:
            continue
        if api:
            yield path, api


def normalize_detail_static(root: Path, category: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    api_rows: list[dict[str, Any]] = []
    version_rows: list[dict[str, Any]] = []
    tag_rows: list[dict[str, Any]] = []
    spotlight_rows: list[dict[str, Any]] = []

    for path, api in iter_valid_detail_apis(root, category):
        owner = api.get("owner") or {}
        parents = owner.get("parents") or []
        parent = parents[0] if parents else {}
        rating = api.get("rating") or {}
        quality = api.get("quality") or {}
        score = api.get("score") or {}
        doc = api.get("documentation") or {}
        readme = (doc.get("readme") or {}).get("text") or ""
        long_description = api.get("longDescription") or ""
        tos = api.get("termsOfService") or {}
        version = api.get("version") or {}
        gateway_ids = api.get("gatewayIds") or []
        allowed_context = api.get("allowedContext") or []
        spotlights = api.get("spotlights") or []

        api_rows.append(
            {
                "api_id": api.get("id"),
                "api_name": api.get("name"),
                "api_title": api.get("title"),
                "api_slug": api.get("slugifiedName"),
                "api_description": api.get("description"),
                "api_visibility": api.get("visibility"),
                "api_status": api.get("status"),
                "api_type": api.get("apiType"),
                "api_subtype": version.get("apiSubType"),
                "pricing": api.get("pricing"),
                "category": api.get("category"),
                "category_id": api.get("categoryId"),
                "created_at": api.get("createdAt"),
                "updated_at": api.get("updatedAt"),
                "subscriptions_count": api.get("subscriptionsCount"),
                "rating": rating.get("rating"),
                "rating_votes": rating.get("votes"),
                "best_rating": rating.get("bestRating"),
                "quality_score": quality.get("score"),
                "popularity_score": score.get("popularityScore"),
                "avg_latency": score.get("avgLatency"),
                "avg_service_level": score.get("avgServiceLevel"),
                "avg_success_rate": score.get("avgSuccessRate"),
                "owner_id": owner.get("id"),
                "owner_name": owner.get("name"),
                "owner_slug": owner.get("slugifiedName") or owner.get("username"),
                "owner_type": owner.get("type"),
                "parent_org_id": parent.get("id"),
                "parent_org_name": parent.get("name"),
                "parent_org_slug": parent.get("slugifiedName"),
                "website_url": api.get("websiteUrl"),
                "thumbnail_url": api.get("thumbnail"),
                "gateway_ids_count": len(gateway_ids),
                "gateway_ids_json": json_compact(gateway_ids),
                "allowed_context_count": len(allowed_context),
                "allowed_context_json": json_compact(allowed_context),
                "is_context_subscriber": api.get("isCtxSubscriber"),
                "versions_count": len(api.get("versions") or []),
                "current_version_id": version.get("id"),
                "billing_plans_count": len(api.get("billingPlans") or []),
                "billing_items_count": len(api.get("billingItems") or []),
                "billing_features_count": len(api.get("billingFeatures") or []),
                "has_terms_of_service": bool_int(tos),
                "terms_name": tos.get("name") if isinstance(tos, dict) else None,
                "terms_text_len": len(tos.get("text") or "") if isinstance(tos, dict) else 0,
                "long_description_len": len(long_description),
                "readme_len": len(readme),
                "has_readme": bool_int(readme),
                "has_long_description": bool_int(long_description),
                "spotlights_count": len(spotlights),
                "detail_raw_file": str(path),
            }
        )

        for item in api.get("versions") or []:
            version_rows.append(
                {
                    "api_id": api.get("id"),
                    "api_slug": api.get("slugifiedName"),
                    "version_id": item.get("id"),
                    "version_name": item.get("name"),
                    "version_current": item.get("current"),
                    "version_created_at": item.get("createdAt"),
                    "version_status": item.get("versionStatus"),
                }
            )

        for tag in version.get("tags") or []:
            tag_rows.append(
                {
                    "api_id": api.get("id"),
                    "api_slug": api.get("slugifiedName"),
                    "version_id": version.get("id"),
                    "tag_id": tag.get("id"),
                    "tag_status": tag.get("status"),
                    "tag_definition": tag.get("tagdefinition"),
                    "tag_type": tag.get("type"),
                    "tag_value": tag.get("value"),
                }
            )

        for spotlight in spotlights:
            spotlight_rows.append(
                {
                    "api_id": api.get("id"),
                    "api_slug": api.get("slugifiedName"),
                    "spotlight_id": spotlight.get("id"),
                    "spotlight_title": spotlight.get("title"),
                    "spotlight_type": spotlight.get("type"),
                    "spotlight_weight": spotlight.get("weight"),
                    "spotlight_published": spotlight.get("published"),
                    "spotlight_status": spotlight.get("status"),
                    "spotlight_slug": spotlight.get("slugifiedName"),
                    "spotlight_url": spotlight.get("spotlightURL") or spotlight.get("spotlightUrl"),
                    "spotlight_thumbnail": spotlight.get("thumbnailURL") or spotlight.get("thumbnailUrl"),
                    "spotlight_description_len": len(spotlight.get("description") or ""),
                }
            )

    return api_rows, version_rows, tag_rows, spotlight_rows


def normalize_playground(root: Path, category: str, targets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    target_by_version = {row.get("version_id"): row for row in targets if row.get("version_id")}
    out = {
        "playground_versions": [],
        "endpoints": [],
        "endpoint_params": [],
        "payloads": [],
        "assets": [],
        "groups": [],
        "public_dns": [],
        "target_urls": [],
        "auth": [],
    }

    raw_dir = root / "raw" / "graphql" / f"static_{safe_name(category)}" / "playground"
    for path in sorted(raw_dir.glob("*.json")):
        try:
            version = (read_json(path).get("data") or {}).get("apiVersion")
        except Exception:
            continue
        if not version:
            continue
        target = target_by_version.get(version.get("id"), {})
        api_id = target.get("api_id")
        api_slug = target.get("api_slug")
        endpoints = version.get("endpoints") or []
        payloads = version.get("payloads") or []
        assets = version.get("assets") or []
        groups = version.get("groups") or []
        public_dns = version.get("publicdns") or []
        target_urls = ((version.get("targetGroup") or {}).get("targetUrls")) or []
        access = version.get("accessControl") or {}
        auth = access.get("authentication") or {}
        security = access.get("security") or []
        method_counts = Counter(str(ep.get("method") or "").upper() for ep in endpoints if ep.get("method"))

        out["playground_versions"].append(
            {
                "api_id": api_id,
                "api_slug": api_slug,
                "version_id": version.get("id"),
                "version_name": version.get("name"),
                "api_version_type": version.get("apiVersionType"),
                "has_openapi_spec": bool_int(version.get("spec")),
                "spec_len": len(version.get("spec") or ""),
                "endpoints_count": len(endpoints),
                "get_endpoints_count": method_counts.get("GET", 0),
                "post_endpoints_count": method_counts.get("POST", 0),
                "put_endpoints_count": method_counts.get("PUT", 0),
                "delete_endpoints_count": method_counts.get("DELETE", 0),
                "graphql_endpoints_count": sum(bool(ep.get("isGraphQL")) for ep in endpoints),
                "endpoint_groups_count": len(groups),
                "assets_count": len(assets),
                "version_payloads_count": len(payloads),
                "public_dns_count": len(public_dns),
                "target_urls_count": len(target_urls),
                "auth_type": auth.get("authType"),
                "security_rules_count": len(security),
                "raw_file": str(path),
            }
        )

        for endpoint in endpoints:
            endpoint_id = endpoint.get("id")
            params = ((endpoint.get("params") or {}).get("parameters")) or []
            req_payloads = endpoint.get("requestPayloads") or []
            res_payloads = endpoint.get("responsePayloads") or []
            external_docs = endpoint.get("externalDocs") or {}
            out["endpoints"].append(
                {
                    "api_id": api_id,
                    "api_slug": api_slug,
                    "version_id": version.get("id"),
                    "endpoint_id": endpoint_id,
                    "endpoint_index": endpoint.get("index"),
                    "endpoint_created_at": endpoint.get("createdAt"),
                    "endpoint_group": endpoint.get("group"),
                    "method": endpoint.get("method"),
                    "route": endpoint.get("route"),
                    "route_depth": route_depth(endpoint.get("route")),
                    "endpoint_name": endpoint.get("name"),
                    "endpoint_description": endpoint.get("description"),
                    "endpoint_description_len": len(endpoint.get("description") or ""),
                    "is_graphql": endpoint.get("isGraphQL"),
                    "has_external_docs": bool_int(external_docs.get("url") or external_docs.get("description")),
                    "external_docs_url": external_docs.get("url"),
                    "external_docs_description_len": len(external_docs.get("description") or ""),
                    "params_count": len(params),
                    "required_params_count": sum(str(p.get("condition")).upper() == "REQUIRED" for p in params if isinstance(p, dict)),
                    "query_params_count": sum(bool(p.get("querystring")) for p in params if isinstance(p, dict)),
                    "request_payloads_count": len(req_payloads),
                    "response_payloads_count": len(res_payloads),
                    "has_schema": bool_int(
                        endpoint.get("graphQLSchema")
                        or any(p.get("schema") for p in req_payloads + res_payloads if isinstance(p, dict))
                    ),
                }
            )

            for idx, param in enumerate(params, 1):
                if not isinstance(param, dict):
                    out["endpoint_params"].append(
                        {
                            "api_id": api_id,
                            "api_slug": api_slug,
                            "version_id": version.get("id"),
                            "endpoint_id": endpoint_id,
                            "param_order": idx,
                            "param_json": json_compact(param),
                        }
                    )
                    continue
                out["endpoint_params"].append(
                    {
                        "api_id": api_id,
                        "api_slug": api_slug,
                        "version_id": version.get("id"),
                        "endpoint_id": endpoint_id,
                        "param_order": idx,
                        "param_id": param.get("id"),
                        "param_name": param.get("name"),
                        "param_type": param.get("paramType") or param.get("type"),
                        "param_condition": param.get("condition"),
                        "param_status": param.get("status"),
                        "is_querystring": param.get("querystring"),
                        "param_index": param.get("index"),
                        "default_value": param.get("value"),
                        "description": param.get("description"),
                        "description_len": len(param.get("description") or ""),
                        "schema_json": json_compact(param.get("schema") or param.get("schemaDefinition")),
                        "param_json": json_compact(param),
                    }
                )

            for payload_kind, rows in [("request", req_payloads), ("response", res_payloads)]:
                for payload in rows:
                    out["payloads"].append(payload_row(api_id, api_slug, version.get("id"), endpoint_id, payload_kind, "endpoint", payload))

        for payload in payloads:
            out["payloads"].append(payload_row(api_id, api_slug, version.get("id"), payload.get("apiendpoint"), "version", "version", payload))

        for asset in assets:
            out["assets"].append(
                {
                    "api_id": api_id,
                    "api_slug": api_slug,
                    "version_id": version.get("id"),
                    "asset_id": asset.get("id"),
                    "filename": asset.get("filename"),
                    "title": asset.get("title"),
                    "description": asset.get("description"),
                    "visible": asset.get("visible"),
                    "file_size_bytes": asset.get("fileSizeBytes"),
                    "created_at": asset.get("createdAt"),
                }
            )

        for group in groups:
            docs = group.get("externalDocs") or {}
            out["groups"].append(
                {
                    "api_id": api_id,
                    "api_slug": api_slug,
                    "version_id": version.get("id"),
                    "group_id": group.get("id"),
                    "group_name": group.get("name"),
                    "group_index": group.get("index"),
                    "group_description": group.get("description"),
                    "external_docs_url": docs.get("url"),
                    "external_docs_description": docs.get("description"),
                }
            )

        for dns in public_dns:
            out["public_dns"].append(
                {
                    "api_id": api_id,
                    "api_slug": api_slug,
                    "version_id": version.get("id"),
                    "public_dns_id": dns.get("id"),
                    "address": dns.get("address"),
                    "proxy_mode": dns.get("proxyMode"),
                    "current": dns.get("current"),
                }
            )

        for idx, url in enumerate(target_urls, 1):
            out["target_urls"].append(
                {
                    "api_id": api_id,
                    "api_slug": api_slug,
                    "version_id": version.get("id"),
                    "target_url_index": idx,
                    "target_url": url.get("url") if isinstance(url, dict) else url,
                }
            )

        if auth:
            out["auth"].append(
                {
                    "api_id": api_id,
                    "api_slug": api_slug,
                    "version_id": version.get("id"),
                    "auth_row_type": "authentication",
                    "auth_id": auth.get("id"),
                    "auth_type": auth.get("authType"),
                    "security_type": None,
                    "name": None,
                    "description": auth.get("description"),
                    "client_secret_required": auth.get("clientSecretRequired"),
                    "grant_type": auth.get("grantType"),
                    "auth_params_count": len(auth.get("authParams") or []),
                    "requirements_count": None,
                    "raw_json": json_compact(auth),
                }
            )
        for sec in security:
            out["auth"].append(
                {
                    "api_id": api_id,
                    "api_slug": api_slug,
                    "version_id": version.get("id"),
                    "auth_row_type": "security",
                    "auth_id": sec.get("apiVersionId"),
                    "auth_type": None,
                    "security_type": sec.get("securityType"),
                    "name": sec.get("name"),
                    "description": sec.get("description"),
                    "client_secret_required": None,
                    "grant_type": None,
                    "auth_params_count": None,
                    "requirements_count": len(sec.get("requirements") or []),
                    "raw_json": json_compact(sec),
                }
            )

    return out


def payload_row(api_id: Any, api_slug: Any, version_id: Any, endpoint_id: Any, payload_kind: str, scope: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "api_id": api_id,
        "api_slug": api_slug,
        "version_id": version_id,
        "endpoint_id": endpoint_id or payload.get("apiendpoint"),
        "payload_kind": payload_kind,
        "payload_scope": scope,
        "payload_id": payload.get("id"),
        "payload_name": payload.get("name"),
        "format": payload.get("format"),
        "payload_type": payload.get("type"),
        "status_code": payload.get("statusCode"),
        "description": payload.get("description"),
        "description_len": len(payload.get("description") or ""),
        "body": payload.get("body"),
        "body_len": len(payload.get("body") or ""),
        "headers_json": json_compact(payload.get("headers")),
        "examples_json": json_compact(payload.get("examples")),
        "schema_json": json_compact(payload.get("schema")),
        "has_schema": bool_int(payload.get("schema")),
    }


def normalize_billing_endpoints(root: Path, category: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_dir = root / "raw" / "graphql" / f"static_{safe_name(category)}" / "billing_endpoints"
    for path in sorted(raw_dir.glob("*.json")):
        try:
            api = (read_json(path).get("data") or {}).get("api")
        except Exception:
            continue
        if not api:
            continue
        for item in api.get("billingItems") or []:
            mappings = item.get("billingItemEndpoints") or []
            if not mappings:
                rows.append(billing_endpoint_row(api.get("id"), item, None))
            for mapping in mappings:
                rows.append(billing_endpoint_row(api.get("id"), item, mapping))
    return rows


def billing_endpoint_row(api_id: Any, item: dict[str, Any], mapping: dict[str, Any] | None) -> dict[str, Any]:
    endpoint = ((mapping or {}).get("apiEndpoint")) or {}
    return {
        "api_id": api_id,
        "billingitem_id": item.get("id"),
        "billingitem_name": item.get("name"),
        "billingitem_title": item.get("title"),
        "billingitem_description": item.get("description"),
        "billingitem_display_name": item.get("displayName"),
        "billingitem_type": item.get("type"),
        "all_endpoints": item.get("allEndpoints"),
        "billingitemendpoint_id": (mapping or {}).get("id"),
        "endpoint_id": endpoint.get("id"),
        "endpoint_method": endpoint.get("method"),
        "endpoint_name": endpoint.get("name"),
        "endpoint_route": endpoint.get("route"),
        "endpoint_description": endpoint.get("description"),
    }


def normalize_owners(root: Path, category: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_dir = root / "raw" / "graphql" / f"static_{safe_name(category)}" / "owner"
    for path in sorted(raw_dir.glob("*.json")):
        try:
            owner = (read_json(path).get("data") or {}).get("entityById")
        except Exception:
            continue
        if not owner:
            continue
        parents = owner.get("parents") or []
        parent = parents[0] if parents else {}
        apis = owner.get("publishedApisList") or []
        categories = Counter(api.get("category") for api in apis if api.get("category"))
        data_apis = [api for api in apis if api.get("category") == category]
        rows.append(
            {
                "owner_id": owner.get("id"),
                "owner_name": owner.get("name"),
                "owner_slug": owner.get("slugifiedName") or owner.get("username"),
                "owner_username": owner.get("username"),
                "owner_type": owner.get("type"),
                "thumbnail_url": owner.get("thumbnail"),
                "has_thumbnail": bool_int(owner.get("thumbnail")),
                "description_len": len(owner.get("description") or ""),
                "bio_len": len(owner.get("bio") or ""),
                "has_description": bool_int(owner.get("description")),
                "has_bio": bool_int(owner.get("bio")),
                "parent_org_id": parent.get("id"),
                "parent_org_name": parent.get("name"),
                "parent_org_slug": parent.get("slugifiedName"),
                "published_apis_count": len(apis),
                "published_data_apis_count": len(data_apis),
                "published_public_apis_count": sum(api.get("visibility") == "PUBLIC" for api in apis),
                "published_freemium_apis_count": sum(api.get("pricing") == "FREEMIUM" for api in apis),
                "published_free_apis_count": sum(api.get("pricing") == "FREE" for api in apis),
                "published_paid_apis_count": sum(api.get("pricing") == "PAID" for api in apis),
                "published_categories_count": len(categories),
                "published_categories_json": json_compact(categories),
                "raw_file": str(path),
            }
        )
    return rows


VARIABLES = [
    ("rapidapi_static_Data_api_enriched.csv", "api_id", "API 唯一标识，用于连接 API、plan、endpoint 和 owner 表。"),
    ("rapidapi_static_Data_api_enriched.csv", "current_version_id", "当前公开版本 ID，用于连接 playground endpoint 数据。"),
    ("rapidapi_static_Data_api_enriched.csv", "gateway_ids_count", "API 绑定的 gateway 数量，刻画部署/访问入口复杂度。"),
    ("rapidapi_static_Data_api_enriched.csv", "has_terms_of_service", "是否公开服务条款，刻画合同/合规透明度。"),
    ("rapidapi_static_Data_api_enriched.csv", "readme_len", "README 文档文本长度，刻画信息披露和质量可观察性。"),
    ("rapidapi_static_Data_playground_versions.csv", "endpoints_count", "当前版本公开 endpoint 数量，刻画产品功能复杂度。"),
    ("rapidapi_static_Data_playground_versions.csv", "api_version_type", "API 版本类型，例如 REST。"),
    ("rapidapi_static_Data_playground_versions.csv", "auth_type", "访问认证机制类型，刻画使用门槛。"),
    ("rapidapi_static_Data_endpoints.csv", "endpoint_id", "endpoint 唯一标识。"),
    ("rapidapi_static_Data_endpoints.csv", "method", "HTTP 方法，例如 GET/POST。"),
    ("rapidapi_static_Data_endpoints.csv", "route", "endpoint 路径。"),
    ("rapidapi_static_Data_endpoints.csv", "params_count", "endpoint 参数数量，刻画接入复杂度。"),
    ("rapidapi_static_Data_endpoints.csv", "required_params_count", "必填参数数量。"),
    ("rapidapi_static_Data_endpoint_params.csv", "param_condition", "参数是否必填，通常为 REQUIRED 或 OPTIONAL。"),
    ("rapidapi_static_Data_payloads.csv", "payload_kind", "请求、响应或版本级 payload。"),
    ("rapidapi_static_Data_billing_item_endpoints.csv", "billingitem_id", "计费项目 ID，可与 limit 表中的 billingitem_id 连接。"),
    ("rapidapi_static_Data_billing_item_endpoints.csv", "all_endpoints", "该计费项目是否覆盖所有 endpoints。"),
    ("rapidapi_static_Data_owners.csv", "published_data_apis_count", "owner 公开发布的 Data 类 API 数量，刻画供给经验。"),
]


def write_variable_dictionary(root: Path, category: str) -> None:
    rows = [
        {"table": table, "column": column, "meaning": meaning}
        for table, column, meaning in VARIABLES
    ]
    path = root / "data" / f"rapidapi_static_{safe_name(category)}_variable_dictionary.csv"
    save_csv(path, rows)
    md = ["# RapidAPI 静态补充数据变量字典", ""]
    for row in rows:
        md.append(f"- `{row['table']}.{row['column']}`: {row['meaning']}")
    (root / "data" / f"rapidapi_static_{safe_name(category)}_variable_dictionary.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def normalize_all(root: Path, category: str) -> dict[str, Any]:
    targets = load_detail_targets(root, category)
    suffix = safe_name(category)

    api_rows, version_rows, tag_rows, spotlight_rows = normalize_detail_static(root, category)
    playground = normalize_playground(root, category, targets)
    billing_endpoint_rows = normalize_billing_endpoints(root, category)
    owner_rows = normalize_owners(root, category)
    missing_counts = write_missing_lists(root, category, targets)

    outputs: dict[str, list[dict[str, Any]]] = {
        f"rapidapi_static_{suffix}_api_enriched.csv": api_rows,
        f"rapidapi_static_{suffix}_api_versions.csv": version_rows,
        f"rapidapi_static_{suffix}_api_tags.csv": tag_rows,
        f"rapidapi_static_{suffix}_spotlights.csv": spotlight_rows,
        f"rapidapi_static_{suffix}_billing_item_endpoints.csv": billing_endpoint_rows,
        f"rapidapi_static_{suffix}_owners.csv": owner_rows,
    }
    for name, rows in playground.items():
        outputs[f"rapidapi_static_{suffix}_{name}.csv"] = rows

    for filename, rows in outputs.items():
        save_csv(root / "data" / filename, rows)

    raw_base = root / "raw" / "graphql" / f"static_{suffix}"
    summary = {
        "category": category,
        "detail_targets": len(targets),
        "valid_detail_apis": len(api_rows),
        "raw_playground_files": len(list((raw_base / "playground").glob("*.json"))) if (raw_base / "playground").exists() else 0,
        "raw_billing_endpoint_files": len(list((raw_base / "billing_endpoints").glob("*.json"))) if (raw_base / "billing_endpoints").exists() else 0,
        "raw_owner_files": len(list((raw_base / "owner").glob("*.json"))) if (raw_base / "owner").exists() else 0,
        "rows": {filename: len(rows) for filename, rows in outputs.items()},
        "missing": missing_counts,
    }
    write_json(root / "data" / f"rapidapi_static_{suffix}_summary.json", summary)
    write_variable_dictionary(root, category)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="rapidapi_crawl")
    parser.add_argument("--category", default="Data")
    parser.add_argument("--kinds", default="playground,billing_endpoints,owner", help="Comma-separated fetch kinds. Empty with --normalize-only.")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--delay", type=float, default=0.20, help="Per-worker delay after each network request.")
    parser.add_argument("--limit", type=int, default=0, help="Limit fetch targets per kind. 0 means all.")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--normalize-only", action="store_true")
    args = parser.parse_args()

    root = Path(args.root)
    if not args.normalize_only:
        targets = load_detail_targets(root, args.category)
        if not targets:
            raise SystemExit("No detail targets found. Run the base detail crawl first.")
        kinds = [kind.strip() for kind in args.kinds.split(",") if kind.strip()]
        for kind in kinds:
            fetch_kind(root, args.category, kind, targets, args.workers, args.delay, args.limit, args.retry_errors)

    summary = normalize_all(root, args.category)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

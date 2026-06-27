#!/usr/bin/env python3
"""Capture RapidAPI public-page network traffic with real Chrome.

This is a public-page capture only. It does not log in, does not use private
credentials, and writes raw browser-observed GraphQL traffic for reproducibility.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


TARGET_URL = "https://rapidapi.com/search/data"
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def now_ms() -> int:
    return int(time.time() * 1000)


def parse_perf_entry(entry: dict) -> dict | None:
    try:
        msg = json.loads(entry["message"])["message"]
    except Exception:
        return None
    return msg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="rapidapi_crawl")
    ap.add_argument("--url", default=TARGET_URL)
    ap.add_argument("--seconds", type=int, default=55)
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    raw = root / "raw" / "browser"
    data = root / "data"
    raw.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)

    opts = Options()
    opts.binary_location = CHROME_BIN
    if not args.headed:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1440,1200")
    opts.add_argument("--lang=en-US")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})

    driver = webdriver.Chrome(options=opts)
    request_map: dict[str, dict] = {}
    graphql_events: list[dict] = []
    all_events: list[dict] = []

    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.get(args.url)
        started = time.time()
        last_scroll = started

        while time.time() - started < args.seconds:
            try:
                if time.time() - last_scroll > 7:
                    driver.execute_script("window.scrollBy(0, Math.floor(window.innerHeight * 0.8));")
                    last_scroll = time.time()
            except Exception:
                pass

            try:
                entries = driver.get_log("performance")
            except Exception:
                entries = []

            for entry in entries:
                msg = parse_perf_entry(entry)
                if not msg:
                    continue
                method = msg.get("method")
                params = msg.get("params", {})
                if method not in {
                    "Network.requestWillBeSent",
                    "Network.requestWillBeSentExtraInfo",
                    "Network.responseReceived",
                    "Network.responseReceivedExtraInfo",
                    "Network.loadingFinished",
                    "Network.loadingFailed",
                }:
                    continue
                all_events.append(msg)
                rid = params.get("requestId")
                if not rid:
                    continue

                if method == "Network.requestWillBeSent":
                    req = params.get("request", {})
                    url = req.get("url", "")
                    request_map.setdefault(rid, {})["request"] = req
                    if "/graphql" in url or "/gateway/" in url:
                        graphql_events.append(
                            {
                                "ts": now_ms(),
                                "kind": "request",
                                "requestId": rid,
                                "url": url,
                                "method": req.get("method"),
                                "headers": req.get("headers"),
                                "postData": req.get("postData"),
                                "initiator": params.get("initiator"),
                            }
                        )

                elif method == "Network.requestWillBeSentExtraInfo":
                    request_map.setdefault(rid, {})["requestExtra"] = params

                elif method == "Network.responseReceived":
                    resp = params.get("response", {})
                    url = resp.get("url", "")
                    request_map.setdefault(rid, {})["response"] = resp
                    if "/graphql" in url or "/gateway/" in url:
                        body = None
                        body_error = None
                        try:
                            body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": rid})
                        except WebDriverException as exc:
                            body_error = str(exc)[:500]
                        graphql_events.append(
                            {
                                "ts": now_ms(),
                                "kind": "response",
                                "requestId": rid,
                                "url": url,
                                "status": resp.get("status"),
                                "mimeType": resp.get("mimeType"),
                                "headers": resp.get("headers"),
                                "body": body,
                                "bodyError": body_error,
                            }
                        )

                elif method == "Network.responseReceivedExtraInfo":
                    request_map.setdefault(rid, {})["responseExtra"] = params

            time.sleep(1)

        screenshot_path = raw / "search_data_capture.png"
        driver.save_screenshot(str(screenshot_path))
        body_text = ""
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text
        except Exception:
            pass
        (raw / "search_data_body_text.txt").write_text(body_text, encoding="utf-8")

    finally:
        try:
            remaining = driver.get_log("performance")
            for entry in remaining:
                msg = parse_perf_entry(entry)
                if msg:
                    all_events.append(msg)
        except Exception:
            pass
        driver.quit()

    (raw / "performance_events.json").write_text(json.dumps(all_events, ensure_ascii=False, indent=2), encoding="utf-8")
    (data / "browser_graphql_events.json").write_text(json.dumps(graphql_events, ensure_ascii=False, indent=2), encoding="utf-8")

    graphql_requests = [e for e in graphql_events if e.get("kind") == "request"]
    graphql_responses = [e for e in graphql_events if e.get("kind") == "response"]
    summary = {
        "target_url": args.url,
        "captured_events": len(all_events),
        "graphql_or_gateway_events": len(graphql_events),
        "graphql_or_gateway_requests": len(graphql_requests),
        "graphql_or_gateway_responses": len(graphql_responses),
        "request_urls": sorted({e.get("url") for e in graphql_requests if e.get("url")}),
        "response_statuses": sorted({str(e.get("status")) for e in graphql_responses if e.get("status") is not None}),
    }
    (data / "browser_capture_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Bounded, on-demand inputs for the existing SEO audit/report API."""
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from app.gsc_mcp import collect_gsc, private_url
from app.settings import load_config

LATEST_REPORT = None


def error_detail(exc):
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(error_detail(child) for child in exc.exceptions)[:500]
    return f"{type(exc).__name__}: {exc}"[:500]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def competitor_metadata():
    endpoint = private_url(os.environ["COMPETITOR_METADATA_URL"])
    headers = {"Accept": "application/json"}
    key = os.getenv("COMPETITOR_API_KEY")
    if key:
        headers["x-api-key"] = key
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(Request(endpoint + "/api/v1/watch", headers=headers), timeout=20) as response:
        watches = json.load(response)
    if not isinstance(watches, dict):
        raise ValueError("Unexpected changedetection watch response")
    # Return competitor HTML metadata history, never settings or credentials.
    selected = [{"id": ident, **{k: watch.get(k) for k in
                         ("url", "title", "last_checked", "last_changed", "last_error", "last_check_status", "paused")}}
                        for ident, watch in watches.items() if watch.get("url") == "https://bikes2go.co.za/"]
    for watch in selected:
        with opener.open(Request(endpoint + "/api/v1/watch/" + watch["id"] + "/history/latest", headers=headers), timeout=20) as response:
            watch["html_metadata_snapshot"] = response.read(20000).decode("utf-8")
        with opener.open(Request(endpoint + "/api/v1/watch/" + watch["id"] + "/history", headers=headers), timeout=20) as response:
            watch["history"] = json.load(response)
    return {"source": "changedetection", "endpoint": endpoint, "watch_count": len(watches),
            "competitor_watch_count": len(selected), "watches": selected,
            "note": "HTML metadata history; Google displayed snippets and competitor CTR are not measured.",
            "write_actions_taken": 0}


def setup_competitor_watch_once():
    import requests
    endpoint = private_url(os.environ["COMPETITOR_METADATA_URL"])
    with requests.Session() as session:
        session.trust_env = False
        session.headers["x-api-key"] = os.environ["COMPETITOR_API_KEY"]
        response = session.get(endpoint + "/api/v1/watch", timeout=20, allow_redirects=False)
        response.raise_for_status()
        existing = response.json()
        target = "https://bikes2go.co.za/"
        if not any(w.get("url") == target for w in existing.values()):
            response = session.post(endpoint + "/api/v1/watch", json={
                "url": target, "title": "BTSA competitor metadata: Bikes2Go",
                "include_filters": ["xpath://title/text()", "xpath://meta[translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='description']/@content"],
                "fetch_backend": "html_requests", "notification_urls": [], "notification_muted": True,
                "time_between_check": {"weeks": 1}, "time_between_check_use_default": False,
            }, timeout=30, allow_redirects=False)
            response.raise_for_status()
            print("COMPETITOR_WATCH_CREATED " + json.dumps(response.json()), flush=True)
            # One bounded wait for the initial asynchronous fetch, no polling loop.
            time.sleep(20)
        print("COMPETITOR_METADATA_ACCEPTANCE " + json.dumps(competitor_metadata()), flush=True)


async def weekly_signals():
    global LATEST_REPORT
    config = load_config()
    report = {"collected_at": datetime.now(timezone.utc).isoformat(),
              "write_actions_taken": 0, "paid_api_calls": 0,
              "ai_referrals": {"status": "unavailable", "reason": "No verified analytics/referrer source configured; GSC does not measure AI referrals."}}
    if os.getenv("AI_REFERRAL_BASELINE_JSON"):
        report["ai_referrals"] = json.loads(os.environ["AI_REFERRAL_BASELINE_JSON"])
    try:
        report["gsc"] = await collect_gsc(config["gsc_site"], config["market"]["gsc_country"])
    except Exception as exc:
        report["gsc"] = {"status": "error", "endpoint": os.getenv("GSC_MCP_URL"), "error": error_detail(exc)}
    try:
        report["competitors"] = await asyncio.to_thread(competitor_metadata)
    except Exception as exc:
        report["competitors"] = {"status": "error", "endpoint": os.getenv("COMPETITOR_METADATA_URL"), "error": error_detail(exc)}
    report["verified"] = "properties" in report["gsc"] and report["competitors"].get("competitor_watch_count", 0) > 0
    LATEST_REPORT = report
    return report


def acceptance_once():
    if os.getenv("COMPETITOR_SETUP_ONCE") == "1":
        try:
            setup_competitor_watch_once()
        except Exception as exc:
            print("COMPETITOR_SETUP_FAILED " + error_detail(exc), flush=True)
    report = asyncio.run(weekly_signals())
    gsc = report["gsc"]
    summary = {**report, "gsc": {**gsc}}
    for name in ("overview", "analytics", "mobile_organic_baseline"):
        if name in gsc:
            summary["gsc"][name] = {**gsc[name], "rows": gsc[name].get("rows", [])[:5],
                                     "returned_row_count": len(gsc[name].get("rows", []))}
    print("SEO_SIGNALS_ACCEPTANCE " + json.dumps(summary), flush=True)

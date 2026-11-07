"""Bounded, on-demand inputs for the existing SEO audit/report API."""
import asyncio
import json
import os
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
    # Return only monitoring metadata, never settings, credentials or snapshot content.
    return {"source": "changedetection", "endpoint": endpoint, "watch_count": len(watches),
            "watches": [{"id": ident, **{k: watch.get(k) for k in
                         ("url", "title", "last_checked", "last_changed", "last_error", "last_check_status", "paused")}}
                        for ident, watch in watches.items()], "write_actions_taken": 0}


async def weekly_signals():
    global LATEST_REPORT
    config = load_config()
    report = {"collected_at": datetime.now(timezone.utc).isoformat(),
              "write_actions_taken": 0, "paid_api_calls": 0,
              "ai_referrals": {"status": "unavailable", "reason": "No verified analytics/referrer source configured; GSC does not measure AI referrals."}}
    try:
        report["gsc"] = await collect_gsc(config["gsc_site"], config["market"]["gsc_country"])
    except Exception as exc:
        report["gsc"] = {"status": "error", "endpoint": os.getenv("GSC_MCP_URL"), "error": error_detail(exc)}
    try:
        report["competitors"] = await asyncio.to_thread(competitor_metadata)
    except Exception as exc:
        report["competitors"] = {"status": "error", "endpoint": os.getenv("COMPETITOR_METADATA_URL"), "error": error_detail(exc)}
    report["verified"] = "properties" in report["gsc"] and "watch_count" in report["competitors"]
    LATEST_REPORT = report
    return report


def acceptance_once():
    report = asyncio.run(weekly_signals())
    gsc = report["gsc"]
    summary = {**report, "gsc": {**gsc}}
    for name in ("overview", "analytics", "mobile_organic_baseline"):
        if name in gsc:
            summary["gsc"][name] = {**gsc[name], "rows": gsc[name].get("rows", [])[:5],
                                     "returned_row_count": len(gsc[name].get("rows", []))}
    print("SEO_SIGNALS_ACCEPTANCE " + json.dumps(summary), flush=True)

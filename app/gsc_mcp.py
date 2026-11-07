"""Read-only Search Console collection through the private Railway MCP service."""
import asyncio
import json
import os
from datetime import date, timedelta
from urllib.parse import urlsplit


def private_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or not parsed.hostname
            or not parsed.hostname.endswith(".railway.internal")
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Connector URL must use a Railway private HTTP hostname")
    return value.rstrip("/")


async def read_tool(session, name, arguments):
    if name not in {"list_properties", "get_search_analytics", "get_advanced_search_analytics"}:
        raise ValueError("Only read-only GSC tools are allowed")
    result = await session.call_tool(name, arguments)
    text = "\n".join(item.text for item in result.content if item.type == "text")
    if result.isError:
        raise RuntimeError("GSC MCP tool failed: " + name + ": " + text[:500])
    if text.startswith("No search analytics data found"):
        return {"rows": [], "no_data": True}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise RuntimeError("GSC returned a non-JSON response: " + text[:300]) from exc
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected GSC response shape")
    return data


async def collect_gsc(site, country="zaf", days=28, row_limit=250):
    # Import lazily so the existing direct OAuth path remains independent.
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    endpoint = private_url(os.environ["GSC_MCP_URL"])
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=days - 1)
    async with asyncio.timeout(120):
        async with sse_client(endpoint, timeout=15, sse_read_timeout=45) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                properties = await read_tool(session, "list_properties", {})
                if site not in {p.get("site_url") for p in properties.get("properties", [])}:
                    raise ValueError("Configured GSC property was not returned by list_properties")
                # Acceptance includes the documented basic analytics tool, then
                # explicit final-data windows and filters for reporting/baselines.
                overview = await read_tool(session, "get_search_analytics", {
                    "site_url": site, "days": days, "dimensions": "query,page", "row_limit": 5,
                })
                if os.getenv("SEO_SIGNALS_ACCEPTANCE") == "1":
                    print("GSC_AUTHENTICATED_ACCEPTANCE " + json.dumps({
                        "endpoint": endpoint, "properties": properties, "overview": overview,
                        "write_actions_taken": 0,
                    }), flush=True)
                params = {
                    "site_url": site, "start_date": start.isoformat(), "end_date": end.isoformat(),
                    "dimensions": "query,page", "search_type": "WEB", "row_limit": row_limit,
                    "filter_dimension": "country", "filter_operator": "equals",
                    "filter_expression": country, "data_state": "final",
                }
                analytics = await read_tool(session, "get_advanced_search_analytics", params)
                previous_end = start - timedelta(days=1)
                previous = await read_tool(session, "get_advanced_search_analytics", {
                    **params, "start_date": (previous_end - timedelta(days=days - 1)).isoformat(),
                    "end_date": previous_end.isoformat(),
                })
                # FastMCP coerces the upstream Optional[str] JSON filters into a
                # list, which fails validation. Use the scalar country filter,
                # group by device, and select MOBILE rows without another API.
                mobile = await read_tool(session, "get_advanced_search_analytics", {
                    **params, "dimensions": "query,page,device",
                })
                mobile["rows_before_device_selection"] = len(mobile["rows"])
                mobile["rows"] = [r for r in mobile["rows"] if str(r.get("device", "")).upper() == "MOBILE"]
                mobile["device_selected_locally"] = "MOBILE"
                if os.getenv("SEO_SIGNALS_ACCEPTANCE") == "1":
                    for offset in range(0, len(mobile["rows"]), 25):
                        print("GSC_MOBILE_BASELINE " + json.dumps({
                            "site": site, "country": country, "date_range": mobile.get("date_range"),
                            "offset": offset, "rows": mobile["rows"][offset:offset + 25],
                        }), flush=True)
    return {
        "source": "private_gsc_mcp", "endpoint": endpoint, "site": site, "country": country,
        "properties": properties, "overview": overview, "analytics": analytics,
        "previous_analytics": previous, "mobile_organic_baseline": mobile, "row_limit": row_limit,
        "baseline_note": "GSC average positions, not live SERP ranks. Mobile rows are selected from capped country query/page/device results; capped rows are not site totals.",
        "paid_api_calls": 0, "write_actions_taken": 0,
    }


def fetch_mcp_queries(site, country, days, row_limit):
    data = asyncio.run(collect_gsc(site, country, days, row_limit))
    return [{**row, "keyword": row.get("query", "")} for row in data["analytics"]["rows"]]

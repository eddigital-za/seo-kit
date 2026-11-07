"""Email presentation and a narrowly scoped reporting credential."""
import csv
import io
import json
import hmac
import os
from html import escape
from urllib.parse import urlsplit


def valid_report_key(value):
    expected = os.getenv("REPORT_API_KEY", "")
    return bool(expected and value and hmac.compare_digest(expected, value))


def ga4_referrals(payload):
    from datetime import date
    raw = payload["ga4_report"]
    if isinstance(raw, str):
        raw = json.loads(raw)
    expected = ["sessions", "engagedSessions", "keyEvents"]
    if [h["name"] for h in raw["metricHeaders"]] != expected:
        raise ValueError("Unexpected GA4 metrics")
    start, end = payload["start_date"], payload["end_date"]
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError("Invalid date range")
    rows = raw.get("rows", [])
    if len(rows) > 1 or (not rows and raw.get("rowCount", 0) != 0):
        raise ValueError("Expected an aggregate GA4 result")
    values = [float(v["value"]) for v in rows[0]["metricValues"]] if rows else [0, 0, 0]
    if len(values) != 3 or any(v < 0 or not __import__("math").isfinite(v) for v in values):
        raise ValueError("Invalid GA4 values")
    return {"status": "live_report", "source": "GA4 via Make", "property": "properties/495141074",
            "date_range": {"start": start, "end": end}, "sessions": values[0],
            "engaged_sessions": values[1], "key_events": values[2], "metadata": raw.get("metadata", {}),
            "note": "Selected AI session sources only; does not measure citations or zero-click visibility."}


def render_email(report):
    def text(value):
        return escape(str(value if value is not None else "—"))

    def page_name(url):
        parsed = urlsplit(url or "")
        if parsed.path in ("", "/"):
            return "Homepage" + (" · alternate link" if parsed.query else "")
        return parsed.path.strip("/").replace("-", " ").capitalize()

    def short_date(value):
        try:
            from datetime import date
            return date.fromisoformat(value).strftime("%d %b").lstrip("0")
        except (TypeError, ValueError):
            return "Not available"

    plan = report.get("commercial_plan", {})
    gsc = report.get("gsc", {})
    window = gsc.get("analytics", {}).get("date_range", {})
    opportunities = report.get("top_opportunities", [])
    lead = opportunities[0] if opportunities else None
    ai = report.get("ai_referrals", {})
    ai_window = ai.get("date_range", {})
    ai_label = "Fresh GA4 report: " if ai.get("status") == "live_report" else "Dated baseline: "
    ai_note = ". Refreshed with this weekly report." if ai.get("status") == "live_report" else ". Automatic refresh pending."
    competitors = report.get("competitors", {})
    watches = competitors.get("watches", [])
    muted = 'font-size:13px;line-height:1.5;color:#64748b'
    parts = ['<!doctype html><html><body style="margin:0;background:#eef3f7;font-family:Arial,sans-serif;color:#193247">',
             '<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:24px 10px">',
             '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:640px;background:white;border-radius:14px;overflow:hidden">',
             '<tr><td style="padding:26px;background:#12374d;color:white"><div style="font-size:12px;letter-spacing:2px;color:#a9dce4">BIKE TRANSPORT SOUTH AFRICA</div>',
             '<h1 style="font-size:27px;margin:10px 0 8px">Your SEO week, at a glance</h1>',
             '<div style="font-size:14px;color:#cfdee7">Google data: ' + text(short_date(window.get("start"))) + '–' + text(short_date(window.get("end"))) + ' · South Africa</div></td></tr>',
             '<tr><td style="padding:26px">']
    if gsc.get("status") == "error" or not lead:
        parts.append('<div style="background:#fff3dc;padding:18px;border-radius:10px"><strong>Search data needs attention</strong><p style="margin-bottom:0">Collection failed or no opportunities were returned. Check the data feed before making a website decision.</p></div>')
    else:
        headline = "People see this page, but aren't clicking" if lead.get("clicks") == 0 else "Your clearest search opportunity"
        parts.append('<h2 style="font-size:23px;line-height:1.25;margin:0 0 10px">' + headline + '</h2><p style="font-size:15px;line-height:1.5;margin:0 0 18px">For <strong>“' + text(lead.get("keyword", lead.get("query"))) + '”</strong> · ' + text(page_name(lead.get("page"))) + '</p>')
        parts.append('<table role="presentation" width="100%" cellspacing="0" cellpadding="12" style="background:#edf6f8;border-radius:10px;text-align:center"><tr>')
        for value, label in [(lead.get("impressions"), "Seen on Google"), (lead.get("clicks"), "Clicks to your site"), (lead.get("position"), "Avg. position")]:
            parts.append('<td width="33%"><strong style="font-size:29px;color:#0c6577">' + text(value) + '</strong><div style="font-size:12px;line-height:1.4;margin-top:6px">' + label + '</div></td>')
        parts.append('</tr></table><p style="' + muted + ';margin:8px 0 20px">These figures describe this search phrase and page, not all website traffic.</p>')
        parts.append('<div style="background:#fff4d9;border-left:4px solid #e4a526;padding:16px 18px"><strong style="font-size:13px;color:#815700">ONE PRIORITY</strong><p style="font-size:17px;line-height:1.45;margin:8px 0">' + text(plan.get("recommended_action", "Review this page and prepare one improvement proposal.")) + '</p><div style="font-size:13px;color:#705b32">Your approval comes before any website edit.</div></div>')
    if plan.get("markets"):
        parts.append('<h2 style="font-size:18px;margin:25px 0 12px">Five-market coverage</h2>')
        parts.append('<p style="' + muted + '">' + ' · '.join(text(m["market"]) + ': ' + text(m["observed_commercial_queries"]) + ' searches observed' for m in plan["markets"]) + '</p>')
        parts.append('<p style="' + muted + '">Full search-to-page map attached. City-specific mobile top-10 tracking is not connected; Google mobile averages are included instead.</p>')
    if lead and lead.get("previous_impressions") is not None:
        parts.append('<p style="font-size:14px;line-height:1.5"><strong>Previous 28 days → latest 28 days</strong><br>Times seen: ' + text(lead["previous_impressions"]) + ' → ' + text(lead.get("impressions")) + ' · Clicks: ' + text(lead["previous_clicks"]) + ' → ' + text(lead.get("clicks")) + '. Same search and URL; this does not prove an SEO change caused the difference.</p>')
    if len(opportunities) > 1:
        parts.append('<h2 style="font-size:18px;margin:25px 0 12px">Also worth watching</h2><table width="100%" cellspacing="0" cellpadding="9" style="font-size:14px;border-collapse:collapse"><tr style="background:#f1f5f9;text-align:left"><th>Search / page</th><th>Seen</th><th>Clicks</th></tr>')
        for row in opportunities[1:4]:
            parts.append('<tr><td style="border-bottom:1px solid #e5edf2">' + text(row.get("keyword", row.get("query"))) + '<br><span style="' + muted + '">' + text(page_name(row.get("page"))) + '</span></td><td style="border-bottom:1px solid #e5edf2">' + text(row.get("impressions")) + '</td><td style="border-bottom:1px solid #e5edf2">' + text(row.get("clicks")) + '</td></tr>')
        parts.append('</table>')
    parts.append('<h2 style="font-size:18px;margin:25px 0 12px">Other signals</h2>')
    if competitors.get("status") == "error":
        competitor_line = "Competitor check unavailable — no change claim made."
    elif watches:
        changed = any(len(w.get("history", {})) > 1 for w in watches)
        competitor_line = "A competitor page has recorded edits. Review its history." if changed else "No competitor page change recorded since tracking began."
    else:
        competitor_line = "No competitor page checked yet."
    parts.append('<p style="font-size:14px;line-height:1.5"><strong>Competitors</strong><br>' + competitor_line + '</p>')
    parts.append('<p style="font-size:14px;line-height:1.5"><strong>AI referrals</strong><br>' + text(ai.get("sessions", "Unavailable")) + ' visits · ' + text(ai.get("engaged_sessions", "Unavailable")) + ' engaged visits · ' + text(ai.get("key_events", "Unavailable")) + ' recorded goals.<br><span style="' + muted + '">' + ai_label + text(short_date(ai_window.get("start"))) + '–' + text(short_date(ai_window.get("end"))) + ai_note + '</span></p>')
    parts.append('<p style="font-size:14px;line-height:1.5"><strong>Website changes</strong><br>None made by this report.</p></td></tr>')
    parts.append('<tr><td style="padding:18px 26px;background:#f5f8fa;' + muted + '">Position is a Google average; lower is better. Comparisons use matching search phrases and URLs. Search figures cover the listed phrases, not site totals.</td></tr></table></td></tr></table></body></html>')
    sheet = io.StringIO(newline="")
    writer = csv.writer(sheet)
    writer.writerow(["Search", "Intent", "Markets", "Proposed page", "Page match", "Observed URL", "Times seen", "Clicks", "CTR", "Average position", "Monthly search estimate"])
    for entry in plan.get("keyword_to_page_map", []):
        for row in entry["observations"] or [{}]:
            values = [entry["query"], entry["intent"], ", ".join(entry["markets"]), entry["expected_page"], entry["mapping_status"], row.get("page"), row.get("impressions"), row.get("clicks"), row.get("ctr"), row.get("position"), entry["monthly_search_estimate"]]
            writer.writerow([("'" + str(v)) if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v for v in values])
    return {"email_subject": "BTSA SEO at a glance — " + report.get("collected_at", "")[:10],
            "email_html": "".join(parts), "keyword_map_csv": sheet.getvalue(), "keyword_map_json": json.dumps(plan, ensure_ascii=False, indent=2)}

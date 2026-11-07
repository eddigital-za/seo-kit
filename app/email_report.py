"""Email presentation and a narrowly scoped reporting credential."""
import hmac
import os
from html import escape
from urllib.parse import urlsplit


def valid_report_key(value):
    expected = os.getenv("REPORT_API_KEY", "")
    return bool(expected and value and hmac.compare_digest(expected, value))


def render_email(report):
    def text(value):
        return escape(str(value if value is not None else "—"))

    def page_name(url):
        parsed = urlsplit(url or "")
        if parsed.path in ("", "/"):
            return "Homepage" + (" · tagged URL" if parsed.query else "")
        return parsed.path.strip("/").replace("-", " ").capitalize()

    def short_date(value):
        try:
            from datetime import date
            return date.fromisoformat(value).strftime("%d %b").lstrip("0")
        except (TypeError, ValueError):
            return "Not available"

    gsc = report.get("gsc", {})
    window = gsc.get("analytics", {}).get("date_range", {})
    opportunities = report.get("top_opportunities", [])
    lead = opportunities[0] if opportunities else None
    ai = report.get("ai_referrals", {})
    ai_window = ai.get("date_range", {})
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
        parts.append('<div style="background:#fff4d9;border-left:4px solid #e4a526;padding:16px 18px"><strong style="font-size:13px;color:#815700">ONE PRIORITY</strong><p style="font-size:17px;line-height:1.45;margin:8px 0">Review how this page answers “' + text(lead.get("keyword", lead.get("query"))) + '”, then propose one improvement.</p><div style="font-size:13px;color:#705b32">Your approval comes before any website edit.</div></div>')
    if opportunities:
        parts.append('<h2 style="font-size:18px;margin:25px 0 12px">Three searches to watch</h2><table width="100%" cellspacing="0" cellpadding="9" style="font-size:14px;border-collapse:collapse"><tr style="background:#f1f5f9;text-align:left"><th>Search / page</th><th>Seen</th><th>Clicks</th></tr>')
        for row in opportunities[:3]:
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
    parts.append('<p style="font-size:14px;line-height:1.5"><strong>AI referrals</strong><br>' + text(ai.get("sessions", "Unavailable")) + ' visits · ' + text(ai.get("engaged_sessions", "Unavailable")) + ' engaged visits · ' + text(ai.get("key_events", "Unavailable")) + ' recorded goals.<br><span style="' + muted + '">Dated baseline: ' + text(short_date(ai_window.get("start"))) + '–' + text(short_date(ai_window.get("end"))) + '. Automatic refresh pending.</span></p>')
    parts.append('<p style="font-size:14px;line-height:1.5"><strong>Website changes</strong><br>None made by this report.</p></td></tr>')
    parts.append('<tr><td style="padding:18px 26px;background:#f5f8fa;' + muted + '">Position is a Google average; lower is better. No week-to-week improvement is claimed yet. Search figures cover the listed phrases, not site totals.</td></tr></table></td></tr></table></body></html>')
    return {"email_subject": "BTSA SEO at a glance — " + report.get("collected_at", "")[:10],
            "email_html": "".join(parts)}

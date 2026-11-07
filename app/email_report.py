"""Email presentation and a narrowly scoped reporting credential."""
import hmac
import os
from html import escape


def valid_report_key(value):
    expected = os.getenv("REPORT_API_KEY", "")
    return bool(expected and value and hmac.compare_digest(expected, value))


def render_email(report):
    def text(value):
        return escape(str(value))

    gsc = report.get("gsc", {})
    analytics = gsc.get("analytics", {})
    window = analytics.get("date_range", {})
    mobile = gsc.get("mobile_organic_baseline", {})
    parts = ['<html><body style="font:16px Arial,sans-serif;color:#173042;max-width:760px;margin:24px auto;padding:16px">',
             '<h1 style="color:#125b78">BTSA weekly SEO report</h1>',
             '<p>One evidence-led action for the week. Website edits require your approval.</p>',
             '<h2>Search visibility</h2>']
    if gsc.get("status") == "error":
        parts.append('<p>Collection failed: ' + text(gsc.get("error")) + '</p>')
    else:
        parts.append('<p>South Africa · Google web search · final data: ' + text(window.get("start")) + ' to ' + text(window.get("end")) +
                     '. Returned ' + text(len(analytics.get("rows", []))) + ' query/page rows and ' + text(len(mobile.get("rows", []))) + ' mobile rows.</p>')
    parts.append('<h2>Leading opportunities</h2><table cellpadding="9" cellspacing="0" border="1" style="border-collapse:collapse;border-color:#d8e4ea"><tr><th>Query / page</th><th>Clicks</th><th>Impressions</th><th>CTR</th><th>Avg position</th></tr>')
    for row in report.get("top_opportunities", [])[:5]:
        parts.append('<tr><td>' + text(row.get("keyword")) + '<br><small>' + text(row.get("page")) + '</small></td><td>' + text(row.get("clicks")) + '</td><td>' + text(row.get("impressions")) + '</td><td>' + text(round(100 * row.get("ctr", 0), 2)) + '%</td><td>' + text(row.get("position")) + '</td></tr>')
    parts.append('</table><p><strong>Next action:</strong> ' + text(report.get("recommended_next_action", {}).get("action", "Review collection failures before drawing conclusions.")) + '</p>')
    parts.append('<h2>Competitor metadata</h2>')
    competitors = report.get("competitors", {})
    if competitors.get("status") == "error":
        parts.append('<p>Collection failed: ' + text(competitors.get("error")) + '</p>')
    for watch in competitors.get("watches", []):
        meta = watch.get("live_html_metadata", {})
        parts.append('<p><strong>' + text(watch.get("url")) + '</strong><br>Title: ' + text(meta.get("title")) + '<br>Meta description: ' + text(meta.get("meta_description") if meta.get("description_status") == "present" else "absent in the fetched HTML") + '<br>Retained changedetection snapshots: ' + text(len(watch.get("history", {}))) + '.</p>')
    ai = report.get("ai_referrals", {})
    ai_window = ai.get("date_range", {})
    parts.append('<h2>AI referrals</h2><p>' + text(ai.get("sessions", "Unavailable")) + ' sessions · ' + text(ai.get("engaged_sessions", "Unavailable")) + ' engaged · ' + text(ai.get("key_events", "Unavailable")) + ' key events.</p><p><strong>Dated baseline, not a fresh weekly GA4 feed:</strong> ' + text(ai_window.get("start", "unknown")) + ' to ' + text(ai_window.get("end", "unknown")) + '.</p>')
    parts.append('<hr><p style="font-size:13px;color:#536873">GSC positions are observed averages, not a controlled mobile top-10 SERP snapshot. Query rows exclude anonymised queries; do not sum these rows as site totals. HTML edits and Google snippet changes are separate signals. Zero paid API calls; no website changes.</p><p style="font-size:13px">Collected: ' + text(report.get("collected_at")) + '</p></body></html>')
    return {"email_subject": "BTSA SEO weekly report — " + report.get("collected_at", "")[:10],
            "email_html": "".join(parts)}

import os
import subprocess
from pathlib import Path
from urllib.parse import urlencode

import requests


def _result(process):
    return {
        "ok": process.returncode == 0,
        "exit_code": process.returncode,
        "stdout": process.stdout[-30000:],
        "stderr": process.stderr[-10000:],
    }


def run_crawl(domain: str, limit: int):
    kit = Path(os.getenv("SEO_KIT_DIR", "/app"))
    script = kit / "skills/seo-health/scripts/health-crawl.sh"
    p = subprocess.run(
        ["bash", str(script), domain, "--limit", str(limit)],
        cwd="/app",
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    return _result(p)


def _metric(audits, key):
    audit = audits.get(key, {})
    return {
        "value_ms": audit.get("numericValue"),
        "display": audit.get("displayValue"),
        "score": audit.get("score"),
    }


def run_pagespeed(url: str, strategy: str):
    api_key = os.getenv("PAGESPEED_API_KEY", "").strip()
    if not api_key:
        return {"ok": False, "url": url, "strategy": strategy, "error": "PAGESPEED_API_KEY is not configured"}

    params = [
        ("url", url),
        ("strategy", strategy),
        ("category", "performance"),
        ("category", "seo"),
        ("category", "accessibility"),
        ("key", api_key),
    ]
    endpoint = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed?" + urlencode(params)

    try:
        response = requests.get(endpoint, timeout=120, headers={"User-Agent": "BTSA-SEO-Agent/1.0"})
        data = response.json()
    except Exception as exc:
        return {"ok": False, "url": url, "strategy": strategy, "error": str(exc)}

    if response.status_code != 200:
        return {
            "ok": False,
            "url": url,
            "strategy": strategy,
            "status_code": response.status_code,
            "error": data.get("error", {}).get("message", "PageSpeed request failed"),
        }

    lighthouse = data.get("lighthouseResult", {})
    categories = lighthouse.get("categories", {})
    audits = lighthouse.get("audits", {})
    field = data.get("loadingExperience", {}).get("metrics", {})

    def category_score(name):
        value = categories.get(name, {}).get("score")
        return round(value * 100) if isinstance(value, (int, float)) else None

    opportunities = []
    for audit_id, audit in audits.items():
        details = audit.get("details") or {}
        savings = details.get("overallSavingsMs")
        if isinstance(savings, (int, float)) and savings > 0:
            opportunities.append({
                "id": audit_id,
                "title": audit.get("title"),
                "savings_ms": round(savings),
                "display": audit.get("displayValue"),
            })
    opportunities.sort(key=lambda item: item["savings_ms"], reverse=True)

    return {
        "ok": True,
        "url": url,
        "strategy": strategy,
        "scores": {
            "performance": category_score("performance"),
            "seo": category_score("seo"),
            "accessibility": category_score("accessibility"),
        },
        "lab": {
            "fcp": _metric(audits, "first-contentful-paint"),
            "lcp": _metric(audits, "largest-contentful-paint"),
            "speed_index": _metric(audits, "speed-index"),
            "tbt": _metric(audits, "total-blocking-time"),
            "cls": {
                "value": audits.get("cumulative-layout-shift", {}).get("numericValue"),
                "display": audits.get("cumulative-layout-shift", {}).get("displayValue"),
                "score": audits.get("cumulative-layout-shift", {}).get("score"),
            },
            "ttfb": _metric(audits, "server-response-time"),
        },
        "field": {
            "lcp_ms": field.get("LARGEST_CONTENTFUL_PAINT_MS", {}).get("percentile"),
            "inp_ms": field.get("INTERACTION_TO_NEXT_PAINT", {}).get("percentile"),
            "cls": (field.get("CUMULATIVE_LAYOUT_SHIFT_SCORE", {}).get("percentile") / 100)
                if isinstance(field.get("CUMULATIVE_LAYOUT_SHIFT_SCORE", {}).get("percentile"), (int, float)) else None,
            "fcp_ms": field.get("FIRST_CONTENTFUL_PAINT_MS", {}).get("percentile"),
            "ttfb_ms": field.get("EXPERIMENTAL_TIME_TO_FIRST_BYTE", {}).get("percentile"),
            "overall_category": data.get("loadingExperience", {}).get("overall_category"),
        },
        "opportunities": opportunities[:10],
        "analysis_timestamp": lighthouse.get("fetchTime"),
    }


def run_speed(site_url: str):
    return {
        "mobile": run_pagespeed(site_url, "mobile"),
        "desktop": run_pagespeed(site_url, "desktop"),
    }


def run_speed_batch(urls):
    results = []
    for url in urls:
        results.append({
            "url": url,
            "mobile": run_pagespeed(url, "mobile"),
            "desktop": run_pagespeed(url, "desktop"),
        })
    return results

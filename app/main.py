import os
from fastapi import Depends, FastAPI, HTTPException
from app.settings import load_config
from app.security import require_agent_key
from app.gsc import fetch_queries, GSCConfigurationError
from app.scoring import score_gsc_rows
from app.seo_kit import run_crawl, run_speed

app = FastAPI(title="BTSA SEO Agent", version="0.1.0")

@app.get("/health")
def health():
    config = load_config()
    return {
        "ok": True,
        "service": "btsa-seo-agent",
        "domain": config["domain"],
        "market": config["market"]["country_name"],
        "wordpress_write_enabled": config["guardrails"]["wordpress_write_enabled"],
    }

@app.post("/audit/technical", dependencies=[Depends(require_agent_key)])
def audit_technical():
    config = load_config()
    return {
        "domain": config["domain"],
        "crawl": run_crawl(config["domain"], config["seo"]["crawl_limit"]),
        "speed": run_speed(config["site_url"]),
        "write_actions_taken": 0,
    }

@app.post("/audit/gsc", dependencies=[Depends(require_agent_key)])
def audit_gsc():
    config = load_config()
    try:
        rows = fetch_queries(
            site=config["gsc_site"],
            country=config["market"]["gsc_country"],
            days=28,
            row_limit=250,
        )
    except GSCConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GSC request failed: {exc}")

    opportunities = score_gsc_rows(
        rows,
        strike_min=config["seo"]["strike_zone_min"],
        strike_max=config["seo"]["strike_zone_max"],
    )
    return {
        "site": config["gsc_site"],
        "country": config["market"]["gsc_country"],
        "rows": len(rows),
        "top_opportunities": opportunities[:30],
        "write_actions_taken": 0,
    }

@app.post("/audit/full", dependencies=[Depends(require_agent_key)])
def audit_full():
    config = load_config()
    return {
        "brand": config["brand"],
        "technical": audit_technical(),
        "gsc": audit_gsc(),
        "guardrails": config["guardrails"],
    }

@app.post("/diagnostic/crawl-once")
def diagnostic_crawl_once():
    if os.getenv("DIAGNOSTIC_MODE") != "1":
        raise HTTPException(status_code=404, detail="Not found")
    config = load_config()
    return {
        "domain": config["domain"],
        "crawl": run_crawl(config["domain"], min(config["seo"]["crawl_limit"], 50)),
        "write_actions_taken": 0,
    }

import os
import threading
import base64
import requests
from fastapi import Depends, FastAPI, HTTPException
from contextlib import asynccontextmanager
from pydantic import BaseModel
from app.settings import load_config
from app.security import require_agent_key
from app.gsc import fetch_queries, GSCConfigurationError
from app.scoring import score_gsc_rows
from app.seo_kit import run_crawl, run_speed, run_speed_batch
from app.wordpress import connection_check, update_content, replace_content_text, replace_elementor_text, rankmath_update_canonical, inspect_elementor_assets, get_estimator_html, optimize_estimator_widget, optimize_post_content_estimator, elementor_clear_cache, restore_estimator_from_backup, capture_estimator_backup, wp_rocket_purge_all, WordPressConfigurationError

def _bootstrap_audit():
    try:
        wp = connection_check()
        print(f"SEO_BOOTSTRAP wp_authenticated={wp['authenticated']} pages_read={len(wp['recent_pages'])}", flush=True)
        if os.getenv("SEO_BOOTSTRAP_FIX", "0") == "1":
            repairs = {
                1078: {
                    "Deliveries across Gauteng include:": "Deliveries across KwaZulu-Natal include:",
                    "JHB ⇄ Cape Town Transport": "Durban ⇄ Cape Town Transport",
                },
                1217: {"ober 160": "over 160"},
            }
            for page_id, replacements in repairs.items():
                content_result = replace_content_text(page_id, replacements, dry_run=False)
                elementor_result = replace_elementor_text(page_id, replacements, dry_run=False)
                print(
                    f"WP_REPAIR page={page_id} content_changed={content_result.get('write_actions_taken', 0) > 0} "
                    f"elementor_changed={elementor_result.get('write_actions_taken', 0) > 0} "
                    f"matches={sorted(set(content_result.get('matches', []) + elementor_result.get('matches', [])))}",
                    flush=True,
                )
            canonicals = {
                1211: "https://biketransport.co.za/durban-johannesburg-motorcycle-transport/",
                1036: "https://biketransport.co.za/johannesburg-and-cape-town/",
            }
            for page_id, canonical in canonicals.items():
                result = rankmath_update_canonical(page_id, canonical)
                print(f"CANONICAL_REPAIR page={page_id} canonical={canonical} writes={result['write_actions_taken']}", flush=True)
        for object_id in (1078, 1217, 1211, 1036):
            snap = update_content("pages", object_id, {}, dry_run=True)
            before = snap["before"]
            raw = (before.get("content") or {}).get("raw", "")
            meta = before.get("meta") or {}
            print(
                f"WP_AUDIT page={object_id} slug={before.get('slug')} "
                f"gauteng_kzn_error={'Deliveries across Gauteng' in raw} "
                f"wrong_jhb_ct_cta={'JHB' in raw and 'Cape Town Transport' in raw} "
                f"ober_typo={'ober 160' in raw} meta_keys={sorted(meta.keys())}",
                flush=True,
            )
        if os.getenv("SEO_RESTORE_ESTIMATOR", "0") == "1":
            backup_path = "backups/estimator-known-working-2026-10-19.b64"
            for page_id in (275, 1211, 1078, 1036, 1217, 1234):
                result = restore_estimator_from_backup(page_id, backup_path)
                print(
                    f"ESTIMATOR_RESTORE page={page_id} writes={result['write_actions_taken']} "
                    f"elementor={result['elementor_replacements']} content={result['content_changed']} "
                    f"babel={result['babel_restored']} exact={result['verified_exact_backup']} "
                    f"backup={result['backup_ref']}",
                    flush=True,
                )
        if os.getenv("SEO_OPTIMIZE_ESTIMATOR", "0") == "1":
            for page_id in (275, 1211, 1078, 1036, 1217, 1234):
                elementor_result = optimize_estimator_widget(page_id, dry_run=False)
                content_result = optimize_post_content_estimator(page_id, dry_run=False)
                print(
                    f"ESTIMATOR_OPTIMIZE page={page_id} "
                    f"elementor_changed={elementor_result.get('write_actions_taken', 0) > 0} "
                    f"content_changed={content_result.get('write_actions_taken', 0) > 0} "
                    f"elementor_babel_removed={elementor_result.get('babel_removed')} "
                    f"content_babel_removed={content_result.get('babel_removed')}",
                    flush=True,
                )
        if os.getenv("SEO_CLEAR_ELEMENTOR_CACHE", "0") == "1":
            try:
                cache_result = elementor_clear_cache()
                print(f"ELEMENTOR_CACHE_CLEAR cleared={cache_result['cleared']} status={cache_result['status']}", flush=True)
            except Exception as exc:
                print(f"ELEMENTOR_CACHE_CLEAR_FAILED type={type(exc).__name__} detail={repr(exc)}", flush=True)
        if os.getenv("SEO_BACKUP_ESTIMATOR", "0") == "1":
            backup = capture_estimator_backup((275, 1211, 1078, 1036, 1217, 1234))
            print(
                f"ESTIMATOR_BACKUP path={backup['backup_path']} pages={backup['pages']} "
                f"bytes={backup['bytes']} verified={backup['verified']}",
                flush=True,
            )
        if os.getenv("SEO_PURGE_WPROCKET", "0") == "1":
            purge = wp_rocket_purge_all()
            print(f"WPROCKET_PURGE {purge}", flush=True)
        if os.getenv("SEO_RENDER_VERIFY", "0") == "1":
            for url in BTSA_PERFORMANCE_URLS:
                for suffix in ("", "?btsa_seo_verify=1"):
                    response = requests.get(url + suffix, timeout=30, headers={"User-Agent": "BTSA-SEO-Agent-Verify/1.0"})
                    print(
                        f"RENDER_VERIFY url={url + suffix} status={response.status_code} "
                        f"babel={'@babel/standalone' in response.text} tailwind={'cdn.tailwindcss.com' in response.text} "
                        f"bytes={len(response.content)}",
                        flush=True,
                    )
        if os.getenv("SEO_ESTIMATOR_INSPECT", "0") == "1":
            for page_id in (275, 1211, 1078, 1036, 1217, 1234):
                asset_info = inspect_elementor_assets(page_id)
                print(f"ESTIMATOR_ASSETS {asset_info}", flush=True)
        if os.getenv("SEO_EXPORT_ELEMENTOR", "0") == "1":
            page_id = int(os.getenv("SEO_EXPORT_ELEMENTOR_PAGE", "1211"))
            snap = update_content("pages", page_id, {}, dry_run=True)
            elementor_data = ((snap["before"].get("meta") or {}).get("_elementor_data") or "")
            if not isinstance(elementor_data, str) or not elementor_data.strip():
                raise RuntimeError(f"Elementor export missing for page {page_id}")
            encoded = base64.b64encode(elementor_data.encode("utf-8")).decode("ascii")
            chunk_size = 6000
            chunks = [encoded[i:i + chunk_size] for i in range(0, len(encoded), chunk_size)]
            print(f"ELEMENTOR_EXPORT_META page={page_id} chunks={len(chunks)} bytes={len(elementor_data.encode('utf-8'))}", flush=True)
            for index, chunk in enumerate(chunks):
                print(f"ELEMENTOR_EXPORT_{index:03d} {chunk}", flush=True)
        if os.getenv("SEO_EXPORT_ESTIMATOR", "0") == "1":
            html = get_estimator_html(275)
            encoded = base64.b64encode(html.encode("utf-8")).decode("ascii")
            chunk_size = 6000
            chunks = [encoded[i:i + chunk_size] for i in range(0, len(encoded), chunk_size)]
            print(f"ESTIMATOR_EXPORT_META chunks={len(chunks)} bytes={len(html)}", flush=True)
            for index, chunk in enumerate(chunks):
                print(f"ESTIMATOR_EXPORT_{index:02d} {chunk}", flush=True)
        for result in run_speed_batch(BTSA_PERFORMANCE_URLS):
            url = result["url"]
            for strategy in ("mobile", "desktop"):
                item = result[strategy]
                if item.get("ok"):
                    print(
                        f"PAGESPEED strategy={strategy} url={url} perf={item['scores']['performance']} "
                        f"seo={item['scores']['seo']} lcp={item['lab']['lcp']['value_ms']} "
                        f"cls={item['lab']['cls']['value']} ttfb={item['lab']['ttfb']['value_ms']} "
                        f"field_lcp={item['field']['lcp_ms']} field_cls={item['field']['cls']} "
                        f"field_ttfb={item['field']['ttfb_ms']} opps={item['opportunities'][:5]} "
                        f"diag={item['diagnostics'] if strategy == 'mobile' else {}}",
                        flush=True,
                    )
                else:
                    print(f"PAGESPEED_FAILED strategy={strategy} url={url} error={item.get('error')}", flush=True)
    except Exception as exc:
        print(f"SEO_BOOTSTRAP_FAILED type={type(exc).__name__} detail={repr(exc)}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("WP_VERIFY_ON_STARTUP", "0") == "1":
        try:
            result = connection_check()
            print(f"WP_VERIFY authenticated={result['authenticated']} user_id={result['user']['id']} pages_read={len(result['recent_pages'])} write_actions={result['write_actions_taken']}", flush=True)
        except Exception as exc:
            print(f"WP_VERIFY failed type={type(exc).__name__} detail={repr(exc)}", flush=True)
    if os.getenv("SEO_BOOTSTRAP_AUDIT", "0") == "1":
        threading.Thread(target=_bootstrap_audit, daemon=True).start()
    yield

app = FastAPI(title="BTSA SEO Agent", version="0.4.0", lifespan=lifespan)

BTSA_PERFORMANCE_URLS = [
    "https://biketransport.co.za/",
    "https://biketransport.co.za/durban-johannesburg-motorcycle-transport/",
    "https://biketransport.co.za/durban-bloemfontein-motorcycle-transport/",
    "https://biketransport.co.za/durban-cape-town-motorcycle-transport/",
    "https://biketransport.co.za/johannesburg-and-cape-town/",
    "https://biketransport.co.za/kzn-local-motorcycle-transport/",
]


class SpeedBatchRequest(BaseModel):
    urls: list[str] | None = None


class WordPressUpdateRequest(BaseModel):
    object_type: str
    object_id: int
    fields: dict
    dry_run: bool = True


@app.get("/health")
def health():
    config = load_config()
    return {
        "ok": True,
        "service": "btsa-seo-agent",
        "version": "0.4.0",
        "wordpress_configured": all(bool(os.getenv(key, "").strip()) for key in ("WP_BASE_URL", "WP_USERNAME", "WP_APP_PASSWORD")),
        "domain": config["domain"],
        "market": config["market"]["country_name"],
        "wordpress_write_enabled": config["guardrails"]["wordpress_write_enabled"],
        "pagespeed_configured": bool(os.getenv("PAGESPEED_API_KEY", "").strip()),
    }


@app.get("/wordpress/connection", dependencies=[Depends(require_agent_key)])
def wordpress_connection():
    try:
        return connection_check()
    except WordPressConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"WordPress request failed: {exc}")


@app.post("/wordpress/update", dependencies=[Depends(require_agent_key)])
def wordpress_update(request: WordPressUpdateRequest):
    config = load_config()
    if not request.dry_run and not config["guardrails"]["wordpress_write_enabled"]:
        raise HTTPException(status_code=403, detail="WordPress writes are disabled by guardrail")
    try:
        return update_content(request.object_type, request.object_id, request.fields, dry_run=request.dry_run)
    except (WordPressConfigurationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"WordPress update failed: {exc}")


@app.post("/audit/technical", dependencies=[Depends(require_agent_key)])
def audit_technical():
    config = load_config()
    return {
        "domain": config["domain"],
        "crawl": run_crawl(config["domain"], config["seo"]["crawl_limit"]),
        "speed": run_speed(config["site_url"]),
        "write_actions_taken": 0,
    }


@app.post("/audit/pagespeed", dependencies=[Depends(require_agent_key)])
def audit_pagespeed(request: SpeedBatchRequest | None = None):
    urls = request.urls if request and request.urls else BTSA_PERFORMANCE_URLS
    if len(urls) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 URLs per PageSpeed batch")
    if any(not url.startswith("https://biketransport.co.za") for url in urls):
        raise HTTPException(status_code=400, detail="Only biketransport.co.za URLs are allowed")
    return {
        "urls_tested": len(urls),
        "strategies": ["mobile", "desktop"],
        "results": run_speed_batch(urls),
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

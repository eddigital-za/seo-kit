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
from app.wordpress import connection_check, update_content, replace_content_text, replace_elementor_text, rankmath_update_canonical, inspect_elementor_assets, get_estimator_html, optimize_estimator_widget, optimize_post_content_estimator, elementor_clear_cache, restore_estimator_from_backup, capture_estimator_backup, wp_rocket_purge_all, apply_elementor_min_height_reservation, restore_elementor_from_backup, elementor_document_snapshot, inspect_page_text, root_file_check, sitemap_commercial_audit, create_geo_page, retire_geo_page, geo_existing_page_audit, apply_btsa_geo_schema, capture_page_backup, apply_homepage_geo_copy, audit_live_canonicals, audit_commercial_internal_links, WordPressConfigurationError

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
        if os.getenv("SEO_INTERNAL_LINK_AUDIT", "0") == "1":
            for item in audit_commercial_internal_links():
                print(f"SEO_INTERNAL_LINK_RESULT {item}", flush=True)
        if os.getenv("SEO_CANONICAL_AUDIT", "0") == "1":
            for item in audit_live_canonicals():
                print(f"SEO_CANONICAL_AUDIT_RESULT {item}", flush=True)
        if os.getenv("SEO_GEO_UPDATE_HOME", "0") == "1":
            geo_home = apply_homepage_geo_copy("backups/homepage-275-pre-geo-2026-11-02.b64")
            print(
                f"GEO_HOME_UPDATED writes={geo_home['write_actions_taken']} verify={geo_home['verify']}",
                flush=True,
            )
        if os.getenv("SEO_GEO_BACKUP_HOME", "0") == "1":
            home_backup = capture_page_backup(275)
            print(
                f"GEO_HOME_BACKUP page={home_backup['page_id']} slug={home_backup['slug']} "
                f"bytes={home_backup['raw_bytes']} elementor={home_backup['has_elementor']} "
                f"estimator={home_backup['has_estimator_marker']} chunks={(len(home_backup['backup_b64']) + 7999) // 8000}",
                flush=True,
            )
            for i in range(0, len(home_backup["backup_b64"]), 8000):
                print(f"GEO_HOME_BACKUP_CHUNK index={i // 8000:03d} data={home_backup['backup_b64'][i:i+8000]}", flush=True)
        if os.getenv("SEO_GEO_APPLY_SCHEMA", "0") == "1":
            schema_result = apply_btsa_geo_schema()
            print(
                f"GEO_SCHEMA_APPLIED homepage={schema_result['homepage']['page_id']} "
                f"routes={[x['page_id'] for x in schema_result['routes']]} "
                f"writes={schema_result['homepage']['write_actions_taken'] + sum(x['write_actions_taken'] for x in schema_result['routes'])}",
                flush=True,
            )
        if os.getenv("SEO_GEO_EXISTING_PAGE_AUDIT", "0") == "1":
            geo_pages = geo_existing_page_audit((275, 1211, 1217, 1078, 1036, 1234))
            for item in geo_pages:
                print(f"GEO_EXISTING_PAGE {item}", flush=True)
        if os.getenv("SEO_GEO_RETIRE_AI_PAGE", "0") == "1":
            retired = retire_geo_page(1432)
            print(
                f"GEO_AI_PAGE_RETIRE retired={retired.get('retired')} "
                f"already_trash={retired.get('already_trash', False)} "
                f"status={(retired.get('verified') or retired.get('before') or {}).get('status')} "
                f"writes={retired['write_actions_taken']}",
                flush=True,
            )
        if os.getenv("SEO_SITEMAP_AUDIT", "0") == "1":
            audit = sitemap_commercial_audit()
            print(f"SEO_SITEMAP_RESULT {audit}", flush=True)
        if os.getenv("SEO_GEO_AUDIT", "0") == "1":
            for filename in ("robots.txt", "llms.txt"):
                check = root_file_check(filename)
                body = check["body"]
                print(
                    f"GEO_ROOT_FILE file={filename} status={check['status']} "
                    f"oai={'OAI-SearchBot' in body} gpt={'GPTBot' in body} "
                    f"perplexity={'PerplexityBot' in body} claude={'ClaudeBot' in body} "
                    f"bytes={len(body.encode('utf-8'))} body={body.replace(chr(10), '|')[:1000]}",
                    flush=True,
                )
        if os.getenv("SEO_GEO_CREATE_AI_PAGE", "0") == "1":
            ai_content = """<p><strong>Bike Transport South Africa</strong> is a South African motorcycle transport service based in KwaZulu-Natal. We arrange insured motorcycle transport for individual riders, dealerships, collectors, buyers and sellers, with nationwide coverage through our own operations and selected transport partners.</p>
<h2>Motorcycle transport services</h2>
<p>Our core services include door-to-door motorcycle collection and delivery, scheduled long-distance routes, KZN local and regional transport, dealership movements, and motorcycle pre-purchase inspections in the Durban region.</p>
<h2>Core routes</h2>
<ul><li>Durban to Johannesburg and Johannesburg to Durban</li><li>Durban to Bloemfontein and Bloemfontein to Durban</li><li>Durban to Cape Town and Cape Town to Durban</li><li>Johannesburg to Cape Town and Cape Town to Johannesburg</li><li>Local and regional motorcycle transport across KwaZulu-Natal</li></ul>
<h2>How the service works</h2>
<p>Customers request a route-specific quote, confirm collection and delivery details, and secure the booking under Bike Transport South Africa's service terms. Motorcycles are loaded using motorcycle-appropriate tie-down equipment and customers receive transport updates during the job.</p>
<h2>Official information</h2>
<p>Website: <a href="https://biketransport.co.za/">biketransport.co.za</a>. Service information and current route details should be verified on the official website because schedules, availability and pricing can change.</p>
<h2>Key official pages</h2>
<ul><li><a href="https://biketransport.co.za/durban-johannesburg-motorcycle-transport/">Durban to Johannesburg motorcycle transport</a></li><li><a href="https://biketransport.co.za/durban-bloemfontein-motorcycle-transport/">Durban to Bloemfontein motorcycle transport</a></li><li><a href="https://biketransport.co.za/durban-cape-town-motorcycle-transport/">Durban to Cape Town motorcycle transport</a></li><li><a href="https://biketransport.co.za/johannesburg-and-cape-town/">Johannesburg and Cape Town motorcycle transport</a></li><li><a href="https://biketransport.co.za/kzn-local-motorcycle-transport/">KZN local motorcycle transport</a></li><li><a href="https://biketransport.co.za/contact/">Contact Bike Transport South Africa</a></li></ul>"""
            created = create_geo_page("Bike Transport South Africa - AI & Company Information", "ai-info", ai_content)
            print(f"GEO_AI_PAGE created={created.get('created')} id={created.get('id') or (created.get('existing') or {}).get('id')} writes={created['write_actions_taken']}", flush=True)
        if os.getenv("SEO_ROUTE_CONTEXT_AUDIT", "0") == "1":
            for page_id in (1078, 1036):
                exact = inspect_page_text(
                    page_id,
                    ("Deliveries across Gauteng", "JHB ⇄ Cape Town Transport", "Cape Town Transport"),
                )
                print(f"ROUTE_CONTEXT_AUDIT {exact}", flush=True)
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
        if os.getenv("SEO_ELEMENTOR_PROBE", "0") == "1":
            probe = elementor_document_snapshot(1211)
            print(f"ELEMENTOR_DOCUMENT_PROBE {probe}", flush=True)
        if os.getenv("SEO_CLS_TEST", "0") == "1":
            page_id = 1211
            backup_path = "backups/page-1211-elementor-pre-cls-2026-10-20.b64"
            cls_result = apply_elementor_min_height_reservation(
                page_id, "32e56a75", mobile_px=1000, tablet_px=950,
                backup_ref=backup_path, dry_run=False,
            )
            print(
                f"CLS_TEST_WRITE page={page_id} container={cls_result['container_id']} "
                f"mobile={cls_result['mobile_px']} tablet={cls_result['tablet_px']} "
                f"estimator_unchanged={cls_result['estimator_unchanged']} "
                f"backup={backup_path}",
                flush=True,
            )
            test_url = "https://biketransport.co.za/durban-johannesburg-motorcycle-transport/?btsa_cls_test=1"
            test = run_speed_batch([test_url])[0]["mobile"]
            if not test.get("ok"):
                rollback = restore_elementor_from_backup(page_id, backup_path)
                print(f"CLS_TEST_ROLLBACK reason=pagespeed_failed exact={rollback['verified_exact_backup']}", flush=True)
            else:
                cls_value = test["lab"]["cls"]["value"]
                lcp_value = test["lab"]["lcp"]["value_ms"]
                print(f"CLS_TEST_RESULT cls={cls_value} lcp={lcp_value}", flush=True)
                if cls_value >= 0.15:
                    rollback = restore_elementor_from_backup(page_id, backup_path)
                    print(f"CLS_TEST_ROLLBACK reason=no_material_improvement cls={cls_value} exact={rollback['verified_exact_backup']}", flush=True)
                else:
                    print(f"CLS_TEST_KEEP cls={cls_value}", flush=True)
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
    if os.getenv("SEO_SIGNALS_ACCEPTANCE", "0") == "1":
        from app.signals import acceptance_once
        threading.Thread(target=acceptance_once, daemon=True).start()
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
    import asyncio
    from app.signals import weekly_signals
    config = load_config()
    signals = asyncio.run(weekly_signals())
    return {
        "brand": config["brand"],
        "technical": audit_technical(),
        "gsc": {"site": config["gsc_site"], "country": config["market"]["gsc_country"],
                "rows": len(signals["gsc"].get("analytics", {}).get("rows", [])),
                "top_opportunities": signals["top_opportunities"], "write_actions_taken": 0,
                "status": "error" if signals["gsc"].get("status") == "error" else "ok"},
        "signals": signals,
        "guardrails": config["guardrails"],
    }


@app.post("/audit/weekly", dependencies=[Depends(require_agent_key)])
async def audit_weekly():
    from app.signals import weekly_signals
    return await weekly_signals()


@app.get("/audit/weekly/latest", dependencies=[Depends(require_agent_key)])
def latest_weekly():
    from app.signals import LATEST_REPORT
    if LATEST_REPORT is None:
        raise HTTPException(status_code=404, detail="No signals collected in this process yet")
    return LATEST_REPORT


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

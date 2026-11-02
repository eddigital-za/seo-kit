import os
import json
import re
import base64
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


class WordPressConfigurationError(RuntimeError):
    pass


def _credentials() -> tuple[str, str, str]:
    base_url = os.getenv("WP_BASE_URL", "").strip().rstrip("/")
    username = os.getenv("WP_USERNAME", "").strip()
    app_password = os.getenv("WP_APP_PASSWORD", "").strip()
    if not base_url or not username or not app_password:
        raise WordPressConfigurationError(
            "WP_BASE_URL, WP_USERNAME and WP_APP_PASSWORD must be configured"
        )
    return base_url, username, app_password


def _assert_btsa_host(base_url: str) -> None:
    host = (urlparse(base_url).hostname or "").lower()
    if host not in {"biketransport.co.za", "www.biketransport.co.za"}:
        raise WordPressConfigurationError(f"Refusing WordPress access to unapproved host: {host}")


def _request(method: str, path: str, params: dict[str, Any] | None = None, payload: dict[str, Any] | None = None) -> Any:
    base_url, username, app_password = _credentials()
    _assert_btsa_host(base_url)
    response = requests.request(
        method,
        f"{base_url}/wp-json/wp/v2/{path.lstrip('/')}",
        params=params,
        json=payload,
        auth=(username, app_password),
        timeout=30,
        headers={"User-Agent": "BTSA-SEO-Agent/0.4"},
    )
    if response.status_code in (401, 403):
        raise PermissionError(f"WordPress authentication/permission failed ({response.status_code})")
    content_type = response.headers.get("content-type", "")
    if not response.ok:
        raise RuntimeError(f"WordPress HTTP {response.status_code}; content_type={content_type}; url={response.url}")
    if "json" not in content_type.lower():
        raise RuntimeError(f"WordPress returned non-JSON response; status={response.status_code}; content_type={content_type}; url={response.url}")
    try:
        return response.json()
    except ValueError as exc:
        text = response.text
        candidates = [pos for token in ('{"id":', '[{"id":') if (pos := text.find(token)) >= 0]
        if candidates:
            try:
                return requests.models.complexjson.loads(text[min(candidates):])
            except ValueError:
                pass
        raise RuntimeError(
            f"WordPress JSON parse failed; status={response.status_code}; content_type={content_type}; "
            f"url={response.url}; bytes={len(response.content)}"
        ) from exc

def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    return _request("GET", path, params=params)


def _code_bearing_fields(fields: dict[str, Any]) -> bool:
    if "meta" in fields and isinstance(fields.get("meta"), dict) and "_elementor_data" in fields["meta"]:
        return True
    for key in ("content",):
        value = fields.get(key)
        if isinstance(value, str) and any(marker in value for marker in ("<script", "motorcycle-calculator-root", "ReactDOM", "text/babel")):
            return True
    return False


def update_content(object_type: str, object_id: int, fields: dict[str, Any], dry_run: bool = True, backup_ref: str | None = None) -> dict[str, Any]:
    if object_type not in {"pages", "posts"}:
        raise ValueError("object_type must be pages or posts")
    allowed = {"title", "content", "excerpt", "status", "slug", "meta"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unsupported WordPress fields: {sorted(unknown)}")
    before = _get(
        f"{object_type}/{object_id}",
        {"context": "edit", "_fields": "id,slug,status,modified_gmt,title,content,excerpt,meta"},
    )
    if dry_run:
        return {"dry_run": True, "before": before, "proposed_fields": fields, "write_actions_taken": 0}
    if _code_bearing_fields(fields) and not backup_ref:
        raise RuntimeError("CODE_CHANGE_BLOCKED: a persistent backup_ref is required before modifying code-bearing WordPress content")
    updated = _request("POST", f"{object_type}/{object_id}", payload=fields)
    try:
        verify = _get(
            f"{object_type}/{object_id}",
            {"context": "edit", "_fields": "id,slug,status,modified_gmt,title,content,excerpt,meta"},
        )
    except Exception:
        rollback = {}
        for key in ("title", "content", "excerpt"):
            value = before.get(key)
            if isinstance(value, dict) and "raw" in value:
                rollback[key] = value["raw"]
        for key in ("status", "slug"):
            if before.get(key):
                rollback[key] = before[key]
        if rollback:
            _request("POST", f"{object_type}/{object_id}", payload=rollback)
        raise
    return {
        "dry_run": False,
        "backup": before,
        "updated_id": updated.get("id"),
        "verified": verify,
        "rollback_ready": True,
        "write_actions_taken": 1,
    }


def connection_check() -> dict[str, Any]:
    users = _get("users/me", {"context": "edit"})
    pages = _get("pages", {"context": "edit", "per_page": 5, "orderby": "modified", "order": "desc", "_fields": "id,slug,status,modified_gmt"})
    return {
        "authenticated": True,
        "user": {
            "id": users.get("id"),
            "name": users.get("name"),
            "slug": users.get("slug"),
            "capabilities": users.get("capabilities", {}),
        },
        "recent_pages": [
            {
                "id": page.get("id"),
                "slug": page.get("slug"),
                "status": page.get("status"),
                "modified": page.get("modified_gmt"),
            }
            for page in pages
        ],
        "write_actions_taken": 0,
    }


def replace_content_text(object_id: int, replacements: dict[str, str], dry_run: bool = True, backup_ref: str | None = None) -> dict[str, Any]:
    before = _get(
        f"pages/{object_id}",
        {"context": "edit", "_fields": "id,slug,status,modified_gmt,title,content,excerpt,meta"},
    )
    content = (before.get("content") or {}).get("raw", "")
    changed = content
    applied = []
    for old, new in replacements.items():
        if old in changed:
            changed = changed.replace(old, new)
            applied.append(old)
    if not applied:
        return {"page_id": object_id, "changed": False, "matches": [], "write_actions_taken": 0}
    if dry_run:
        return {"page_id": object_id, "changed": True, "matches": applied, "write_actions_taken": 0}
    result = update_content("pages", object_id, {"content": changed}, dry_run=False, backup_ref=backup_ref)
    result["matches"] = applied
    return result


def replace_elementor_text(object_id: int, replacements: dict[str, str], dry_run: bool = True, backup_ref: str | None = None) -> dict[str, Any]:
    before = _get(
        f"pages/{object_id}",
        {"context": "edit", "_fields": "id,slug,status,modified_gmt,meta"},
    )
    meta = before.get("meta") or {}
    elementor_data = meta.get("_elementor_data", "")
    if not isinstance(elementor_data, str):
        raise RuntimeError("Elementor data is not a string")
    changed = elementor_data
    applied = []
    for old, new in replacements.items():
        if old in changed:
            changed = changed.replace(old, new)
            applied.append(old)
    if not applied:
        return {"page_id": object_id, "changed": False, "matches": [], "write_actions_taken": 0}
    if dry_run:
        return {"page_id": object_id, "changed": True, "matches": applied, "write_actions_taken": 0}
    result = update_content("pages", object_id, {"meta": {"_elementor_data": changed}}, dry_run=False, backup_ref=backup_ref)
    result["matches"] = applied
    return result




def apply_homepage_geo_copy(backup_ref: str) -> dict[str, Any]:
    """Strengthen existing homepage entity/citability copy without changing layout, H1 or estimator."""
    replacements = {
        "We make <strong>motorcycle transport</strong> simple, secure, and stress-free. With trusted routes and reliable delivery, Bike Transport South Africa gives you total peace of mind, wherever your ride needs to go.":
        "Bike Transport South Africa provides insured, door-to-door <strong>motorcycle transport</strong> across South Africa for private owners, buyers, sellers and dealerships. We collect and deliver motorcycles on scheduled national routes and local KZN services, with regular connections between Durban, Johannesburg, Bloemfontein and Cape Town.",
        "We're more than just a courier. Bike Transport South Africa was built by riders, for riders. Our reputation is built on transparency, reliability, and a deep respect for your motorcycle. From initial quote to final drop-off, we keep you updated and in control.":
        "Bike Transport South Africa is a family-run motorcycle transport service based in Durban and operating nationwide. Motorcycles are loaded and secured using professional tie-down equipment, with collection and delivery updates throughout the move. Customers can request a route estimate online and confirm collection and delivery details before transport."
    }
    dry_content = replace_content_text(275, replacements, dry_run=True)
    dry_elementor = replace_elementor_text(275, replacements, dry_run=True)
    if len(dry_content.get("matches", [])) != 2 or len(dry_elementor.get("matches", [])) != 2:
        raise RuntimeError(f"Homepage GEO preflight mismatch content={dry_content.get('matches')} elementor={dry_elementor.get('matches')}")
    content_result = replace_content_text(275, replacements, dry_run=False, backup_ref=backup_ref)
    elementor_result = replace_elementor_text(275, replacements, dry_run=False, backup_ref=backup_ref)
    verify = inspect_page_text(275, ("insured, door-to-door", "family-run motorcycle transport service", "motorcycle-calculator-root"))
    return {"content": content_result, "elementor": elementor_result, "verify": verify, "write_actions_taken": 2}



def root_file_check(filename: str) -> dict[str, Any]:
    """Read a public root-level GEO discovery file without mutation."""
    if filename not in {"robots.txt", "llms.txt"}:
        raise ValueError("Unsupported root file")
    base_url, _, _ = _credentials()
    _assert_btsa_host(base_url)
    response = requests.get(
        f"{base_url}/{filename}", timeout=30,
        headers={"User-Agent": "BTSA-GEO-Agent/1.0"},
    )
    return {
        "filename": filename,
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "body": response.text[:50000],
    }

def retire_geo_page(object_id: int) -> dict[str, Any]:
    """Trash only the explicitly allowlisted mistaken GEO page after verifying its identity."""
    if object_id != 1432:
        raise ValueError("Only GEO page 1432 is allowlisted for retirement")
    before = _get(f"pages/{object_id}", {"context": "edit", "_fields": "id,slug,status,title,link"})
    if before.get("slug") != "ai-info":
        raise RuntimeError(f"Refusing retirement: page {object_id} slug is {before.get('slug')!r}")
    if before.get("status") == "trash":
        return {"retired": False, "already_trash": True, "before": before, "write_actions_taken": 0}
    # WordPress core trashes resources via DELETE with force=false.
    base_url, username, app_password = _credentials()
    response = requests.delete(
        f"{base_url}/wp-json/wp/v2/pages/{object_id}",
        params={"force": "false"},
        auth=(username, app_password),
        timeout=30,
        headers={"User-Agent": "BTSA-SEO-Agent/0.4"},
    )
    if response.status_code in (401, 403):
        raise PermissionError(f"WordPress authentication/permission failed ({response.status_code})")
    if not response.ok:
        raise RuntimeError(f"WordPress trash HTTP {response.status_code}: {response.text[:500]}")
    updated = response.json()
    verify = _get(f"pages/{object_id}", {"context": "edit", "_fields": "id,slug,status,title,link"})
    if verify.get("status") != "trash":
        raise RuntimeError("GEO page retirement did not verify")
    return {"retired": True, "before": before, "updated_id": updated.get("id"), "verified": verify, "write_actions_taken": 1}



def create_geo_page(title: str, slug: str, content: str, status: str = "publish") -> dict[str, Any]:
    """Create a plain WordPress page for machine-readable GEO resources."""
    if slug not in {"ai-info", "motorcycle-transport-south-africa"}:
        raise ValueError("GEO page slug not allowlisted")
    existing = _get("pages", {"slug": slug, "context": "edit", "per_page": 10, "_fields": "id,slug,status,link"})
    if existing:
        return {"created": False, "existing": existing[0], "write_actions_taken": 0}
    created = _request("POST", "pages", payload={
        "title": title, "slug": slug, "content": content, "status": status,
    })
    verify = _get(f"pages/{created['id']}", {"context": "edit", "_fields": "id,slug,status,link,title,content"})
    return {"created": True, "id": created.get("id"), "slug": slug, "verified": verify, "write_actions_taken": 1}


def inspect_page_text(object_id: int, needles: tuple[str, ...]) -> dict[str, Any]:
    page = _get(
        f"pages/{object_id}",
        {"context": "edit", "_fields": "id,slug,title,content,meta"},
    )
    content = (page.get("content") or {}).get("raw", "")
    elementor = ((page.get("meta") or {}).get("_elementor_data") or "")
    out = {}
    for needle in needles:
        hits = []
        for source_name, source in (("content", content), ("elementor", elementor)):
            start = 0
            while True:
                pos = source.find(needle, start)
                if pos < 0:
                    break
                hits.append({
                    "source": source_name,
                    "context": source[max(0, pos - 180): min(len(source), pos + len(needle) + 220)],
                })
                start = pos + len(needle)
        out[needle] = hits
    return {"page_id": object_id, "slug": page.get("slug"), "hits": out}


def geo_existing_page_audit(object_ids: tuple[int, ...]) -> list[dict[str, Any]]:
    """Audit existing canonical pages for GEO/entity/citability signals without creating content."""
    out = []
    for object_id in object_ids:
        page = _get(f"pages/{object_id}", {"context": "edit", "_fields": "id,slug,status,link,title,content,meta"})
        raw = (page.get("content") or {}).get("raw", "")
        plain = re.sub(r"<[^>]+>", " ", raw)
        plain = re.sub(r"\\s+", " ", plain).strip()
        meta = page.get("meta") or {}
        out.append({
            "id": object_id, "slug": page.get("slug"), "status": page.get("status"),
            "link": page.get("link"), "title": (page.get("title") or {}).get("raw", ""),
            "word_count": len(plain.split()),
            "has_brand": "Bike Transport South Africa" in plain,
            "has_door_to_door": "door-to-door" in plain.lower() or "door to door" in plain.lower(),
            "has_insured": "insured" in plain.lower() or "insurance" in plain.lower(),
            "has_faq": "frequently asked" in plain.lower() or "faq" in plain.lower(),
            "has_jsonld": "application/ld+json" in raw.lower(),
            "meta_keys": sorted(meta.keys()),
        })
    return out



def capture_page_backup(object_id: int) -> dict[str, Any]:
    """Capture full editable page state for a production-safe content/structure backup."""
    page = _get(f"pages/{object_id}", {"context": "edit", "_fields": "id,slug,status,modified_gmt,title,content,excerpt,meta"})
    payload = json.dumps(page, ensure_ascii=False, separators=(",", ":"))
    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    return {
        "page_id": object_id,
        "slug": page.get("slug"),
        "modified_gmt": page.get("modified_gmt"),
        "backup_b64": encoded,
        "raw_bytes": len(payload.encode("utf-8")),
        "has_elementor": "_elementor_data" in (page.get("meta") or {}),
        "has_estimator_marker": "motorcycle-calculator-root" in payload,
    }



def audit_live_canonicals() -> list[dict[str, Any]]:
    """Read-only public canonical audit; does not alter WordPress."""
    pages = {
        275: "https://biketransport.co.za/",
        1211: "https://biketransport.co.za/durban-johannesburg-motorcycle-transport/",
        1217: "https://biketransport.co.za/durban-bloemfontein-motorcycle-transport/",
        1078: "https://biketransport.co.za/durban-cape-town-motorcycle-transport/",
        1036: "https://biketransport.co.za/johannesburg-and-cape-town/",
        1234: "https://biketransport.co.za/kzn-local-motorcycle-transport/",
    }
    out = []
    for page_id, url in pages.items():
        response = requests.get(url, timeout=30, headers={"User-Agent": "BTSA-SEO-Agent/0.4"})
        matches = re.findall(r'<link\\b[^>]*\\brel=["\\\']canonical["\\\'][^>]*>', response.text, re.I)
        canonical = re.search(r'href=["\\\']([^"\\\']+)', matches[0], re.I).group(1) if matches else None
        head = response.text.split("</head>", 1)[0][:60000]
        spots = [m.start() for m in re.finditer("canonical", head, re.I)]
        out.append({"page_id": page_id, "status": response.status_code, "expected": url, "canonical": canonical, "matches": len(matches), "correct": canonical == url, "blocked": "request is being verified" in response.text.lower(), "head_bytes": len(head), "canonical_mentions": len(spots), "canonical_context": [head[max(0,p-130):p+180] for p in spots[:3]], "head_prefix": head[:180]})
    return out



def audit_commercial_internal_links() -> list[dict[str, Any]]:
    """Read-only links between the existing homepage and five commercial routes."""
    pages = {
        275: "https://biketransport.co.za/",
        1211: "https://biketransport.co.za/durban-johannesburg-motorcycle-transport/",
        1217: "https://biketransport.co.za/durban-bloemfontein-motorcycle-transport/",
        1078: "https://biketransport.co.za/durban-cape-town-motorcycle-transport/",
        1036: "https://biketransport.co.za/johannesburg-and-cape-town/",
        1234: "https://biketransport.co.za/kzn-local-motorcycle-transport/",
    }
    result = []
    for page_id, url in pages.items():
        response = requests.get(url, timeout=30, headers={"User-Agent": "BTSA-SEO-Agent/0.4"})
        html = response.text
        hrefs = []
        for tag in re.findall("<a[^>]*>", html, re.I):
            match = re.search("href=[\\\"']([^\\\"']+)", tag, re.I)
            if match:
                hrefs.append(match.group(1))
        targets = {target_id: sum(1 for href in hrefs if href.rstrip("/") == target.rstrip("/")) for target_id, target in pages.items() if target_id != page_id}
        internal = sorted({href for href in hrefs if href.startswith("https://biketransport.co.za/") or href.startswith("/")})
        result.append({"page_id": page_id, "targets": targets, "missing": [key for key, count in targets.items() if not count], "status": response.status_code, "html_bytes": len(html), "anchor_tags": len(re.findall("<a[^>]*>", html, re.I)), "href_count": len(hrefs), "internal_count": len(internal), "internal_links": internal[:80]})
    return result


def rankmath_update_canonical(object_id: int, canonical_url: str) -> dict[str, Any]:
    base_url, username, app_password = _credentials()
    _assert_btsa_host(base_url)
    if not canonical_url.startswith("https://biketransport.co.za/"):
        raise ValueError("Canonical must stay on biketransport.co.za")
    response = requests.post(
        f"{base_url}/wp-json/rankmath/v1/updateMeta",
        json={
            "objectType": "post",
            "objectID": object_id,
            "meta": {"rank_math_canonical_url": canonical_url},
        },
        auth=(username, app_password),
        timeout=30,
        headers={"User-Agent": "BTSA-SEO-Agent/0.4"},
    )
    if response.status_code in (401, 403):
        raise PermissionError(f"Rank Math authentication/permission failed ({response.status_code})")
    if not response.ok:
        raise RuntimeError(f"Rank Math HTTP {response.status_code}; url={response.url}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Rank Math returned invalid JSON; status={response.status_code}") from exc
    return {"page_id": object_id, "canonical": canonical_url, "response": payload, "write_actions_taken": 1}


def rankmath_update_schemas(object_id: int, schemas: dict[str, Any]) -> dict[str, Any]:
    """Update Rank Math schema for one page through Rank Math's own REST endpoint."""
    base_url, username, app_password = _credentials()
    _assert_btsa_host(base_url)
    response = requests.post(
        f"{base_url}/wp-json/rankmath/v1/updateSchemas",
        json={"objectType": "post", "objectID": object_id, "schemas": schemas},
        auth=(username, app_password), timeout=30,
        headers={"User-Agent": "BTSA-SEO-Agent/0.4"},
    )
    if response.status_code in (401, 403):
        raise PermissionError(f"Rank Math schema permission failed ({response.status_code})")
    if not response.ok:
        raise RuntimeError(f"Rank Math schema HTTP {response.status_code}; body={response.text[:500]}")
    return {"page_id": object_id, "response": response.json(), "write_actions_taken": 1}


def apply_btsa_geo_schema() -> dict[str, Any]:
    """Apply conservative schema to existing authority pages. No invented address, hours, reviews or social URLs."""
    org_id = "https://biketransport.co.za/#organization"
    site_id = "https://biketransport.co.za/#website"
    homepage = {
        "new-btsa-organization": {
            "@type": "Organization",
            "@id": org_id,
            "name": "Bike Transport South Africa",
            "url": "https://biketransport.co.za/",
            "description": "Motorcycle transport service operating across South Africa.",
            "telephone": "+27724075794",
            "areaServed": {"@type": "Country", "name": "South Africa"},
        },
        "new-btsa-website": {
            "@type": "WebSite",
            "@id": site_id,
            "url": "https://biketransport.co.za/",
            "name": "Bike Transport South Africa",
            "publisher": {"@id": org_id},
            "inLanguage": "en-ZA",
        },
        "new-btsa-service": {
            "@type": "Service",
            "@id": "https://biketransport.co.za/#motorcycle-transport-service",
            "name": "Motorcycle Transport South Africa",
            "serviceType": "Motorcycle transport",
            "url": "https://biketransport.co.za/",
            "provider": {"@id": org_id},
            "areaServed": {"@type": "Country", "name": "South Africa"},
        },
    }
    routes = {
        1211: ("Durban to Johannesburg Motorcycle Transport", "https://biketransport.co.za/durban-johannesburg-motorcycle-transport/", ["Durban", "Johannesburg"]),
        1217: ("Durban to Bloemfontein Motorcycle Transport", "https://biketransport.co.za/durban-bloemfontein-motorcycle-transport/", ["Durban", "Bloemfontein"]),
        1078: ("Durban to Cape Town Motorcycle Transport", "https://biketransport.co.za/durban-cape-town-motorcycle-transport/", ["Durban", "Cape Town"]),
        1036: ("Johannesburg to Cape Town Motorcycle Transport", "https://biketransport.co.za/johannesburg-and-cape-town/", ["Johannesburg", "Cape Town"]),
        1234: ("KZN Local and Regional Motorcycle Transport", "https://biketransport.co.za/kzn-local-motorcycle-transport/", ["KwaZulu-Natal"]),
    }
    results = {"homepage": rankmath_update_schemas(275, homepage), "routes": []}
    for page_id, (name, url, areas) in routes.items():
        schema = {
            f"new-btsa-service-{page_id}": {
                "@type": "Service",
                "@id": f"{url}#service",
                "name": name,
                "serviceType": "Motorcycle transport",
                "url": url,
                "provider": {"@id": org_id},
                "areaServed": [{"@type": "Place", "name": area} for area in areas],
            }
        }
        results["routes"].append(rankmath_update_schemas(page_id, schema))
    return results



def inspect_elementor_assets(object_id: int) -> dict[str, Any]:
    page = _get(f"pages/{object_id}", {"context": "edit", "_fields": "id,slug,meta"})
    data = ((page.get("meta") or {}).get("_elementor_data") or "")
    needles = ("babel", "tailwindcss", "text/babel", "unpkg.com/react")
    snippets = {}
    lower = data.lower()
    for needle in needles:
        pos = lower.find(needle.lower())
        if pos >= 0:
            snippets[needle] = data[max(0, pos - 800): min(len(data), pos + 2200)]
    return {"page_id": object_id, "slug": page.get("slug"), "bytes": len(data), "snippets": snippets}


def get_estimator_html(object_id: int) -> str:
    """Return the estimator widget whether it is the original JSX or a compiled variant."""
    page = _get(f"pages/{object_id}", {"context": "edit", "_fields": "id,meta"})
    data = ((page.get("meta") or {}).get("_elementor_data") or "")
    tree = json.loads(data)
    stack = list(tree if isinstance(tree, list) else [tree])
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        settings = item.get("settings") or {}
        html = settings.get("html")
        if isinstance(html, str) and 'motorcycle-calculator-root' in html:
            return html
        stack.extend(item.get("elements") or [])
    raise RuntimeError(f"Estimator HTML widget not found on page {object_id}")


def replace_estimator_html(object_id: int, new_html: str, dry_run: bool = True, backup_ref: str | None = None) -> dict[str, Any]:
    page = _get(f"pages/{object_id}", {"context": "edit", "_fields": "id,slug,status,modified_gmt,meta"})
    meta = page.get("meta") or {}
    data = meta.get("_elementor_data") or ""
    tree = json.loads(data)
    changed = False
    replacements = 0
    stack = list(tree if isinstance(tree, list) else [tree])
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        settings = item.get("settings") or {}
        html = settings.get("html")
        if isinstance(html, str) and 'type="text/babel"' in html:
            settings["html"] = new_html
            changed = True
            replacements += 1
        stack.extend(item.get("elements") or [])
    if not changed:
        return {"page_id": object_id, "changed": False, "replacements": 0, "write_actions_taken": 0}
    if dry_run:
        return {"page_id": object_id, "changed": True, "write_actions_taken": 0}
    compact = json.dumps(tree, separators=(",", ":"), ensure_ascii=False)
    result = update_content("pages", object_id, {"meta": {"_elementor_data": compact}}, dry_run=False, backup_ref=backup_ref)
    result["replacements"] = replacements
    return result


def optimize_estimator_widget(object_id: int, dry_run: bool = True, backup_ref: str | None = None) -> dict[str, Any]:
    try:
        html = get_estimator_html(object_id)
    except RuntimeError:
        return {"page_id": object_id, "changed": False, "reason": "no text/babel estimator", "write_actions_taken": 0}
    match = re.search(r'<script\s+type=["\']text/babel["\']>(.*?)</script>', html, flags=re.I | re.S)
    if not match:
        return {"page_id": object_id, "changed": False, "reason": "no text/babel script", "write_actions_taken": 0}
    source = match.group(1)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "estimator.jsx"
        out = Path(tmp) / "estimator.js"
        src.write_text(source, encoding="utf-8")
        process = subprocess.run(
            [
                "tsc", str(src), "--jsx", "react", "--target", "ES2018",
                "--module", "none", "--allowJs", "--skipLibCheck",
                "--outFile", str(out),
            ],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if not out.exists():
            raise RuntimeError(f"Estimator transpilation failed: {process.stderr[-1000:]}")
        compiled = out.read_text(encoding="utf-8")
    optimized = re.sub(
        r'\s*<script[^>]+@babel/standalone[^>]*></script>\s*',
        "\n",
        html,
        flags=re.I,
    )
    optimized = re.sub(
        r'<script\s+type=["\']text/babel["\']>.*?</script>',
        lambda _match: "<script>\n" + compiled + "\n</script>",
        optimized,
        count=1,
        flags=re.I | re.S,
    )
    if optimized == html:
        return {"page_id": object_id, "changed": False, "reason": "no effective change", "write_actions_taken": 0}
    if dry_run:
        return {
            "page_id": object_id, "changed": True, "source_bytes": len(source),
            "compiled_bytes": len(compiled), "babel_removed": "@babel/standalone" not in optimized,
            "write_actions_taken": 0,
        }
    result = replace_estimator_html(object_id, optimized, dry_run=False, backup_ref=backup_ref)
    result.update({
        "source_bytes": len(source),
        "compiled_bytes": len(compiled),
        "babel_removed": "@babel/standalone" not in optimized,
    })
    return result


def optimize_post_content_estimator(object_id: int, dry_run: bool = True, backup_ref: str | None = None) -> dict[str, Any]:
    page = _get(f"pages/{object_id}", {"context": "edit", "_fields": "id,slug,content"})
    html = (page.get("content") or {}).get("raw", "")
    match = re.search(r'<script\s+type=["\']text/babel["\']>(.*?)</script>', html, flags=re.I | re.S)
    if not match:
        return {"page_id": object_id, "changed": False, "reason": "no text/babel in post_content", "write_actions_taken": 0}
    source = match.group(1)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "estimator.jsx"
        out = Path(tmp) / "estimator.js"
        src.write_text(source, encoding="utf-8")
        process = subprocess.run(
            ["tsc", str(src), "--jsx", "react", "--target", "ES2018", "--module", "none",
             "--allowJs", "--skipLibCheck", "--outFile", str(out)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if not out.exists():
            raise RuntimeError(f"Post-content estimator transpilation failed: {process.stderr[-1000:]}")
        compiled = out.read_text(encoding="utf-8")
    optimized = re.sub(r'\s*<script[^>]+@babel/standalone[^>]*></script>\s*', "\n", html, flags=re.I)
    optimized = re.sub(
        r'<script\s+type=["\']text/babel["\']>.*?</script>',
        lambda _match: "<script>\n" + compiled + "\n</script>",
        optimized, count=1, flags=re.I | re.S,
    )
    if dry_run:
        return {"page_id": object_id, "changed": True, "babel_removed": "@babel/standalone" not in optimized, "write_actions_taken": 0}
    result = update_content("pages", object_id, {"content": optimized}, dry_run=False, backup_ref=backup_ref)
    result["babel_removed"] = "@babel/standalone" not in optimized
    return result




def apply_elementor_min_height_reservation(
    object_id: int,
    container_id: str,
    mobile_px: int,
    tablet_px: int | None,
    backup_ref: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Reserve layout space on one Elementor container without touching estimator HTML."""
    page = _get(f"pages/{object_id}", {"context": "edit", "_fields": "id,slug,status,modified_gmt,meta"})
    data = ((page.get("meta") or {}).get("_elementor_data") or "")
    tree = json.loads(data)
    before_estimator = get_estimator_html(object_id)
    found = None
    stack = list(tree if isinstance(tree, list) else [tree])
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        if item.get("id") == container_id:
            found = item
            break
        stack.extend(item.get("elements") or [])
    if not found:
        raise RuntimeError(f"Elementor container {container_id} not found on page {object_id}")
    settings = found.setdefault("settings", {})
    previous = {
        "min_height": settings.get("min_height"),
        "min_height_mobile": settings.get("min_height_mobile"),
        "min_height_tablet": settings.get("min_height_tablet"),
    }
    settings["min_height"] = {"unit": "px", "size": mobile_px, "sizes": []}
    settings["min_height_mobile"] = {"unit": "px", "size": mobile_px, "sizes": []}
    if tablet_px is not None:
        settings["min_height_tablet"] = {"unit": "px", "size": tablet_px, "sizes": []}
    compact = json.dumps(tree, separators=(",", ":"), ensure_ascii=False)
    if dry_run:
        return {"page_id": object_id, "container_id": container_id, "previous": previous, "write_actions_taken": 0}
    result = update_content(
        "pages", object_id, {"meta": {"_elementor_data": compact}},
        dry_run=False, backup_ref=backup_ref,
    )
    after_estimator = get_estimator_html(object_id)
    if after_estimator != before_estimator:
        raise RuntimeError("ESTIMATOR_GUARD_FAILED: estimator HTML changed during layout reservation")
    result.update({
        "container_id": container_id,
        "previous": previous,
        "mobile_px": mobile_px,
        "tablet_px": tablet_px,
        "estimator_unchanged": True,
    })
    return result


def restore_elementor_from_backup(object_id: int, backup_path: str) -> dict[str, Any]:
    """Restore exact Elementor JSON from a persistent base64 backup and verify byte equality."""
    path = Path(backup_path)
    if not path.exists():
        raise RuntimeError(f"Elementor backup not found: {backup_path}")
    raw = base64.b64decode(path.read_text(encoding="utf-8").strip()).decode("utf-8")
    json.loads(raw)
    result = update_content(
        "pages", object_id, {"meta": {"_elementor_data": raw}},
        dry_run=False, backup_ref=str(path),
    )
    verify = _get(f"pages/{object_id}", {"context": "edit", "_fields": "id,meta"})
    readback = ((verify.get("meta") or {}).get("_elementor_data") or "")
    if readback != raw:
        raise RuntimeError("Elementor rollback read-back mismatch")
    result["verified_exact_backup"] = True
    result["backup_ref"] = str(path)
    return result


def capture_estimator_backup(object_ids: tuple[int, ...], backup_dir: str = "backups") -> dict[str, Any]:
    """Persist an exact pre-purge estimator rollback bundle and verify every page first."""
    path = Path(backup_dir)
    path.mkdir(parents=True, exist_ok=True)
    captured: dict[int, str] = {}
    for object_id in object_ids:
        html = get_estimator_html(object_id)
        if "motorcycle-calculator-root" not in html or 'type="text/babel"' not in html:
            raise RuntimeError(f"Estimator backup validation failed on page {object_id}")
        captured[object_id] = html
    unique = set(captured.values())
    if len(unique) != 1:
        raise RuntimeError("Estimator instances are not identical; refusing to create a single rollback baseline")
    html = next(iter(unique))
    encoded = base64.b64encode(html.encode("utf-8")).decode("ascii")
    stamp = os.getenv("SEO_BACKUP_STAMP", "pre-cache-purge-2026-10-19")
    backup_file = path / f"estimator-{stamp}.b64"
    backup_file.write_text(encoded, encoding="utf-8")
    readback = base64.b64decode(backup_file.read_text(encoding="utf-8")).decode("utf-8")
    if readback != html:
        raise RuntimeError("Estimator rollback backup read-back verification failed")
    return {"backup_path": str(backup_file), "pages": list(object_ids), "bytes": len(html.encode("utf-8")), "verified": True}


def wp_rocket_purge_all() -> dict[str, Any]:
    """Discover WP Rocket's actual REST route and explicitly purge only when a supported route exists."""
    base_url, username, app_password = _credentials()
    _assert_btsa_host(base_url)
    index = requests.get(
        f"{base_url}/wp-json/",
        auth=(username, app_password),
        timeout=30,
        headers={"User-Agent": "BTSA-SEO-Agent/0.4"},
    )
    if not index.ok:
        raise RuntimeError(f"WordPress REST index failed HTTP {index.status_code}")
    payload = index.json()
    routes = payload.get("routes") or {}
    rocket_routes = sorted(route for route in routes if "rocket" in route.lower())
    purge_candidates = [
        route for route in rocket_routes
        if any(token in route.lower() for token in ("purge", "clear", "cache"))
    ]
    attempts = []
    for route in purge_candidates:
        route_info = routes.get(route) or {}
        endpoints = route_info.get("endpoints") or []
        methods = []
        for endpoint in endpoints:
            allowed = endpoint.get("methods") or []
            if isinstance(allowed, list):
                methods.extend(allowed)
        for method in ("POST", "DELETE"):
            if methods and method not in methods:
                continue
            response = requests.request(
                method,
                f"{base_url}/wp-json{route}",
                auth=(username, app_password),
                timeout=30,
                headers={"User-Agent": "BTSA-SEO-Agent/0.4"},
            )
            attempts.append({"method": method, "route": route, "status": response.status_code})
            if response.ok:
                return {"cleared": True, "method": method, "route": route, "status": response.status_code, "attempts": attempts}
    return {"cleared": False, "discovered_routes": rocket_routes, "attempts": attempts}


def elementor_document_snapshot(object_id: int) -> dict[str, Any]:
    """Read Elementor's own document endpoint without changing the page."""
    base_url, username, app_password = _credentials()
    _assert_btsa_host(base_url)
    response = requests.get(
        f"{base_url}/wp-json/elementor/v1/documents/{object_id}",
        auth=(username, app_password),
        timeout=30,
        headers={"User-Agent": "BTSA-SEO-Agent/0.4"},
    )
    if not response.ok:
        return {"ok": False, "status": response.status_code}
    payload = response.json()
    return {
        "ok": True,
        "status": response.status_code,
        "keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
        "type": type(payload).__name__,
    }


def elementor_clear_cache() -> dict[str, Any]:
    base_url, username, app_password = _credentials()
    _assert_btsa_host(base_url)
    response = requests.delete(
        f"{base_url}/wp-json/elementor/v1/cache",
        auth=(username, app_password),
        timeout=30,
        headers={"User-Agent": "BTSA-SEO-Agent/0.4"},
    )
    if response.status_code in (401, 403):
        raise PermissionError(f"Elementor cache permission failed ({response.status_code})")
    if not response.ok:
        raise RuntimeError(f"Elementor cache clear failed HTTP {response.status_code}")
    return {"cleared": True, "status": response.status_code}


def restore_estimator_from_backup(object_id: int, backup_path: str) -> dict[str, Any]:
    """Restore the exact backed-up estimator into Elementor and post_content."""
    path = Path(backup_path)
    if not path.exists():
        raise RuntimeError(f"Estimator backup not found: {backup_path}")
    backup_html = base64.b64decode(path.read_text(encoding="utf-8").strip()).decode("utf-8")
    if "motorcycle-calculator-root" not in backup_html or 'type="text/babel"' not in backup_html:
        raise RuntimeError("Estimator backup validation failed")

    backup_ref = str(path)
    page = _get(f"pages/{object_id}", {"context": "edit", "_fields": "id,slug,status,modified_gmt,meta,content"})
    meta = page.get("meta") or {}
    data = meta.get("_elementor_data") or ""
    tree = json.loads(data)
    elementor_replacements = 0
    stack = list(tree if isinstance(tree, list) else [tree])
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        settings = item.get("settings") or {}
        html = settings.get("html")
        if isinstance(html, str) and "motorcycle-calculator-root" in html:
            if html != backup_html:
                settings["html"] = backup_html
                elementor_replacements += 1
        stack.extend(item.get("elements") or [])

    writes = 0
    if elementor_replacements:
        compact = json.dumps(tree, separators=(",", ":"), ensure_ascii=False)
        update_content("pages", object_id, {"meta": {"_elementor_data": compact}}, dry_run=False, backup_ref=backup_ref)
        writes += 1

    raw = (page.get("content") or {}).get("raw", "")
    content_changed = False
    if "motorcycle-calculator-root" in raw:
        start = raw.rfind("<!-- Load Required Libraries", 0, raw.find("motorcycle-calculator-root"))
        if start < 0:
            start = raw.rfind("<script", 0, raw.find("motorcycle-calculator-root"))
        end_marker = "root.render(<MotorcycleCalculator />);"
        end_pos = raw.find(end_marker, raw.find("motorcycle-calculator-root"))
        if start >= 0 and end_pos >= 0:
            close = raw.find("</script>", end_pos)
            if close >= 0:
                close += len("</script>")
                restored = raw[:start] + backup_html + raw[close:]
                if restored != raw:
                    update_content("pages", object_id, {"content": restored}, dry_run=False, backup_ref=backup_ref)
                    content_changed = True
                    writes += 1

    verify = get_estimator_html(object_id)
    if verify != backup_html:
        raise RuntimeError(f"Estimator restore verification failed on page {object_id}")
    return {
        "page_id": object_id,
        "elementor_replacements": elementor_replacements,
        "content_changed": content_changed,
        "babel_restored": "@babel/standalone" in verify,
        "verified_exact_backup": verify == backup_html,
        "write_actions_taken": writes,
        "backup_ref": backup_ref,
    }

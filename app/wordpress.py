import os
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


def update_content(object_type: str, object_id: int, fields: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
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

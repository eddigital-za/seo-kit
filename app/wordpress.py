import os
from typing import Any

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


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    base_url, username, app_password = _credentials()
    response = requests.get(
        f"{base_url}/wp-json/wp/v2/{path.lstrip('/')}",
        params=params,
        auth=(username, app_password),
        timeout=30,
        headers={"User-Agent": "BTSA-SEO-Agent/0.3"},
    )
    if response.status_code in (401, 403):
        raise PermissionError(
            f"WordPress authentication/permission failed ({response.status_code})"
        )
    response.raise_for_status()
    return response.json()


def connection_check() -> dict[str, Any]:
    users = _get("users/me", {"context": "edit"})
    pages = _get("pages", {"context": "edit", "per_page": 5, "orderby": "modified", "order": "desc"})
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

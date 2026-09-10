from datetime import date, timedelta
import os
from urllib.parse import quote
import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
GSC_API = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"

class GSCConfigurationError(RuntimeError):
    pass

def _access_token() -> str:
    required = {
        "GOOGLE_CLIENT_ID": os.getenv("GOOGLE_CLIENT_ID"),
        "GOOGLE_CLIENT_SECRET": os.getenv("GOOGLE_CLIENT_SECRET"),
        "GOOGLE_REFRESH_TOKEN": os.getenv("GOOGLE_REFRESH_TOKEN"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise GSCConfigurationError("Missing Google OAuth settings: " + ", ".join(missing))

    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": required["GOOGLE_CLIENT_ID"],
            "client_secret": required["GOOGLE_CLIENT_SECRET"],
            "refresh_token": required["GOOGLE_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["access_token"]

def fetch_queries(site: str, country: str = "zaf", days: int = 28, row_limit: int = 250):
    token = _access_token()
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    payload = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": ["query", "page"],
        "rowLimit": row_limit,
        "dimensionFilterGroups": [{"filters": [{"dimension": "country", "operator": "equals", "expression": country}]}],
    }
    response = requests.post(
        GSC_API.format(site=quote(site, safe="")),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    rows = []
    for row in response.json().get("rows", []):
        keys = row.get("keys", ["", ""])
        rows.append({
            "keyword": keys[0],
            "page": keys[1] if len(keys) > 1 else None,
            "clicks": row.get("clicks", 0),
            "impressions": row.get("impressions", 0),
            "ctr": row.get("ctr", 0),
            "position": row.get("position", 0),
        })
    return rows

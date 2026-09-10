import json
import os
from pathlib import Path

CONFIG_PATH = Path(os.getenv("BTSA_CONFIG", "/app/config/btsa.json"))

def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = json.load(f)

    config["domain"] = os.getenv("SITE_DOMAIN", config["domain"])
    config["site_url"] = os.getenv("SITE_URL", f"https://{config['domain']}")
    config["gsc_site"] = os.getenv("GSC_SITE", f"sc-domain:{config['domain']}")
    config["market"]["gsc_country"] = os.getenv("GSC_COUNTRY", config["market"]["gsc_country"])
    config["seo"]["crawl_limit"] = int(os.getenv("CRAWL_LIMIT", config["seo"]["crawl_limit"]))
    return config

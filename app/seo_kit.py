import os
import subprocess
from pathlib import Path

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

def run_speed(site_url: str):
    kit = Path(os.getenv("SEO_KIT_DIR", "/app"))
    script = kit / "skills/seo-health/scripts/health-speed.sh"
    p = subprocess.run(
        ["bash", str(script), site_url, "--both"],
        cwd="/app",
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    return _result(p)

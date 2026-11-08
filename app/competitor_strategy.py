"""Public page evidence and approval-gated actions for the weekly SEO report."""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


def host(url):
    return (urlsplit(url).hostname or "").removeprefix("www.")


class SameSiteRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != "https" or host(newurl) != host(req.full_url) or target.port not in (None, 443):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


TOPICS = {
    "collection and delivery": r"door.to.door|collection|pickup",
    "timing": r"weekly|schedule|transit time|lead time",
    "pricing": r"pricing|\bprices?\b|\brates?\b|\bR\s?\d[\d, ]{2,}",
    "insurance": r"\binsur\w+|goods in transit",
    "transport equipment": r"trailer|enclosed|straps|tie.down",
    "customer proof": r"reviews|testimonials|trusted by|case stud",
    "dealer services": r"dealers?|dealership",
}


class PageEvidence(HTMLParser):
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.title = ""
        self.description = None
        self.robots = ""
        self.canonical = None
        self.headings = []
        self.links = set()
        self.schema = set()
        self.body = []
        self.active = None
        self.buffer = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        name = (attrs.get("name") or "").lower()
        rel = (attrs.get("rel") or "").lower().split()
        href = attrs.get("href") or ""
        if tag == "meta" and name == "description":
            self.description = attrs.get("content")
        if tag == "meta" and name in ("robots", "googlebot"):
            self.robots += " " + (attrs.get("content") or "")
        if tag == "link" and "canonical" in rel and href:
            self.canonical = urljoin(self.url, href)
        if tag == "a" and href and not href.startswith("#"):
            target = urlsplit(urljoin(self.url, href))
            if target.scheme in ("http", "https") and host(target.geturl()) == host(self.url):
                self.links.add(target.path.rstrip("/") or "/")
        if tag in ("title", "h1", "h2", "h3", "script", "style", "noscript"):
            self.active = tag
            self.buffer = []
            if tag == "script" and attrs.get("type") == "application/ld+json":
                self.active = "jsonld"

    def handle_data(self, data):
        if self.active in ("script", "style", "noscript"):
            return
        if self.active:
            self.buffer.append(data)
        if self.active not in ("title", "jsonld"):
            self.body.append(data)

    def handle_endtag(self, tag):
        if tag != self.active and (tag, self.active) != ("script", "jsonld"):
            return
        value = " ".join("".join(self.buffer).split())
        if self.active == "jsonld":
            try:
                data = json.loads("".join(self.buffer))
            except ValueError:
                pass
            else:
                pending = [data]
                while pending:
                    node = pending.pop()
                    if isinstance(node, dict):
                        types = node.get("@type")
                        if isinstance(types, str):
                            self.schema.add(types)
                        elif isinstance(types, list):
                            self.schema.update(t for t in types if isinstance(t, str))
                        pending.extend(node.values())
                    elif isinstance(node, list):
                        pending.extend(node)
        elif tag == "title":
            self.title = value
        elif tag in ("h1", "h2", "h3") and value:
            self.headings.append({"level": tag, "text": value[:180]})
        self.active = None

    def evidence(self):
        body = " ".join(self.body)
        return {"title": self.title, "description": self.description, "canonical": self.canonical,
                "robots": self.robots.strip(), "schema_types": sorted(self.schema),
                "headings": self.headings[:24], "internal_link_count": len(self.links),
                "internal_paths": sorted(self.links)[:40],
                "topics": sorted(topic for topic, pattern in TOPICS.items() if re.search(pattern, body, re.I))}


def inspect_page(url):
    try:
        request = Request(url, headers={"User-Agent": "BTSA-SEO-Research/1.0"})
        with build_opener(SameSiteRedirect()).open(request, timeout=8) as response:
            if response.headers.get_content_type() != "text/html":
                raise ValueError("Expected HTML")
            parser = PageEvidence(response.url)
            html = response.read(2_000_001)
            if len(html) > 2_000_000:
                raise ValueError("Page exceeds crawl limit")
            parser.feed(html.decode(response.headers.get_content_charset() or "utf-8", errors="replace"))
            evidence = parser.evidence()
            if not evidence["title"] or not evidence["headings"]:
                raise ValueError("Missing title or headings in the fetched HTML")
            return {"url": url, "final_url": response.url, "status": "ok",
                    "http_status": response.status, "x_robots_tag": response.headers.get("X-Robots-Tag"),
                    **evidence}
    except (OSError, ValueError, LookupError) as exc:
        return {"url": url, "status": "unavailable", "reason": str(exc)[:240]}


def collect_strategy(plan, commercial):
    opportunities = commercial["top_opportunities"][:5]
    own_urls = {t["expected_page"] for t in plan["targets"] if t["expected_page"]}
    competitors = plan["competitors"]
    peer_urls = {url for peer in competitors for url in [peer["url"], *peer.get("research_urls", [])]}
    citation_urls = {url for peer in competitors for url in peer.get("citations", [])}
    with ThreadPoolExecutor(max_workers=6) as pool:
        pages = dict(zip(sorted(own_urls | peer_urls | citation_urls),
                         pool.map(inspect_page, sorted(own_urls | peer_urls | citation_urls))))
    live_peers = [pages[url] for url in sorted(peer_urls) if pages[url]["status"] == "ok"]
    actions = []
    for row in opportunities:
        own = pages.get(row["expected_page"])
        if not own or own["status"] != "ok":
            continue
        evidence = {topic: [p["url"] for p in live_peers if topic in p["topics"]]
                    for topic in TOPICS if topic not in own["topics"]}
        gaps = {topic: urls for topic, urls in evidence.items() if len({host(url) for url in urls}) >= 2}
        if row["mapping_status"] == "different_page_observed":
            action = "Check canonical, index coverage and internal links to the intended landing page before drafting changes."
        elif gaps:
            action = "Draft a page update covering verified service facts about " + ", ".join(gaps) + "."
        else:
            action = "Draft a snippet test that answers this search and highlights a verified service differentiator. Record the baseline before requesting approval."
        comparisons = [{"url": peer["url"], "topics_only_on_peer": sorted(set(peer["topics"]) - set(own["topics"])),
                        "topics_only_on_btsa": sorted(set(own["topics"]) - set(peer["topics"])),
                        "schema_only_on_peer": sorted(set(peer["schema_types"]) - set(own["schema_types"])),
                        "btsa_internal_links": own["internal_link_count"],
                        "peer_internal_links": peer["internal_link_count"]} for peer in live_peers]
        actions.append({"query": row["keyword"], "page": own["url"], "observed_page": row["page"],
                        "gsc_impressions": row["impressions"], "priority_score": row["opportunity_score"],
                        "action": action, "peer_topic_evidence": gaps, "comparisons": comparisons,
                        "website_approval_required": True})
    return {"pages": {url: page for url, page in pages.items() if url not in citation_urls},
            "citations": [{"competitor": peer["name"], "url": url, "status": pages[url]["status"],
                           "kind": "public listing", "backlink_verified": False}
                          for peer in competitors for url in peer.get("citations", [])],
            "actions": actions, "serp_status": "controlled_capture_unavailable",
            "interpretation": "Page differences suggest tests. They do not establish why Google ranked a page."}

"""Commercial search/page mapping. Recommendations only; never edits a site."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from app.scoring import score_gsc_rows

PLAN_PATH = Path(__file__).resolve().parent.parent / "config/seo-plan.json"


def load_plan():
    return json.loads(PLAN_PATH.read_text(encoding="utf-8"))


def normal_query(value):
    value = re.sub(r"\bjhb\b|\bjoburg\b", "johannesburg", value.lower())
    value = re.sub(r"\bdbn\b", "durban", value)
    return " ".join(re.findall(r"[a-z0-9]+", value))


def page_key(value):
    parsed = urlsplit(value or "")
    return parsed.netloc.lower() + (parsed.path.rstrip("/") or "/")


def intent(query):
    if any(word in query for word in ("bicycle", "shipping box", "bike box", "fork saver", "jobs", "interstate", "international")):
        return "outside_pilot"
    if query in ("btsa", "biketransport", "bike transport south africa"):
        return "brand_or_generic"
    if re.search(r"\b(motorcycle|motorbike)\b", query) and re.search(r"\b(transport|shipping|freight|movers|removals|courier)\b", query):
        return "commercial"
    return "ambiguous"


def expected_target(query, plan):
    exact = next((t for t in plan["targets"] if normal_query(t["query"]) == query), None)
    if exact:
        return exact
    cities = [m for m in plan["markets"][1:] if m.lower() in query]
    if len(cities) >= 2:
        match = next((t for t in plan["targets"] if set(t["markets"]) == set(cities)), None)
        if match:
            return match
    if cities:
        return next((t for t in plan["targets"] if t["markets"] == cities), {"markets": cities, "expected_page": None, "mapping_note": "Route page match needs review."})
    if any(word in query for word in ("cost", "price", "cheap", "quote")):
        return plan["targets"][-1]
    return plan["targets"][0]


def build_commercial_plan(gsc, plan=None):
    plan = plan or load_plan()
    current = gsc.get("analytics", {})
    previous = gsc.get("previous_analytics", {})
    groups = {}
    for row in current.get("rows", []):
        groups.setdefault(normal_query(row.get("query", "")), []).append(row)
    for target in plan["targets"]:
        groups.setdefault(normal_query(target["query"]), [])
    demand = {normal_query(row["query"]): row["monthly_searches"] for row in plan["demand_snapshot"]["estimates"]}
    mapping, ranked = [], []
    for query, observations in groups.items():
        target = expected_target(query, plan)
        kind = intent(query)
        expected = target.get("expected_page")
        matched = [r for r in observations if expected and page_key(r.get("page")) == page_key(expected)]
        status = ("not_observed_in_returned_data" if not observations else
                  "mapping_review" if not expected else
                  "mapped" if matched else "different_page_observed")
        entry = {"query": query, "intent": kind, "markets": target["markets"], "expected_page": expected,
                 "mapping_status": status, "mapping_note": target.get("mapping_note"),
                 "monthly_search_estimate": demand.get(query), "observations": observations}
        mapping.append(entry)
        if kind != "commercial":
            continue
        # Preserve page-specific metrics; never sum overlapping URL impressions.
        by_page = {}
        for row in observations:
            key = page_key(row.get("page"))
            if key not in by_page or row.get("impressions", 0) > by_page[key].get("impressions", 0):
                by_page[key] = row
        for row in by_page.values():
            scored = score_gsc_rows([{**row, "keyword": query}])[0]
            prior = next((r for r in previous.get("rows", []) if normal_query(r.get("query", "")) == query and r.get("page") == row.get("page")), None)
            scored.update({"markets": target["markets"], "expected_page": expected, "mapping_status": status,
                           "previous_impressions": prior.get("impressions") if prior else None,
                           "previous_clicks": prior.get("clicks") if prior else None,
                           "impression_change": row.get("impressions", 0) - prior.get("impressions", 0) if prior else None,
                           "comparison_note": "Matched query and exact URL" if prior else "No matching row returned; absence is not treated as zero."})
            ranked.append(scored)
    ranked.sort(key=lambda row: row["opportunity_score"], reverse=True)
    coverage = [{"market": market,
                 "pilot_queries": sum(market in t["markets"] for t in plan["targets"]),
                 "observed_commercial_queries": sum(market in m["markets"] and m["intent"] == "commercial" and bool(m["observations"]) for m in mapping),
                 "mapping_reviews": sum(market in m["markets"] and not m["expected_page"] and m["intent"] == "commercial" for m in mapping)} for market in plan["markets"]]
    lead = ranked[0] if ranked else None
    action = ("Review the page match for “" + lead["keyword"] + "”, then prepare one improvement proposal for approval." if lead else
              "Validate demand and page coverage before proposing a website change.")
    return {"markets": coverage, "keyword_to_page_map": mapping, "top_opportunities": ranked[:30],
            "recommended_action": action, "current_window": current.get("date_range"), "previous_window": previous.get("date_range"),
            "demand_snapshot": plan["demand_snapshot"], "mobile_rows": gsc.get("mobile_organic_baseline", {}).get("rows", []),
            "rank_capture": plan["rank_capture"], "website_changes_require_approval": True,
            "excluded_from_priority": {label: sum(m["intent"] == label for m in mapping) for label in ("brand_or_generic", "ambiguous", "outside_pilot")},
            "sampling_note": "Returned GSC query/page rows, not site totals. No returned row is not proof of no demand. URL variants are not added together.",
            "write_actions_taken": 0}

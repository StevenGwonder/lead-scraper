#!/usr/bin/env python3
"""
Construction Lead Qualifier — Scores leads from the scout skill.
Flags hot (8+), warm (5-7), and drops cold (<5) with reasons.

Usage:
  python3 scout.py --trade roofing --city "Riverside, CA" | python3 qualify.py
  python3 qualify.py --input leads.json
  python3 qualify.py --trade roofing --city "Riverside, CA" --input leads.json
"""

import argparse
import json
import sys
import re
from datetime import datetime, timezone

# ─── Scoring Constants ────────────────────────────────────────────────

# Urgency signals (case-insensitive match in snippet)
URGENCY_KEYWORDS = [
    "now hiring", "immediately", "urgent", "asap", "hiring today",
    "apply now", "open now", "deadline", "closing date", "bid due",
    "must have", "start immediately", "positions available",
]

# Project size signals
SIZE_SIGNALS_LARGE = [
    "commercial", "industrial", "50,000", "50000", "100,000", "100000",
    "large project", "major project", "multi-family", "high-rise",
    "hospital", "school district", "government building",
]
SIZE_SIGNALS_MID = [
    "full-time", "experienced crew", "foreman", "superintendent",
    "project manager", "estimator", "multiple positions",
    "team lead", "crew lead",
]
SIZE_DOLLAR_PATTERNS = [
    r"\$[\d,]+",  # $50,000 etc
    r"\d[\d,]+ sq",  # square footage
    r"\d+ roof",  # "50 roof"
]

# Location match patterns
LOCATION_EXACT = []  # filled dynamically from --city
LOCATION_REGION = []  # filled dynamically from --region
LOCATION_STATE = []  # filled dynamically from --state

# Profile quality signals
QUALITY_HIGH = [
    "contact", "email", "phone", "call", "@",  # contact info
    "rfp", "bid package", "scope of work", "proposal",
    "qualifications", "pre-qualification", "submittal",
]
QUALITY_LOW = [
    "browse", "search results", "1-click apply", "sign up",
    "create account", "register to view",
]
# Informational content signals — not actual leads
INFORMATIONAL_SIGNALS = [
    "news", "article", "blog", "outlook", "forecast",
    "market analysis", "industry report", "trends",
    "opinion", "editorial", "podcast", "newsletter",
]


def score_project_size(snippet: str, min_size: int = 5000) -> int:
    """Score project size fit 0-3."""
    snippet_lower = snippet.lower()

    # Check for dollar amounts — extract and compare to min_size
    dollar_matches = re.findall(r"\$([\d,]+)", snippet)
    for amt_str in dollar_matches:
        amt = int(amt_str.replace(",", ""))
        if amt >= min_size:
            return 3
        if amt >= min_size * 0.3:
            return 2

    # Check for square footage
    sq_matches = re.findall(r"([\d,]+)\s*sq", snippet_lower)
    for sq_str in sq_matches:
        sq = int(sq_str.replace(",", ""))
        if sq >= 5000:
            return 3
        if sq >= 1000:
            return 2

    # Check keyword signals
    if any(kw in snippet_lower for kw in SIZE_SIGNALS_LARGE):
        return 3
    if any(kw in snippet_lower for kw in SIZE_SIGNALS_MID):
        return 2

    # Mention of the trade at all
    return 1 if any(w in snippet_lower for w in ["roof", "remodel", "construct", "build", "renovat", "contract"]) else 0


def score_urgency(snippet: str) -> int:
    """Score deadline urgency 0-2."""
    snippet_lower = snippet.lower()
    if any(kw in snippet_lower for kw in URGENCY_KEYWORDS):
        return 2
    # Any hiring/job posting signal
    if any(w in snippet_lower for w in ["hiring", "jobs", "career", "position", "employment"]):
        return 1
    return 0


def score_location(snippet: str, url: str, city: str, region: str, state: str,
                   city_searched: str = "") -> int:
    """Score location match 0-3.
    
    For multi-city briefings, city_searched tells us which area this lead
    came from. The primary city (city param) scores 3, neighboring areas
    in the same region score 2-3 depending on closeness.
    """
    combined = (snippet + " " + url).lower()
    city_lower = city.lower()
    region_lower = region.lower()
    state_lower = state.lower()

    # Exact primary city match
    city_parts = [p.strip().lower() for p in city.split(",")]
    city_name = city_parts[0] if city_parts else city_lower

    # Check primary city
    if city_name in combined:
        return 3
    
    # Check city_searched (which area the scout found this in)
    if city_searched:
        cs_lower = city_searched.lower()
        if cs_lower == city_lower:
            return 3
        # Neighboring counties in the same region get 2-3
        nearby_counties = {
            "riverside": ["riverside", "inland empire"],
            "orange county": ["orange county", "orange", "santa ana", "anaheim", "irvine"],
            "san diego": ["san diego", "sd"],
        }
        primary_lower = city_lower.lower()
        for county_key, aliases in nearby_counties.items():
            if primary_lower in aliases or any(a in primary_lower for a in aliases):
                # Primary is in this county group
                if cs_lower in aliases or any(a in cs_lower for a in aliases):
                    return 3  # Same county group
                break
    
    if region_lower and region_lower in combined:
        return 3
    # State match (abbr or full)
    state_abbr = city_parts[1].strip() if len(city_parts) > 1 else ""
    if state_lower in combined or (state_abbr and state_abbr.lower() in combined):
        return 2
    # "California" or "CA" type matches
    if state_abbr.lower() in combined:
        return 2
    if "united states" in combined or "usa" in combined:
        return 1
    return 1  # Default: assume relevant since we searched for the city


def score_profile_quality(snippet: str, url: str) -> int:
    """Score lead profile quality 0-2."""
    combined = (snippet + " " + url).lower()

    # Informational/news content — not an actionable lead
    if any(kw in combined for kw in INFORMATIONAL_SIGNALS):
        return 0

    # Government/bid portal pages are high quality
    if any(d in combined for d in [".gov", "bid-opportunit", "rfp", "purchasing.ca", "publicpurchase"]):
        return 2

    # Company career pages
    if any(d in combined for d in ["/careers", "/jobs/", "/employment", "company"]):
        if not any(ag in combined for ag in ["indeed.com", "ziprecruiter", "simplyhired", "glassdoor"]):
            return 2

    # Has contact info or specific project details
    if any(kw in combined for kw in QUALITY_HIGH):
        return 2

    # Job board listings with some detail
    if any(w in combined for w in ["hiring", "jobs", "position"]):
        return 1

    # Generic aggregator
    if any(kw in combined for kw in QUALITY_LOW):
        return 0

    return 1


def qualify_lead(lead: dict, city: str, region: str, state: str, min_size: int = 5000) -> dict:
    """Score a single lead and return with score and breakdown."""
    snippet = lead.get("snippet", "")
    url = lead.get("url", "")
    city_searched = lead.get("city_searched", "")

    size_score = score_project_size(snippet, min_size)
    urgency_score = score_urgency(snippet)
    location_score = score_location(snippet, url, city, region, state, city_searched)
    quality_score = score_profile_quality(snippet, url)

    total = size_score + urgency_score + location_score + quality_score

    result = dict(lead)
    result["score"] = total
    result["score_breakdown"] = {
        "project_size_fit": size_score,
        "deadline_urgency": urgency_score,
        "location_match": location_score,
        "profile_quality": quality_score,
    }

    # Generate reason
    if total >= 8:
        reasons = []
        if size_score >= 2:
            reasons.append("strong project size signal")
        if urgency_score == 2:
            reasons.append("urgent hiring")
        if location_score >= 2:
            reasons.append("local match")
        if quality_score >= 2:
            reasons.append("quality source")
        result["why_hot"] = " | ".join(reasons)
    elif total >= 5:
        reasons = []
        if size_score < 2:
            reasons.append("unclear project size")
        if urgency_score < 2:
            reasons.append("no urgency signals")
        if location_score < 3:
            reasons.append("location could be closer")
        if quality_score < 2:
            reasons.append("generic listing")
        result["why_warm"] = " | ".join(reasons)
    else:
        reasons = []
        if size_score == 0:
            reasons.append("no project size info")
        if urgency_score == 0:
            reasons.append("not actively hiring")
        if location_score < 2:
            reasons.append("wrong location")
        if quality_score == 0:
            reasons.append("low-quality source")
        result["why_cold"] = " | ".join(reasons)

    return result


def qualify_leads(leads: list, city: str, region: str, state: str, min_size: int = 5000) -> dict:
    """Qualify all leads and return categorized results."""
    scored = []
    for lead in leads:
        scored.append(qualify_lead(lead, city, region, state, min_size))

    hot = [l for l in scored if l["score"] >= 8]
    warm = [l for l in scored if 5 <= l["score"] < 8]
    cold = [l for l in scored if l["score"] < 5]

    # Sort each bucket by score descending
    hot.sort(key=lambda x: x["score"], reverse=True)
    warm.sort(key=lambda x: x["score"], reverse=True)
    cold.sort(key=lambda x: x["score"], reverse=True)

    top_lead = hot[0]["title"] if hot else (warm[0]["title"] if warm else "None")

    return {
        "hot": hot,
        "warm": warm,
        "cold": cold,
        "summary": {
            "total_input": len(leads),
            "hot_count": len(hot),
            "warm_count": len(warm),
            "cold_count": len(cold),
            "top_lead": top_lead,
            "qualified_at": datetime.now(timezone.utc).isoformat() + "Z",
        }
    }


def main():
    parser = argparse.ArgumentParser(description="Construction Lead Qualifier")
    parser.add_argument("--input", default="",
                        help="Input JSON file (from scout). Default: read stdin")
    parser.add_argument("--trade", default="roofing",
                        help="Trade (for context)")
    parser.add_argument("--city", default="Riverside, CA",
                        help="Client city, ST")
    parser.add_argument("--region", default="Inland Empire",
                        help="Client region")
    parser.add_argument("--state", default="California",
                        help="Client state (full name)")
    parser.add_argument("--min-size", type=int, default=5000,
                        help="Minimum project size in dollars")
    parser.add_argument("--output", default="",
                        help="Output file path (default: stdout)")
    parser.add_argument("--brief", action="store_true",
                        help="Output a human-readable briefing instead of JSON")

    args = parser.parse_args()

    # Load leads
    if args.input:
        with open(args.input) as f:
            leads = json.load(f)
    else:
        leads = json.load(sys.stdin)

    print(f"=== Construction Lead Qualifier ===", file=sys.stderr)
    print(f"  Input:     {len(leads)} leads", file=sys.stderr)
    print(f"  City:      {args.city}", file=sys.stderr)
    print(f"  Region:    {args.region}", file=sys.stderr)
    print(f"  Min Size:  ${args.min_size:,}", file=sys.stderr)
    print(file=sys.stderr)

    result = qualify_leads(
        leads,
        city=args.city,
        region=args.region,
        state=args.state,
        min_size=args.min_size,
    )

    s = result["summary"]
    print(f"  HOT:   {s['hot_count']} leads", file=sys.stderr)
    print(f"  WARM:  {s['warm_count']} leads", file=sys.stderr)
    print(f"  COLD:  {s['cold_count']} leads", file=sys.stderr)
    print(file=sys.stderr)

    if args.brief:
        # Human-readable briefing format
        output = format_briefing(result, args.trade, args.city)
    else:
        output = json.dumps(result, indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"  Wrote results to {args.output}", file=sys.stderr)
    else:
        print(output)


def format_briefing(result: dict, trade: str, city: str) -> str:
    """Format qualified leads as a human-readable briefing."""
    lines = []
    s = result["summary"]

    lines.append(f"🏗️  CONSTRUCTION LEAD BRIEFING — {trade.upper()} in {city}")
    lines.append(f"{'=' * 50}")
    lines.append(f"Found {s['total_input']} leads → {s['hot_count']} hot, {s['warm_count']} warm, {s['cold_count']} cold")
    lines.append("")

    if result["hot"]:
        lines.append(f"🔥 HOT LEADS — Act Now ({s['hot_count']})")
        lines.append("-" * 40)
        for i, lead in enumerate(result["hot"], 1):
            lines.append(f"  {i}. {lead['title']}")
            if lead.get("why_hot"):
                lines.append(f"     Why: {lead['why_hot']}")
            lines.append(f"     Score: {lead['score']}/10 | {lead['url']}")
            if lead.get("snippet"):
                lines.append(f"     {lead['snippet'][:120]}...")
            lines.append("")

    if result["warm"]:
        lines.append(f"🌤️  WARM LEADS — Review This Week ({s['warm_count']})")
        lines.append("-" * 40)
        for i, lead in enumerate(result["warm"], 1):
            lines.append(f"  {i}. {lead['title']}")
            if lead.get("why_warm"):
                lines.append(f"     Note: {lead['why_warm']}")
            lines.append(f"     Score: {lead['score']}/10 | {lead['url'][:80]}")
            lines.append("")

    if result["cold"]:
        lines.append(f"❄️  COLD — Dropped ({s['cold_count']})")
        lines.append("-" * 40)
        cold_sample = result["cold"][:5]
        for i, lead in enumerate(cold_sample, 1):
            reason = lead.get("why_cold", "low relevance")
            lines.append(f"  {i}. {lead['title'][:60]} — {reason}")
        if s['cold_count'] > 5:
            lines.append(f"  ... and {s['cold_count'] - 5} more dropped")
        lines.append("")

    lines.append(f"Top lead: {s['top_lead']}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
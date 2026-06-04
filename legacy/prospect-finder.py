#!/usr/bin/env python3
"""
AI Employee Prospect Finder — Finds businesses across playbook target industries
that are actively seeking AI, automation, or digital worker solutions.

This is Steven's OWN lead generator — finding $5k/mo clients, not construction leads.

Target industries per the playbook:
  - Marketing agencies
  - Law firms
  - Insurance agencies
  - Manufacturers
  - Wholesalers
  - Real estate agencies

Searches SearXNG for businesses posting about AI needs, automation pain points,
and digital transformation signals. Outputs structured JSON prospects.

Usage:
  python3 prospect-finder.py --industries marketing law insurance
  python3 prospect-finder.py --all --cities "Riverside, CA" "Orange County, CA" "San Diego, CA"
  python3 prospect-finder.py --industry realestate --city "Miami, FL"
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import re
from datetime import datetime, timezone

SEARXNG_URL = "http://localhost:8888"
DEFAULT_LIMIT = 10
DEFAULT_DELAY = 3.0

# ─── Industry-Specific Prospect Search Templates ──────────────────────

INDUSTRY_QUERIES = {
    "marketing": {
        "label": "Marketing Agencies",
        "icon": "📣",
        "queries": [
            "marketing agency hiring AI automation",
            "marketing agency looking for AI tools",
            "marketing agency digital transformation",
            "marketing agency needs automation",
            "marketing agency AI employee wanted",
            "marketing agency struggling with capacity client work",
            "small marketing agency growth growing fast",
            "marketing agency overwhelmed client requests",
        ],
    },
    "law": {
        "label": "Law Firms",
        "icon": "⚖️",
        "queries": [
            "law firm hiring AI automation",
            "law firm looking for legal AI tools",
            "law firm digital transformation",
            "law firm needs case management automation",
            "law firm AI assistant wanted",
            "small law firm growing fast overwhelmed",
            "law firm struggling with intake follow-ups",
            "law firm wants to automate client communication",
        ],
    },
    "insurance": {
        "label": "Insurance Agencies",
        "icon": "🛡️",
        "queries": [
            "insurance agency hiring AI automation",
            "insurance agency looking for AI tools",
            "insurance agency digital transformation",
            "insurance agency needs automation",
            "insurance agency AI employee wanted",
            "insurance agency overwhelmed claims follow-ups",
            "insurance agency struggling with lead response time",
            "insurance agency wants CRM automation",
        ],
    },
    "manufacturing": {
        "label": "Manufacturers",
        "icon": "🏭",
        "queries": [
            "manufacturing company hiring AI automation",
            "manufacturer looking for AI tools production",
            "manufacturer digital transformation",
            "manufacturer needs supply chain automation",
            "manufacturer AI employee wanted",
            "manufacturer struggling with paperwork compliance",
            "small manufacturer growing fast overwhelmed",
            "manufacturer wants to automate quoting process",
        ],
    },
    "wholesale": {
        "label": "Wholesalers",
        "icon": "📦",
        "queries": [
            "wholesale distributor hiring AI automation",
            "wholesale distributor looking for AI tools",
            "wholesale distributor digital transformation",
            "wholesale distributor needs inventory automation",
            "wholesale distributor AI employee wanted",
            "wholesale distributor struggling with order management",
            "small wholesale distributor growing fast",
            "wholesale distributor wants to automate reordering",
        ],
    },
    "realestate": {
        "label": "Real Estate Agencies",
        "icon": "🏠",
        "queries": [
            "real estate agency hiring AI automation",
            "real estate agency looking for AI tools",
            "real estate agency digital transformation",
            "real estate agency needs lead follow-up automation",
            "real estate agency AI assistant wanted",
            "real estate agency overwhelmed showing requests",
            "real estate agency struggling with client follow-ups",
            "commercial real estate agency wants CRM automation",
        ],
    },
}

# Geographic broadening — add location to queries
GEO_TEMPLATES = [
    "{industry_query} {city}",
    "{industry_query} {state}",
    "{industry_query} {region}",
]

# Direct buying-signal queries — people actively shopping
BUYING_SIGNAL_QUERIES = [
    "hire AI virtual assistant for business",
    "AI employee for small business cost",
    "automate my business with AI",
    "AI agent for business operations",
    "digital worker for business affordable",
    "business automation service monthly",
    "AI as a service for small business",
    "managed AI service for executives",
]


def search_searxng(query: str, limit: int = 10) -> tuple:
    """Run a single SearXNG search, return (results, unresponsive_engines)."""
    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "pageno": 1,
    })
    url = f"{SEARXNG_URL}/search?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AIProspectFinder/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("results", [])[:limit]
        unresponsive = data.get("unresponsive_engines", [])
        return results, unresponsive
    except Exception as e:
        print(f"  [WARN] Search failed for '{query}': {e}", file=sys.stderr)
        return [], []


def make_prospect(raw: dict, industry: str, query_used: str,
                  city_label: str = "", signal_type: str = "need") -> dict:
    """Transform a SearXNG result into a structured prospect."""
    prospect = {
        "title": raw.get("title", "").strip(),
        "url": raw.get("url", "").strip(),
        "snippet": raw.get("content", "").strip(),
        "source_engines": raw.get("engines", []),
        "industry": industry,
        "industry_label": INDUSTRY_QUERIES.get(industry, {}).get("label", industry),
        "industry_icon": INDUSTRY_QUERIES.get(industry, {}).get("icon", "🔍"),
        "query_used": query_used,
        "signal_type": signal_type,  # "need", "growth", "buying"
        "relevance_score": round(raw.get("score", 0), 2),
        "scraped_at": datetime.now(timezone.utc).isoformat() + "Z",
    }
    if city_label:
        prospect["city_searched"] = city_label
    return prospect


def deduplicate(prospects: list) -> list:
    """Remove duplicate URLs."""
    seen = set()
    unique = []
    for p in prospects:
        url = p["url"]
        try:
            parsed = urllib.parse.urlparse(url)
            key = (parsed.netloc, parsed.path.rstrip("/"))
        except Exception:
            key = url
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def classify_signal(snippet: str, title: str) -> str:
    """Classify the buying signal strength of a prospect."""
    text = (snippet + " " + title).lower()

    # Direct buying signals — they want AI/automation NOW
    buying_words = ["hire", "looking for", "wanted", "need", "seeking", "shopping",
                     "cost", "price", "affordable", "service", "as a service",
                     "monthly", "subscription", "help me", "recommend"]
    growth_words = ["growing", "expanding", "overwhelmed", "struggling", "fast",
                    "hiring", "new office", "scaling", "can't keep up", "bottleneck"]
    need_words = ["automate", "ai", "digital", "tools", "software", "crm",
                  "workflow", "efficiency", "productivity", "transformation"]

    buying_score = sum(1 for w in buying_words if w in text)
    growth_score = sum(1 for w in growth_words if w in text)
    need_score = sum(1 for w in need_words if w in text)

    if buying_score >= 2:
        return "buying"
    elif growth_score >= 2:
        return "growth"
    elif need_score >= 2 or buying_score >= 1:
        return "need"
    else:
        return "awareness"


def score_prospect(prospect: dict) -> int:
    """Score a prospect 1-10 for Steven's $5k/mo AI employee service."""
    score = 3  # base score
    snippet = (prospect.get("snippet", "") + " " + prospect.get("title", "")).lower()
    signal = prospect.get("signal_type", "awareness")

    # Signal type scoring
    if signal == "buying":
        score += 4
    elif signal == "growth":
        score += 3
    elif signal == "need":
        score += 2
    elif signal == "awareness":
        score += 0

    # Industry fit scoring — best targets get higher scores
    industry = prospect.get("industry", "")
    high_fit = {"marketing": 2, "realestate": 2, "law": 1, "insurance": 1}
    score += high_fit.get(industry, 0)

    # Urgency signals
    urgency_words = ["now", "immediately", "asap", "hiring", "urgent", "fast",
                     "struggling", "overwhelmed", "can't keep up", "bottleneck",
                     "growing", "expanding"]
    for word in urgency_words:
        if word in snippet:
            score += 1
            break  # only count once

    # Size signals — small-to-mid businesses are ideal ($5k/mo is right)
    size_words = ["small", "mid-size", "family", "local", "independent", "boutique"]
    for word in size_words:
        if word in snippet:
            score += 1
            break

    # Negative signals — too big = enterprise, too regulated
    negative_words = ["enterprise", "fortune 500", "hospital", "bank", "fintech",
                      "healthcare", "pharma", "federal", "government contract",
                      "compliance officer", "regulatory"]
    for word in negative_words:
        if word in snippet:
            score -= 2
            break

    return max(1, min(10, score))


def run_prospect_finder(industries: list, cities: list, state: str = "",
                        region: str = "", limit: int = DEFAULT_LIMIT,
                        delay: float = DEFAULT_DELAY) -> list:
    """Run searches across all target industries and cities."""
    all_prospects = []
    total_queries = 0

    for industry_key, industry_data in industries.items():
        label = industry_data["label"]
        queries = industry_data["queries"]

        print(f"\n  === {label} ===", file=sys.stderr)

        for query_base in queries:
            # Search without location first (broader reach)
            query = query_base
            total_queries += 1
            print(f"  Searching: {query}", file=sys.stderr)
            results, _ = search_searxng(query, limit=limit)

            for raw in results:
                signal = classify_signal(
                    raw.get("content", ""), raw.get("title", "")
                )
                p = make_prospect(raw, industry_key, query,
                                  city_label="", signal_type=signal)
                all_prospects.append(p)
            time.sleep(delay)

            # Add location-specific queries for each city
            for city in cities:
                query_geo = f"{query_base} {city}"
                total_queries += 1
                print(f"  Searching: {query_geo}", file=sys.stderr)
                results, _ = search_searxng(query_geo, limit=limit)

                for raw in results:
                    signal = classify_signal(
                        raw.get("content", ""), raw.get("title", "")
                    )
                    p = make_prospect(raw, industry_key, query_geo,
                                      city_label=city, signal_type=signal)
                    all_prospects.append(p)
                time.sleep(delay)

    # Direct buying-signal searches (no industry filter)
    print(f"\n  === Direct Buying Signals ===", file=sys.stderr)
    for query_base in BUYING_SIGNAL_QUERIES:
        query = query_base
        total_queries += 1
        print(f"  Searching: {query}", file=sys.stderr)
        results, _ = search_searxng(query, limit=limit)

        for raw in results:
            signal = classify_signal(
                raw.get("content", ""), raw.get("title", "")
            )
            p = make_prospect(raw, "direct", query,
                              city_label="", signal_type="buying")
            all_prospects.append(p)
        time.sleep(delay)

        # Location-specific buying signals
        for city in cities:
            query_geo = f"{query_base} {city}"
            total_queries += 1
            print(f"  Searching: {query_geo}", file=sys.stderr)
            results, _ = search_searxng(query_geo, limit=limit)

            for raw in results:
                signal = classify_signal(
                    raw.get("content", ""), raw.get("title", "")
                )
                p = make_prospect(raw, "direct", query_geo,
                                  city_label=city, signal_type="buying")
                all_prospects.append(p)
            time.sleep(delay)

    # Deduplicate and score
    prospects = deduplicate(all_prospects)
    for p in prospects:
        p["score"] = score_prospect(p)

    prospects.sort(key=lambda x: x["score"], reverse=True)

    print(f"\n  Total: {len(all_prospects)} raw → {len(prospects)} unique prospects from {total_queries} queries", file=sys.stderr)

    return prospects


def format_telegram_prospect_briefing(prospects: list, areas: list) -> str:
    """Format prospects for Telegram delivery in the playbook's voice."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%A, %B %d")
    area_label = ", ".join(areas) if len(areas) <= 3 else f"{len(areas)} areas"

    # Categorize by score
    hot = [p for p in prospects if p["score"] >= 8]
    warm = [p for p in prospects if 5 <= p["score"] < 8]
    cold = [p for p in prospects if p["score"] < 5]

    lines = []
    lines.append(f"🎯 PROSPECT BRIEFING — {now}")
    lines.append(f"Your AI employee scanned {area_label} for $5k/mo clients.")
    lines.append(f"Found: {len(hot)} 🔥 hot, {len(warm)} 🌤️ warm, {len(cold)} noise filtered")
    lines.append("")

    # Hot prospects — full detail
    if hot:
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🔥 HOT PROSPECTS — Call These Today ({len(hot)})")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        for i, p in enumerate(hot[:8], 1):
            icon = p.get("industry_icon", "🎯")
            label = p.get("industry_label", p.get("industry", ""))
            lines.append(f"")
            lines.append(f"#{i} — {icon} {label} (Score {p['score']}/10)")
            lines.append(f"📋 {p['title']}")
            if p.get("snippet"):
                lines.append(f"💬 {p['snippet'][:200]}")
            signal = p.get("signal_type", "")
            if signal == "buying":
                lines.append(f"✅ BUYING SIGNAL — actively seeking AI/automation")
            elif signal == "growth":
                lines.append(f"✅ GROWTH SIGNAL — growing fast, needs help")
            elif signal == "need":
                lines.append(f"✅ NEED SIGNAL — pain point matches our offer")
            if p.get("city_searched"):
                lines.append(f"📍 {p['city_searched']}")
            lines.append(f"🔗 {p['url']}")

            # Pitch angle suggestion
            lines.append(f"💡 Pitch angle: {_pitch_angle(p)}")
    else:
        lines.append("No hot prospects today. Your worker broadened the search.")

    lines.append("")

    # Warm prospects — compact
    if warm:
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🌤️ WARM — Good Prospects, Warm Up First ({len(warm)})")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        for i, p in enumerate(warm[:7], 1):
            icon = p.get("industry_icon", "🎯")
            label = p.get("industry_label", p.get("industry", ""))
            city_tag = f" [{p['city_searched']}]" if p.get("city_searched") else ""
            lines.append(f"  {i}. {icon} {label}: {p['title'][:55]}{city_tag}")
            lines.append(f"     🔗 {p['url'][:75]}")
        if len(warm) > 7:
            lines.append(f"  ... {len(warm) - 7} more warm prospects available")

    # Industry breakdown
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("📊 INDUSTRY BREAKDOWN")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")

    industry_counts = {}
    for p in hot + warm:
        ind = p.get("industry_label", p.get("industry", "other"))
        industry_counts[ind] = industry_counts.get(ind, 0) + 1

    for ind, count in sorted(industry_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  • {ind}: {count} prospects")

    # Outreach reminder in the playbook's voice
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("📞 OUTREACH PLAYBOOK REMINDER")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("Target: The EXECUTIVE, not IT.")
    lines.append("Sell: Business outcomes & revenue, NOT time saved.")
    lines.append("Never mention: Tokens, models, infrastructure, limits.")
    lines.append("Magic words: \"We install a digital employee that knows your business and gets better every week.\"")
    lines.append("Close: 48-hour guarantee — first agent running in under 2 days.")
    lines.append("")
    lines.append("— Your AI Employee Scout @ North Web Pro")

    return "\n".join(lines)


def _pitch_angle(prospect: dict) -> str:
    """Generate a pitch angle based on industry and signal type."""
    industry = prospect.get("industry", "")
    signal = prospect.get("signal_type", "")
    snippet = (prospect.get("snippet", "") + " " + prospect.get("title", "")).lower()

    angles = {
        "marketing": "Stop being the bottleneck — your AI employee handles client reporting, content drafts, and follow-ups 24/7.",
        "law": "Your AI employee handles intake, document review prep, and client follow-ups so your attorneys bill more hours.",
        "insurance": "AI employee handles claims follow-ups, quote comparisons, and renewal reminders — your producers close more.",
        "manufacturing": "AI employee automates quoting, PO tracking, and supplier follow-ups — you ship faster.",
        "wholesale": "AI employee manages reorder alerts, customer follow-ups, and inventory reports — you focus on relationships.",
        "realestate": "AI employee handles lead follow-up, showing scheduling, and listing updates — you close more deals.",
    }

    if signal == "buying":
        base = angles.get(industry, "They're actively looking — fastest close in your pipeline.")
    elif signal == "growth":
        base = angles.get(industry, "Growing fast — needs automation before they drown. Perfect for a 48-hour demo.")
    else:
        base = angles.get(industry, "Pain point matches our offer — warm them up with content first.")

    return base


def main():
    parser = argparse.ArgumentParser(description="AI Employee Prospect Finder")
    parser.add_argument("--industries", nargs="+",
                        choices=list(INDUSTRY_QUERIES.keys()),
                        default=list(INDUSTRY_QUERIES.keys()),
                        help="Target industries to search")
    parser.add_argument("--all", action="store_true",
                        help="Search all industries (default)")
    parser.add_argument("--industry", choices=list(INDUSTRY_QUERIES.keys()),
                        help="Single industry shortcut")
    parser.add_argument("--city", default="",
                        help="Single city (overrides --cities)")
    parser.add_argument("--cities", nargs="+", default=[],
                        help="Cities to target")
    parser.add_argument("--state", default="California",
                        help="State for geo queries")
    parser.add_argument("--region", default="SoCal",
                        help="Region for geo queries")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="Max results per query")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help="Seconds between queries")
    parser.add_argument("--output", default="",
                        help="Output file path (default: stdout)")
    parser.add_argument("--format", choices=["json", "briefing"],
                        default="json",
                        help="Output format")

    args = parser.parse_args()

    if args.industry:
        args.industries = [args.industry]
    elif args.industries == list(INDUSTRY_QUERIES.keys()) and not args.all:
        # Default is all industries
        pass

    if args.city and not args.cities:
        args.cities = [args.city]

    # Default to Steven's territory
    if not args.cities:
        args.cities = ["Riverside, CA", "Orange County, CA", "San Diego, CA"]

    # Select only requested industries
    industries = {k: INDUSTRY_QUERIES[k] for k in args.industries
                 if k in INDUSTRY_QUERIES}

    print(f"=== AI Employee Prospect Finder ===", file=sys.stderr)
    print(f"  Industries: {', '.join(args.industries)}", file=sys.stderr)
    print(f"  Cities: {', '.join(args.cities)}", file=sys.stderr)
    print(f"  State: {args.state}", file=sys.stderr)
    print(f"  Region: {args.region}", file=sys.stderr)

    prospects = run_prospect_finder(
        industries=industries,
        cities=args.cities,
        state=args.state,
        region=args.region,
        limit=args.limit,
        delay=args.delay,
    )

    if args.format == "briefing":
        output = format_telegram_prospect_briefing(prospects, args.cities)
    else:
        output = json.dumps(prospects, indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"  Wrote {len(prospects)} prospects to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
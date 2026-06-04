#!/usr/bin/env python3
"""
Construction Lead Scout — Searches SearXNG for construction job postings, RFPs,
bid opportunities, and contractor wanted ads. Outputs structured JSON leads.

Supports multi-city search with auto-broadening when results are thin.

Usage:
  python3 scout.py --trade roofing --cities "Riverside, CA" "Orange County, CA" "San Diego, CA"
  python3 scout.py --trade roofing --city "Riverside, CA"
  python3 scout.py --trade remodeling --city "Denver, CO" --state "Colorado" --limit 15
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
DEFAULT_DELAY = 3.0  # seconds between requests (lower triggers CAPTCHA)
KNOWN_ENGINES = {"google", "brave", "duckduckgo", "startpage", "wikipedia", "bing"}

# Primary search templates — focused on the trade + city
QUERY_TEMPLATES = {
    "direct_jobs": [
        "{trade} contractor jobs hiring {city}",
        "{trade} jobs {city}",
    ],
    "rfp_bids": [
        "{trade} RFP bid opportunity {state}",
        "{trade} bid request {city}",
    ],
    "project_leads": [
        "{trade} project wanted {city}",
        "{trade} contractor needed {region}",
    ],
    "subcontractor": [
        "{trade} subcontractor needed {region}",
        "{trade} sub wanted {city}",
    ],
    "government": [
        "government {trade} contract {state}",
        "municipal {trade} bid {city}",
    ],
    "remodeling": [
        "remodeling contractor wanted {city}",
        "home renovation project {city}",
    ],
    "general_contracting": [
        "general contractor bid opportunity {city}",
        "construction project bid {state}",
    ],
}

# Broadening queries — used when hot leads < 2 after primary search
BROADEN_TEMPLATES = {
    "nearby_cities": [
        "{trade} jobs near {city}",
        "{trade} contractor hiring near {region}",
    ],
    "county_wide": [
        "{county} county {trade} jobs hiring",
        "{trade} contract work {county} county",
    ],
    "trade_variants": [
        "{trade} repair {city}",
        "{trade} installation {city}",
        "{trade} replacement {city}",
    ],
    "broader_projects": [
        "construction project manager {city}",
        "general contractor {city} seeking subs",
        "construction bids {state}",
    ],
}


def search_searxng(query: str, limit: int = 10) -> tuple:
    """Run a single SearXNG search and return (results, unresponsive_engines)."""
    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "pageno": 1,
    })
    url = f"{SEARXNG_URL}/search?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ConstructionLeadScout/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("results", [])[:limit]
        unresponsive = data.get("unresponsive_engines", [])
        return results, unresponsive
    except Exception as e:
        print(f"  [WARN] Search failed for '{query}': {e}", file=sys.stderr)
        return [], []


def make_lead(raw: dict, category: str, query_used: str, city_label: str = "") -> dict:
    """Transform a SearXNG result into a structured lead."""
    lead = {
        "title": raw.get("title", "").strip(),
        "url": raw.get("url", "").strip(),
        "snippet": raw.get("content", "").strip(),
        "source_engines": raw.get("engines", []),
        "search_category": category,
        "query_used": query_used,
        "relevance_score": round(raw.get("score", 0), 2),
        "scraped_at": datetime.now(timezone.utc).isoformat() + "Z",
    }
    if city_label:
        lead["city_searched"] = city_label
    return lead


def deduplicate(leads: list) -> list:
    """Remove duplicate URLs (same domain+path)."""
    seen = set()
    unique = []
    for lead in leads:
        url = lead["url"]
        try:
            parsed = urllib.parse.urlparse(url)
            key = (parsed.netloc, parsed.path.rstrip("/"))
        except Exception:
            key = url
        if key not in seen:
            seen.add(key)
            unique.append(lead)
    return unique


def extract_city_state(city: str) -> tuple:
    """Parse 'Riverside, CA' into ('Riverside', 'CA')."""
    parts = [p.strip() for p in city.split(",")]
    if len(parts) >= 2:
        return parts[0], parts[1].strip()
    return parts[0], ""


def load_profile(path: str) -> dict:
    """Load a Hermes construction profile config."""
    try:
        with open(path) as f:
            content = f.read()
        try:
            import yaml
            config = yaml.safe_load(content)
            return config.get("construction", {})
        except ImportError:
            return parse_construction_section(content)
    except FileNotFoundError:
        print(f"[WARN] Profile not found: {path}", file=sys.stderr)
        return {}


def parse_construction_section(content: str) -> dict:
    """Rough parse of construction: section from YAML."""
    result = {}
    in_section = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("construction:"):
            in_section = True
            continue
        if in_section:
            if stripped and not stripped.startswith("#") and ":" in stripped:
                if not line.startswith("  ") and not line.startswith("\t"):
                    in_section = False
                    continue
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if val:
                    result[key] = val
    return result


def run_scout_for_city(trade: str, city: str, state: str, region: str,
                       county: str, limit: int, delay: float, city_label: str = "") -> list:
    """Run all primary queries for a single city."""
    city_name, state_abbr = extract_city_state(city)
    if not state:
        state = state_abbr if state_abbr else "California"
    if not region:
        region = f"{city_name} area"

    subs = {
        "trade": trade,
        "city": city,
        "state": state,
        "region": region,
        "county": county or city_name,
    }

    all_leads = []
    total_queries = 0
    suspended_engines = set()
    consecutive_empty = 0

    for category, templates in QUERY_TEMPLATES.items():
        for template in templates:
            query = template.format(**subs)
            total_queries += 1
            print(f"  [{city_label or city}] Searching: {query}", file=sys.stderr)
            results, unresponsive = search_searxng(query, limit=limit)

            for entry in unresponsive:
                name = entry[0] if isinstance(entry, (list, tuple)) else entry
                suspended_engines.add(name)

            if results:
                consecutive_empty = 0
                for raw in results:
                    all_leads.append(make_lead(raw, category, query, city_label))
            else:
                consecutive_empty += 1

            if suspended_engines >= KNOWN_ENGINES:
                print(f"  [STOP] All engines suspended — stopping early", file=sys.stderr)
                break

            if consecutive_empty >= 5 and total_queries > 3:
                print(f"  [STOP] 5+ consecutive empty results — likely rate-limited", file=sys.stderr)
                break

            time.sleep(delay)
        else:
            continue
        break

    return all_leads, total_queries, suspended_engines


def run_broaden(trade: str, city: str, state: str, region: str,
                county: str, limit: int, delay: float, city_label: str = "") -> list:
    """Run broadening queries when initial results are thin."""
    city_name, state_abbr = extract_city_state(city)
    if not state:
        state = state_abbr if state_abbr else "California"
    if not region:
        region = f"{city_name} area"

    subs = {
        "trade": trade,
        "city": city,
        "state": state,
        "region": region,
        "county": county or city_name,
    }

    all_leads = []
    total_queries = 0

    for category, templates in BROADEN_TEMPLATES.items():
        for template in templates:
            query = template.format(**subs)
            total_queries += 1
            print(f"  [{city_label or city}] Broadening: {query}", file=sys.stderr)
            results, _ = search_searxng(query, limit=limit)

            if results:
                for raw in results:
                    all_leads.append(make_lead(raw, f"broaden_{category}", query, city_label))

            time.sleep(delay)

    return all_leads, total_queries


def run_scout(trade: str, cities: list, state: str = "", region: str = "",
              counties: list = None, limit: int = DEFAULT_LIMIT,
              delay: float = DEFAULT_DELAY, auto_broaden: bool = True,
              broaden_threshold: int = 2) -> list:
    """Run all search queries across multiple cities with auto-broadening."""
    if not cities:
        cities = ["Riverside, CA"]

    all_leads = []
    total_queries = 0
    all_suspended = set()

    if not counties:
        counties = ["" for _ in cities]

    for i, city in enumerate(cities):
        city_label = city
        county = counties[i] if i < len(counties) else ""
        city_state = state
        city_region = region

        if not city_state:
            _, abbr = extract_city_state(city)
            city_state = abbr if abbr else "California"
        if not city_region:
            city_name, _ = extract_city_state(city)
            city_region = f"{city_name} area"

        print(f"\n  === Scouting: {city} ===", file=sys.stderr)

        leads, queries, suspended = run_scout_for_city(
            trade, city, city_state, city_region, county, limit, delay, city_label
        )
        all_leads.extend(leads)
        total_queries += queries
        all_suspended.update(suspended)

    # Deduplicate across all cities
    leads = deduplicate(all_leads)

    # Auto-broaden if hot leads are thin
    # Re-import the qualifier scoring logic inline for a quick count
    if auto_broaden and len(leads) < 15:
        print(f"\n  [BROADEN] Only {len(leads)} unique leads — running broader searches...", file=sys.stderr)
        for i, city in enumerate(cities):
            county = counties[i] if i < len(counties) else ""
            city_state = state
            city_region = region
            if not city_state:
                _, abbr = extract_city_state(city)
                city_state = abbr if abbr else "California"
            if not city_region:
                city_name, _ = extract_city_state(city)
                city_region = f"{city_name} area"

            broad_leads, broad_queries = run_broaden(
                trade, city, city_state, city_region, county, limit, delay, city
            )
            all_leads.extend(broad_leads)
            total_queries += broad_queries

        leads = deduplicate(all_leads)

    leads.sort(key=lambda x: x["relevance_score"], reverse=True)

    print(f"\n  Total: {len(all_leads)} raw → {len(leads)} unique leads from {total_queries} queries", file=sys.stderr)
    if all_suspended:
        print(f"  Suspended engines: {', '.join(sorted(all_suspended))}", file=sys.stderr)

    return leads


def main():
    parser = argparse.ArgumentParser(description="Construction Lead Scout")
    parser.add_argument("--trade", default="roofing",
                        help="Trade specialty (roofing, remodeling, general, electrical, plumbing)")
    parser.add_argument("--city", default="",
                        help="Single city, ST (e.g. 'Riverside, CA'). Use --cities for multiple.")
    parser.add_argument("--cities", nargs="+", default=[],
                        help="Multiple cities: --cities 'Riverside, CA' 'Orange County, CA' 'San Diego, CA'")
    parser.add_argument("--counties", nargs="+", default=[],
                        help="County names matching cities: --counties Riverside Orange 'San Diego'")
    parser.add_argument("--state", default="",
                        help="Full state name (auto-detected from city if omitted)")
    parser.add_argument("--region", default="",
                        help="Broader region name")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="Max results per query")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help="Seconds between queries (default 3.0)")
    parser.add_argument("--output", default="",
                        help="Output file path (default: stdout)")
    parser.add_argument("--profile", default="",
                        help="Path to Hermes profile config.yaml")
    parser.add_argument("--no-broaden", action="store_true",
                        help="Disable auto-broadening when results are thin")

    args = parser.parse_args()

    # Single city mode
    if args.city and not args.cities:
        args.cities = [args.city]

    # Load profile if specified
    if args.profile:
        prof = load_profile(args.profile)
        if prof:
            args.trade = prof.get("trade", args.trade)
            if not args.cities:
                args.cities = [prof.get("city", "Riverside, CA")]
            args.state = prof.get("state", args.state)
            args.region = prof.get("region", args.region)

    # Fallback default
    if not args.cities:
        args.cities = ["Riverside, CA"]

    print(f"=== Construction Lead Scout ===", file=sys.stderr)
    print(f"  Trade:   {args.trade}", file=sys.stderr)
    print(f"  Cities:  {', '.join(args.cities)}", file=sys.stderr)
    print(f"  State:   {args.state or '(auto)'}", file=sys.stderr)
    print(f"  Region:  {args.region or '(auto)'}", file=sys.stderr)
    print(f"  Limit:   {args.limit} per query", file=sys.stderr)
    print(f"  Delay:   {args.delay}s between queries", file=sys.stderr)
    print(f"  Auto-broaden: {'off' if args.no_broaden else 'on'}", file=sys.stderr)

    leads = run_scout(
        trade=args.trade,
        cities=args.cities,
        state=args.state,
        region=args.region,
        counties=args.counties or None,
        limit=args.limit,
        delay=args.delay,
        auto_broaden=not args.no_broaden,
    )

    output = json.dumps(leads, indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"  Wrote {len(leads)} leads to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
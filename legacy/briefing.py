#!/usr/bin/env python3
"""
Construction Morning Briefing — Zero-token pre-filter script.

Searches multiple counties/cities, scores leads, auto-broadens when results are thin,
and outputs a formatted briefing ready for Telegram delivery.

Usage:
  python3 briefing.py --trade roofing --cities "Riverside, CA" "Orange County, CA" "San Diego, CA"
  python3 briefing.py --profile ~/.hermes/profiles/construction-demo/config.yaml
"""

import argparse
import json
import sys
import os
import subprocess
from datetime import datetime, timezone

SCOUT_SCRIPT = os.path.expanduser("~/.hermes/skills/construction-lead-scout/scripts/scout.py")
QUALIFY_SCRIPT = os.path.expanduser("~/.hermes/skills/construction-lead-qualifier/scripts/qualify.py")


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
                        if val and key not in ("project_types", "specialties", "preferred_platforms", "additional_keywords", "cities", "counties"):
                            result[key] = val
            return result
    except FileNotFoundError:
        return {}


def run_scout(cities: list, trade: str, state: str, region: str,
              counties: list, limit: int, output_path: str) -> list:
    """Run the scout script across multiple cities."""
    cmd = [
        sys.executable, SCOUT_SCRIPT,
        "--trade", trade,
        "--cities"] + cities + [
        "--state", state,
        "--region", region,
        "--limit", str(limit),
        "--output", output_path,
    ]
    if counties:
        cmd = cmd[:6] + ["--counties"] + counties + cmd[6:]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"[WARN] Scout stderr: {result.stderr[:500]}", file=sys.stderr)
        with open(output_path) as f:
            return json.load(f)
    except subprocess.TimeoutExpired:
        print("[ERROR] Scout timed out after 300s", file=sys.stderr)
        return []
    except FileNotFoundError:
        print(f"[ERROR] Scout output not found at {output_path}", file=sys.stderr)
        return []


def run_qualifier(leads_path: str, city: str, region: str, state: str,
                  trade: str, min_size: int = 5000) -> dict:
    """Run the qualifier script and return categorized results."""
    cmd = [
        sys.executable, QUALIFY_SCRIPT,
        "--input", leads_path,
        "--trade", trade,
        "--city", city,  # primary city for scoring
        "--region", region,
        "--state", state,
        "--min-size", str(min_size),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"[ERROR] Qualifier failed: {e}", file=sys.stderr)
        return {"hot": [], "warm": [], "cold": [], "summary": {
            "total_input": 0, "hot_count": 0, "warm_count": 0, "cold_count": 0,
            "top_lead": "None", "qualified_at": ""}}


def format_telegram_briefing(qualified: dict, trade: str, cities: list,
                             business_name: str = "") -> str:
    """Format the briefing for Telegram — concise, emoji-rich, contractor language."""
    s = qualified["summary"]
    now = datetime.now(timezone.utc).strftime("%A, %B %d")
    area_label = ", ".join(cities) if len(cities) <= 3 else f"{len(cities)} areas"

    lines = []
    lines.append(f"🏗️ MORNING BRIEFING — {now}")
    lines.append(f"Your digital worker checked {s['total_input']} sources across {area_label}.")
    lines.append(f"Found: {s['hot_count']} 🔥 hot leads, {s['warm_count']} 🌤️ warm leads, {s['cold_count']} ❄️ noise filtered out")
    lines.append("")

    if business_name:
        lines.append(f"For: {business_name} | Trade: {trade.title()} | Area: {area_label}")
        lines.append("")

    # Hot leads — full detail
    if qualified["hot"]:
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🔥 HOT — Call These Today ({s['hot_count']})")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        for i, lead in enumerate(qualified["hot"][:10], 1):  # Cap at 10 hot leads
            lines.append(f"")
            lines.append(f"#{i} — Score {lead['score']}/10")
            lines.append(f"📋 {lead['title']}")
            if lead.get("snippet"):
                lines.append(f"💬 {lead['snippet'][:200]}")
            if lead.get("why_hot"):
                lines.append(f"✅ {lead['why_hot']}")
            # Show which city area the lead came from
            if lead.get("city_searched"):
                lines.append(f"📍 {lead['city_searched']}")
            lines.append(f"🔗 {lead['url']}")
    else:
        lines.append("No hot leads this morning. Broadened search — checking warm leads for opportunities.")

    lines.append("")

    # Warm leads — show top 7 with city labels
    if qualified["warm"]:
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🌤️ WARM — Review This Week ({s['warm_count']})")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        show_count = min(7, len(qualified["warm"]))
        for i, lead in enumerate(qualified["warm"][:show_count], 1):
            note = lead.get("why_warm", "")
            city_tag = f" [{lead['city_searched']}]" if lead.get("city_searched") else ""
            lines.append(f"  {i}. {lead['title'][:65]}{city_tag}")
            if note:
                lines.append(f"     → {note}")
            lines.append(f"     🔗 {lead['url'][:80]}")
        if s["warm_count"] > show_count:
            lines.append(f"  ... {s['warm_count'] - show_count} more warm leads available")

    # Market notes
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("📊 MARKET NOTES")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")

    # Count by category
    categories = {}
    for lead in qualified["hot"] + qualified["warm"]:
        cat = lead.get("search_category", "other")
        categories[cat] = categories.get(cat, 0) + 1

    cat_names = {
        "direct_jobs": "Job postings",
        "rfp_bids": "RFPs & Bids",
        "project_leads": "Project leads",
        "subcontractor": "Sub wanted",
        "government": "Government contracts",
        "remodeling": "Remodeling projects",
        "general_contracting": "General contracting",
        "broaden_nearby_cities": "Nearby areas",
        "broaden_county_wide": "County-wide",
        "broaden_trade_variants": "Trade variants",
        "broaden_broader_projects": "Broader projects",
    }

    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        name = cat_names.get(cat, cat.replace("_", " ").title())
        lines.append(f"  • {name}: {count} found")

    # Count by city
    city_counts = {}
    for lead in qualified["hot"] + qualified["warm"]:
        c = lead.get("city_searched", "unknown")
        city_counts[c] = city_counts.get(c, 0) + 1
    if len(city_counts) > 1:
        lines.append("")
        lines.append("  By area:")
        for c, count in sorted(city_counts.items(), key=lambda x: -x[1]):
            lines.append(f"    • {c}: {count} leads")

    if s["hot_count"] > 0:
        lines.append("")
        lines.append("💡 Your worker found action-worthy leads. Don't let them sit.")
    elif s["warm_count"] > 0:
        lines.append("")
        lines.append("💡 No slam-dunks today, but warm leads are worth checking.")
    else:
        lines.append("")
        lines.append("💡 Slow morning. Your worker widened the net — tomorrow may pick up.")

    lines.append("")
    lines.append("— Your Digital Worker @ North Web Pro")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Construction Morning Briefing")
    parser.add_argument("--trade", default="roofing")
    parser.add_argument("--city", default="", help="Single city (overrides --cities)")
    parser.add_argument("--cities", nargs="+", default=[],
                        help="Multiple cities to search")
    parser.add_argument("--counties", nargs="+", default=[],
                        help="County names matching cities")
    parser.add_argument("--state", default="California")
    parser.add_argument("--region", default="SoCal")
    parser.add_argument("--business-name", default="")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--min-size", type=int, default=5000)
    parser.add_argument("--profile", default="")
    parser.add_argument("--output", default="",
                        help="Save briefing to file (default: stdout)")

    args = parser.parse_args()

    # Single city mode
    if args.city and not args.cities:
        args.cities = [args.city]

    # Load profile if specified
    if args.profile:
        prof = load_profile(args.profile)
        if prof:
            args.trade = prof.get("trade", args.trade)
            if not args.cities and prof.get("cities"):
                # Profile may have comma-separated cities
                raw = prof["cities"]
                if isinstance(raw, str):
                    args.cities = [c.strip() for c in raw.split(",")]
                elif isinstance(raw, list):
                    args.cities = raw
            elif not args.cities and prof.get("city"):
                args.cities = [prof["city"]]
            args.state = prof.get("state", args.state)
            args.region = prof.get("region", args.region)
            args.business_name = prof.get("business_name", args.business_name)
            min_size = prof.get("min_project_size", args.min_size)
            args.min_size = int(min_size) if isinstance(min_size, str) else int(min_size)
            if prof.get("counties"):
                raw = prof["counties"]
                if isinstance(raw, str):
                    args.counties = [c.strip() for c in raw.split(",")]
                elif isinstance(raw, list):
                    args.counties = raw

    # Fallback defaults — Steven's territory
    if not args.cities:
        args.cities = ["Riverside, CA", "Orange County, CA", "San Diego, CA"]
    if not args.counties:
        args.counties = ["Riverside", "Orange", "San Diego"]

    # Make primary city for qualifier the first city
    primary_city = args.cities[0]

    print(f"=== Construction Morning Briefing ===", file=sys.stderr)
    print(f"  Trade:   {args.trade}", file=sys.stderr)
    print(f"  Cities:  {', '.join(args.cities)}", file=sys.stderr)
    print(f"  State:   {args.state}", file=sys.stderr)
    print(f"  Region:  {args.region}", file=sys.stderr)
    print(f"  Limit:   {args.limit} per query", file=sys.stderr)

    # Step 1: Scout across all cities (with auto-broadening)
    print(f"\n  Step 1: Running scout across {len(args.cities)} cities...", file=sys.stderr)
    scout_output = "/tmp/briefing_scout.json"
    leads = run_scout(
        args.cities, args.trade, args.state, args.region,
        args.counties, args.limit, scout_output
    )
    print(f"  Found {len(leads)} leads total", file=sys.stderr)

    if not leads:
        briefing = (
            f"🏗️ MORNING BRIEFING\n\n"
            f"No new leads found today for {args.trade} across {', '.join(args.cities)}.\n"
            f"Your worker will keep searching tomorrow — the market picks up.\n\n"
            f"— Your Digital Worker @ North Web Pro"
        )
        if args.output:
            with open(args.output, "w") as f:
                f.write(briefing)
            print(f"  Wrote briefing to {args.output}", file=sys.stderr)
        else:
            print(briefing)
        return

    # Step 2: Qualify
    print(f"  Step 2: Running qualifier...", file=sys.stderr)
    qualified = run_qualifier(
        scout_output, primary_city, args.region, args.state, args.trade, args.min_size
    )

    # Step 3: Format briefing
    print(f"  Step 3: Formatting briefing...", file=sys.stderr)
    briefing = format_telegram_briefing(
        qualified, args.trade, args.cities, args.business_name
    )

    if args.output:
        with open(args.output, "w") as f:
            f.write(briefing)
        print(f"  Wrote briefing to {args.output}", file=sys.stderr)
    else:
        print(briefing)


if __name__ == "__main__":
    main()
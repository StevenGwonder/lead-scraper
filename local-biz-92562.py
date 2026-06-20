#!/usr/bin/env python3
"""
92562 Local Business Scout v9 — Lead qualification pipeline for North Web Pro.

Scrapes SearXNG for local businesses (trades + admin/ops), audits websites
for automation gaps, scores buying readiness (0-100), delivers HTML report.
Ponytail v9: dead code removed, ~1399 lines, zero non-stdlib dependencies.
"""
import argparse
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

SEARXNG = "http://localhost:8888/search"
CACHE_FILE = Path(os.path.expanduser("~/.hermes/scripts/local-biz-cache.json"))
REPORT_DIR = Path(os.path.expanduser("~/.hermes/scripts/reports"))

# ── NOT REAL BUSINESSES ──
AGGREGATOR_DOMAINS = {
    "yellowpages.com", "yelp.com", "google.com", "angi.com",
    "homeadvisor.com", "thumbtack.com", "bbb.org", "manta.com",
    "superpages.com", "mapquest.com", "foursquare.com",
    "facebook.com", "linkedin.com", "expertise.com",
    "threebestrated.com", "birdeye.com", "carwise.com",
    "surecritic.com", "carfax.com", "redfin.com", "zillow.com",
    "chamberofcommerce.com", "nextdoor.com", "todayshomeowner.com",
    "podium.com", "porch.com", "craftjack.com", "networx.com",
    "improvenet.com", "findglocal.com", "localsearchhub.com",
    "bizjournals.com", "patch.com", "tripadvisor.com",
    "wikipedia.org", "reddit.com", "youtube.com", "instagram.com",
    "tiktok.com", "twitter.com", "x.com", "pinterest.com",
    "yelpcdn.com", "apple.com", "zendesk.com", "local.yahoo.com",
    "yahoo.com", "bing.com", "duckduckgo.com", "brave.com",
    "city-data.com", "citygrid.com", "cylex.us.com",
    "podcast.com", "podcasts.apple.com", "homeguide.com",
    "localhvac.com", "roofer.com", "plumber.com",
    "groupon.com", "amazon.com", "ebay.com",
    "buildzoom.com", "houzz.com",
    "threads.com", "threads.net",
    # Fix 1: Q&A sites, national directories, manufacturer pages
    "justanswer.com", "avvo.com", "gaf.com", "aaa.com",
    "quora.com", "medium.com", "blogspot.com",
}

AGGREGATOR_TITLE_PATTERNS = [
    r'^\d+\s+(best|top)\s+', r'^top\s+\d+\s+', r'^the\s+\d+\s+best\s+',
    r'best\s+\d+', r'^\d+\s+(most\s+)?trusted\s+', r'^\d+\s+(affordable|cheap|reliable)\s+',
    r'^find\s+', r'^compare\s+', r'^how\s+to\s+', r'^where\s+to\s+',
    r'^list\s+of\s+', r'^guide\s+to\s+', r'^everything\s+you\s+need\s+',
    r'^what\s+(is|are)\s+', r'^\d+\s+(signs|tips|ways|things|reasons)\s+',
    r'^\d+\s+(stars?|review)',
    r'^(best|top|cheap|affordable|reliable|local)\s+(plumber|hvac|electrician|roofer|painter|landscaper|mechanic|handyman|carpet|tree\s+service|contractor|company|service|repair|business)',
]

# ── TRADE QUERIES — SPLIT INTO ROTATION GROUPS ──
# Fix 5: expanded to cover neighboring cities (Wildomar, Menifee, Lake Elsinore)
# Phase 2: expanded to 6 groups (A-F) — trades + admin/operations businesses
TRADE_GROUPS = [
    # Group A: Plumbing + HVAC
    {"Plumbing": ["plumber Murrieta CA", "plumber Temecula CA", "plumber Wildomar CA"],
     "HVAC": ["HVAC repair Murrieta CA", "AC repair Temecula CA"]},
    # Group B: Electrical + Landscaping + Roofing
    {"Electrical": ["electrician Murrieta CA", "electrician Temecula CA", "electrician Menifee CA"],
     "Landscaping": ["landscaping Murrieta CA", "landscaping Temecula CA"],
     "Roofing": ["roofing contractor Murrieta CA", "roofing Temecula CA"]},
    # Group C: Auto + Tree + Painting
    {"Auto Repair": ["auto repair Murrieta CA", "mechanic Temecula CA", "auto repair Menifee CA"],
     "Tree Service": ["tree service Murrieta CA", "tree removal Temecula CA"],
     "Painting": ["painting contractor Murrieta CA", "painter Temecula CA"]},
    # Group D: Carpet + Handyman + buying signals
    {"Carpet Cleaning": ["carpet cleaning Murrieta CA", "carpet cleaning Temecula CA"],
     "Handyman": ["handyman Murrieta CA", "handyman Temecula CA", "handyman Menifee CA"],
     "_signals": True},
    # Group E (NEW): Accounting + Law + Insurance — admin/operations businesses
    {"Accounting": ["accounting firm Murrieta CA", "CPA Temecula CA", "bookkeeping Murrieta CA"],
     "Law Office": ["law office Murrieta CA", "lawyer Temecula CA", "attorney Murrieta CA"],
     "Insurance": ["insurance agency Murrieta CA", "insurance agent Temecula CA"]},
    # Group F (NEW): Property Mgmt + Recruiting + Consulting — admin/operations businesses
    {"Property Management": ["property management Murrieta CA", "property management Temecula CA"],
     "Recruiting": ["recruiting agency Murrieta CA", "staffing agency Temecula CA"],
     "Consulting": ["business consulting Murrieta CA", "consulting firm Temecula CA"],
     "_signals": True},
]

# Admin/operations trades get higher automation demand bonus in scoring
ADMIN_TRADES = {"Accounting", "Law Office", "Insurance", "Property Management",
                "Recruiting", "Consulting"}

# Platform detection — ponytail: dict loop replaces 6 inline ifs
PLATFORMS = {
    "wp-content": "WordPress", "wordpress": "WordPress",
    "wix": "Wix", "weebly": "Weebly",
    "squarespace": "Squarespace", "godaddy": "GoDaddy",
}

# Phase 2: CRM/tool detection markers (searched in lowercased HTML)
CRM_MARKERS = {
    "hubspot": "HubSpot", "salesforce": "Salesforce", "zoho": "Zoho",
    "monday.com": "Monday", "pipedrive": "Pipedrive", "insightly": "Insightly",
    "freshsales": "Freshsales", "close.com": "Close",
}
ANALYTICS_MARKERS = {
    "google-analytics": "Google Analytics", "gtag": "Google Tag",
    "googletagmanager": "Google Tag Manager", "google_tag_manager": "Google Tag Manager",
    "fbq": "Facebook Pixel", "facebook pixel": "Facebook Pixel",
    "_fbq": "Facebook Pixel", "hotjar": "Hotjar",
}
MARKETING_MARKERS = {
    "mailchimp": "Mailchimp", "constantcontact": "Constant Contact",
    "constant contact": "Constant Contact", "sendgrid": "SendGrid",
    "convertkit": "ConvertKit", "klaviyo": "Klaviyo",
    "campaignmonitor": "Campaign Monitor", "activecampaign": "ActiveCampaign",
}
BOOKING_MARKERS = {
    "calendly": "Calendly", "acuityscheduling": "Acuity",
    "acuity": "Acuity", "setmore": "Setmore", "vcita": "vcita",
    "fresha": "Fresha", "squarespace.com/scheduling": "Squarespace Scheduling",
    "squarespace scheduling": "Squarespace Scheduling", "calendly.com": "Calendly",
    "book.app": "Booking App", "resurva": "Resurva", "square.appointments": "Square Appointments",
    "bookeo": "Bookeo", "opencare": "OpenCare", "dentaloffice": "DentalOffice",
    "mindbody": "Mindbody", "mbo": "Mindbody",
}
# Outdated email providers — digital laggard signal
OUTDATED_EMAIL_DOMAINS = ("hotmail.com", "aol.com", "yahoo.com", "hotmail", "aol", "yahoo")
# Review complaint keywords — negative review buying signal
REVIEW_COMPLAINT_KEYWORDS = [
    "slow", "no response", "didn't call back", "didn't respond",
    "unresponsive", "never showed up", "no-show", "never called",
    "didn't show", "poor communication", "hard to reach",
    "voicemail", "never returned", "didn't return my call",
]

# Clean name suffixes — ponytail: extracted constant replaces 20 inline ifs
NAME_SUFFIXES = [
    " - Yelp", " | Yelp", " — Yelp", " - Updated 2025", " - Updated 2026",
    " - YellowPages", " | YellowPages", " - HomeAdvisor", " - Angi",
    " - Thumbtack", " | BBB", " - MapQuest", " - Facebook",
    " - Updated June 2026", " - Updated May 2026",
    " - Updated April 2026", " - Updated March 2026",
    " - Threads", " | Threads", " - Reddit", " | Reddit",
]

# ── SCORING MODEL ──────────────────────────────────────────────────────
# Edit the ICP philosophy here — see PRD.md §2. (T1) Weights extracted from
# qualify_lead verbatim; later tasks rebalance the values, not their location.
SCORING = {
    "automation": {
        "max": 40,
        "status_down": 25,
        "status_blocked": 10,
        "gap_weights": {
            "no CRM": 15,
            "no marketing tools": 10,
            "no analytics": 5,
            "no booking/chat system": 8,
            "no booking system": 8,
        },
        "gap_default": 5,
    },
    "growth": {
        "max": 30,
        "hiring_signal": 15,
        "trade_admin": 15,      # ADMIN_TRADES
        "trade_high": 15,       # HVAC, Plumbing
        "trade_moderate": 10,   # Electrical, Roofing, Auto Repair
        "trade_other": 5,
        "review_negative": 10,
    },
    "digital": {
        "max": 15,
        "down": 15,
        "blocked": 8,
        "ws_low": 12,           # website_score <= 1
        "ws_2": 8,
        "ws_3": 4,
        "outdated_email": 5,
        "fax": 5,
    },
    "contact": {
        "max": 15,
        "phone": 10,
        "email": 5,
        "own_site": 3,
        "snippet": 2,
    },
    "trades_high": ("HVAC", "Plumbing"),
    "trades_moderate": ("Electrical", "Roofing", "Auto Repair"),
    "tiers": {"hot": 70, "warm": 40},
}


def log(msg):
    print(f"[local-biz] {msg}", file=sys.stderr)


def searx_search(query, limit=15, retries=1, delay=5):
    """Query SearXNG with generous delays."""
    params = urllib.parse.urlencode({"q": query, "format": "json", "limit": limit})
    url = f"{SEARXNG}?{params}"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
                results = data.get("results", [])
                suspended = data.get("unresponsive_engines", [])
                if suspended:
                    log(f"  {len(suspended)} engines suspended")
                return results
        except urllib.error.URLError as e:
            log(f"  SearXNG error: {e}")
            if attempt < retries:
                time.sleep(delay * 2)
        except Exception as e:
            log(f"  SearXNG error: {e}")
            if attempt < retries:
                time.sleep(delay)
    return []


def clean_name(title):
    """Clean business name from search result title.
    Fix 3: aggressive cleanup — strip SEO keywords, take actual business name.
    Strategy: pipe-separated → take part with most 'business-like' words.
    Dash-separated → take part after the last dash (usually the brand).
    Strip cities, SEO prefixes, job postings."""
    name = title
    # Strip aggregator suffixes first
    for s in NAME_SUFFIXES:
        if s in name:
            name = name.split(s)[0]
            break
    # Split on | and pick the best segment
    if "|" in name:
        parts = [p.strip() for p in name.split("|") if p.strip()]
        if len(parts) >= 2:
            # Score each part: business-like words = +1, city/SEO keywords = -1
            biz_words = {"inc", "co", "corp", "llc", "services", "service", "company",
                        "plumbing", "electric", "automotive", "repair", "heating",
                        "air", "conditioning", "landscaping", "roofing", "painting",
                        "handyman", "tree", "auto", "mechanic", "carpet"}
            best_part = parts[-1]
            best_score = -99
            for part in parts:
                words = set(part.lower().split())
                score = sum(1 if w in biz_words else 0 for w in words)
                # Penalize parts that are just cities or SEO keywords
                if any(c in part.lower() for c in ["murrieta", "temecula", "wildomar"]):
                    score -= 1
                if any(kw in part.lower() for kw in ["24/7", "emergency", "top ", "best ", "expert"]):
                    score -= 1
                if len(part) < 3:
                    score -= 5
                if score > best_score:
                    best_score = score
                    best_part = part
            name = best_part
    # Split on – - (en/em dash) and take the part after the last separator
    # e.g. "Your Local Plumber in Murrieta, CA - Guardian Plumbers" → "Guardian Plumbers"
    for sep in [" – ", " - "]:
        if sep in name:
            parts = [p.strip() for p in name.split(sep) if p.strip()]
            if len(parts) >= 2:
                # Take the last part (usually the brand name)
                last = parts[-1]
                if len(last) >= 3 and not last.lower().startswith(("ca", "updated", "photos")):
                    name = last
                    break
    # Strip city names
    name = re.sub(r'\s*[-–—,]\s*(Murrieta|Temecula|Wildomar|Menifee|Lake Elsinore),?\s*CA?\s*', ' ', name, flags=re.I)
    name = re.sub(r'\s+in\s+(Murrieta|Temecula|Wildomar|Menifee|Lake Elsinore).*$', '', name, flags=re.I)
    name = re.sub(r'\s+(Murrieta|Temecula|Wildomar),?\s*CA\s*\d*', '', name, flags=re.I)
    # Strip leading numbers
    name = re.sub(r'^\d+\.?\s+', '', name)
    # Strip trailing "in City, CA"
    name = re.sub(r'\s+in\s+(Murrieta|Temecula|Wildomar),?\s*CA?\s*$', '', name, flags=re.I)
    # Strip SEO prefixes
    name = re.sub(r'^(Best|Top|Expert|Professional|Affordable|Premier|Trusted|Local|Cheap|Rated)\s+', '', name, flags=re.I)
    name = re.sub(r'^(24/7|24\s+Hour)\s+', '', name, flags=re.I)
    # Strip "Home - " prefix (from homepage titles)
    name = re.sub(r'^Home\s*[-–]\s*', '', name, flags=re.I)
    # Strip job postings (not a business)
    if re.search(r'\$\d+.*hr|hiring|jobs?\s+in\s+|ziprecruiter|repairpal|loc8nearme', name, re.I):
        name = ""
    # Collapse whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    return name.strip(" -|:\"'")


def is_aggregator(title, url):
    """Check if result is aggregator/list, not a real business."""
    domain = re.sub(r'https?://(www\.)?', '', url.lower()).split('/')[0]
    if any(agg in domain for agg in AGGREGATOR_DOMAINS):
        return True
    for pattern in AGGREGATOR_TITLE_PATTERNS:
        if re.search(pattern, title, re.I):
            return True
    if any(pat in url.lower() for pat in ["/search?", "/find_", "/browse", "/cflt="]):
        return True
    return False


def extract_phones(text):
    """Extract US phone numbers. Fix 6: broader regex for more formats."""
    # Match: (951) 225-1131, 951-225-1131, 951.225.1131, 951 225 1131, 9512251131
    phones = re.findall(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', text)
    seen, result = set(), []
    for p in phones:
        digits = re.sub(r'\D', '', p)
        # Must be 10 digits (US) or 11 starting with 1
        if len(digits) == 11 and digits.startswith('1'):
            digits = digits[1:]
        if digits not in seen and len(digits) == 10:
            # Skip area codes that are clearly not real (000, 800, 888 toll-free often junk)
            if not digits.startswith(('000', '999')):
                seen.add(digits)
                # Normalize format: (XXX) XXX-XXXX
                formatted = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
                result.append(formatted)
    return result[:3]


# SSL context that tolerates expired/bad certs — many small biz sites have these
_NOVERIFY_CTX = ssl.create_default_context()
_NOVERIFY_CTX.check_hostname = False
_NOVERIFY_CTX.verify_mode = ssl.CERT_NONE

# Multiple user agents — rotate to avoid bot-blocking
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
]


def _detect_markers(html_lower, marker_dict):
    """Helper: detect which named tools are present in lowercased HTML. Returns list of names found."""
    found = []
    seen = set()
    for marker, name in marker_dict.items():
        if marker in html_lower and name not in seen:
            seen.add(name)
            found.append(name)
    return found


def _extract_emails(html):
    """Phase 3: Extract email addresses from HTML. Looks at mailto: links first, then raw text."""
    emails = []
    seen = set()
    # mailto: links — most reliable
    for m in re.findall(r'href=["\']mailto:([^"\'\s>]+)', html, re.I):
        addr = m.split("?")[0].strip().lower()
        if "@" in addr and len(addr) < 80 and addr not in seen:
            # skip image/sprite placeholder emails
            if not addr.startswith(("noreply", "donotreply", "no-reply", "example", "sentry")):
                seen.add(addr)
                emails.append(addr)
    # Raw email regex as fallback (limit to first 8k of page to avoid noise)
    if len(emails) < 2:
        for m in re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', html[:8000]):
            addr = m.lower()
            if (addr not in seen and "@" in addr and len(addr) < 80
                    and not addr.startswith(("noreply", "donotreply", "no-reply", "example", "sentry"))
                    and not addr.endswith((".png", ".jpg", ".gif", ".webp"))):
                seen.add(addr)
                emails.append(addr)
    return emails[:5]


def _base_result(status, confidence, gaps):
    """Base result dict for check_website — avoids repeating 13 keys 4 times."""
    return {"status": status, "confidence": confidence,
            "website_score": -1, "automation_gaps": gaps,
            "platform": "Unknown" if status != "down" else "N/A",
            "words": 0, "phones": [],
            "has_crm": [], "has_analytics": [], "has_marketing_tools": [],
            "has_booking_system": [], "emails": [],
            "has_outdated_email": False, "has_fax": False}


def check_website(domain):
    """Phase 1+2+3: Robust website check with SSL fallback, multi-UA, honest status."""
    for scheme in ["https", "http"]:
        for ua in USER_AGENTS:
            try:
                req = urllib.request.Request(
                    f"{scheme}://{domain}",
                    headers={"User-Agent": ua, "Accept": "text/html,application/xhtml+xml"}
                )
                with urllib.request.urlopen(req, timeout=12, context=_NOVERIFY_CTX) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")[:12000]
                    html_lower = html.lower()

                    if "cf-browser-verification" in html_lower or "checking your browser" in html_lower or "cf-challenge" in html_lower:
                        return _base_result("blocked", "low", ["bot-protected — can't verify"])

                    if len(html_lower) < 200:
                        continue

                    gaps = []
                    platform = "Custom"
                    for marker, name in PLATFORMS.items():
                        if marker in html_lower:
                            platform = name
                    if "elementor" in html_lower and "wordpress" in platform.lower():
                        platform = "WordPress/Elementor"

                    crm_tools = _detect_markers(html_lower, CRM_MARKERS)
                    analytics_tools = _detect_markers(html_lower, ANALYTICS_MARKERS)
                    marketing_tools = _detect_markers(html_lower, MARKETING_MARKERS)
                    booking_tools = _detect_markers(html_lower, BOOKING_MARKERS)
                    emails = _extract_emails(html)
                    has_outdated_email = any(any(od in addr for od in OUTDATED_EMAIL_DOMAINS) for addr in emails)
                    has_fax = bool(re.search(r'fax[\s:.]*[\(\d]{1,2}[\d\s\-\.\/\(\)]{10,}', html_lower))

                    has_viewport = "viewport" in html_lower
                    has_tel = "tel:" in html_lower
                    has_contact = "contact" in html_lower
                    has_booking_system = bool(booking_tools)
                    has_chat = any(x in html_lower for x in ["chat", "intercom", "tawk", "drift", "olark"])

                    if not has_booking_system and not has_chat:
                        gaps.append("no booking/chat system")
                    elif not has_booking_system:
                        gaps.append("no booking system")
                    if not has_tel: gaps.append("no click-to-call")
                    if not has_contact: gaps.append("no contact page")
                    if not has_viewport: gaps.append("not mobile-responsive")
                    if not crm_tools: gaps.append("no CRM")
                    if not marketing_tools: gaps.append("no marketing tools")
                    if not analytics_tools: gaps.append("no analytics")

                    text = re.sub(r'<[^>]+>', ' ', html_lower)
                    words = len(text.split())
                    if words < 200:
                        gaps.append(f"thin content ({words}w)")

                    website_score = sum([has_viewport, has_tel, has_contact, words > 200, has_booking_system or has_chat])

                    page_phones = extract_phones(html[:5000])
                    tel_matches = re.findall(r'href=["\']tel:([+\d\s()\-\.]+)', html, re.I)
                    for tm in tel_matches:
                        digits = re.sub(r'\D', '', tm)
                        if len(digits) == 10:
                            formatted = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
                            if formatted not in page_phones:
                                page_phones.append(formatted)

                    return {"status": "up", "confidence": "high",
                            "website_score": website_score, "automation_gaps": gaps,
                            "platform": platform, "words": words, "phones": page_phones,
                            "has_crm": crm_tools, "has_analytics": analytics_tools,
                            "has_marketing_tools": marketing_tools,
                            "has_booking_system": booking_tools, "emails": emails,
                            "has_outdated_email": has_outdated_email, "has_fax": has_fax}

            except urllib.error.HTTPError as e:
                if e.code in (403, 401, 429):
                    return _base_result("blocked", "low", ["bot-protected — can't verify"])
                if e.code == 404:
                    continue
                return _base_result("blocked", "low", [f"HTTP {e.code} — can't verify"])
            except (urllib.error.URLError, Exception):
                continue
    return _base_result("down", "high", ["site down"])


def search_hiring_signals(biz_name, cache_key, cache):
    """Phase 2: Search SearXNG for hiring signals — '{name} hiring' and '{name} jobs'.
    Returns True if hiring signals found. Stores results in cache['businesses'][cache_key]['hiring_signals'].
    Respects 6-second rate limiting between queries."""
    if not biz_name or len(biz_name) < 3:
        return False
    # Check cache first — don't re-search within 3 days
    cached = cache.get("businesses", {}).get(cache_key, {})
    if cached.get("hiring_checked"):
        cached_hiring = cached.get("hiring_signals", [])
        return bool(cached_hiring)
    
    hiring_found = False
    hiring_results = []
    for q in [f"{biz_name} hiring", f"{biz_name} jobs"]:
        results = searx_search(q, limit=6, delay=6)
        for r in results:
            title = (r.get("title", "") or "").lower()
            snippet = (r.get("content", "") or "").lower()
            combined = title + " " + snippet
            # Look for actual hiring indicators (job listings, "we're hiring", "now hiring", career pages)
            if any(kw in combined for kw in [
                "hiring", "jobs", "careers", "employment", "job opening",
                "now hiring", "we're hiring", "job posting", "apply now",
                "join our team", "career opportunity",
            ]):
                hiring_found = True
                hiring_results.append({
                    "title": r.get("title", "")[:70],
                    "snippet": (r.get("content", "") or "")[:120],
                    "url": r.get("url", ""),
                })
        if hiring_found:
            break  # Don't do second query if first found results
        time.sleep(6)
    
    # Store in cache
    if cache_key not in cache.get("businesses", {}):
        cache["businesses"][cache_key] = {}
    cache["businesses"][cache_key]["hiring_signals"] = hiring_results[:5]
    cache["businesses"][cache_key]["hiring_checked"] = True
    return hiring_found


def search_review_signals(biz_name, cache_key, cache):
    """Phase 2: Search SearXNG for negative review signals — '{name} reviews'.
    If results mention complaint keywords (slow, no response, etc.) = buying signal.
    Stores results in cache['businesses'][cache_key]['review_signals'].
    Respects 6-second rate limiting."""
    if not biz_name or len(biz_name) < 3:
        return False
    # Check cache first — don't re-search within 3 days
    cached = cache.get("businesses", {}).get(cache_key, {})
    if cached.get("review_checked"):
        return bool(cached.get("review_signals", []))
    
    review_results = []
    negative_found = False
    results = searx_search(f"{biz_name} reviews", limit=8, delay=6)
    for r in results:
        title = (r.get("title", "") or "").lower()
        snippet = (r.get("content", "") or "").lower()
        combined = title + " " + snippet
        # Check for complaint keywords
        complaints = [kw for kw in REVIEW_COMPLAINT_KEYWORDS if kw in combined]
        if complaints:
            negative_found = True
        # Only store results that look like reviews (have "review" or rating in them)
        if any(kw in combined for kw in ["review", "rating", "star", "yelp", "google"]):
            review_results.append({
                "title": r.get("title", "")[:70],
                "snippet": (r.get("content", "") or "")[:120],
                "url": r.get("url", ""),
                "complaints": complaints,
            })
    
    # Store in cache
    if cache_key not in cache.get("businesses", {}):
        cache["businesses"][cache_key] = {}
    cache["businesses"][cache_key]["review_signals"] = review_results[:5]
    cache["businesses"][cache_key]["review_negative"] = negative_found
    cache["businesses"][cache_key]["review_checked"] = True
    return negative_found


def qualify_lead(biz, sq):
    """Phase 2+3+4: Lead Qualification Score (0-100).
    Measures BUYING READINESS for North Web Pro's automation services.
    
    AUTOMATION READINESS (0-40): Does the business have gaps we can fill?
      - Gap-based scoring refined: no booking system (actual), no CRM (+15),
        no marketing tools (+10), no analytics (+5)
    GROWTH SIGNALS (0-30): Is the business growing? Hiring signals +15, admin/ops +15
    DIGITAL GAP (0-15): Website score + outdated email +5, fax +5
    CONTACTABILITY (0-15): Phone +10, email +5, has own site +3, snippet +2
    """
    if not sq:
        return {"score": 0, "tier": "Cold", "breakdown": {}, "reasons": ["not yet analyzed"]}

    breakdown = {}
    reasons = []

    A = SCORING["automation"]
    G = SCORING["growth"]
    D = SCORING["digital"]
    C = SCORING["contact"]

    gaps = sq.get("automation_gaps", sq.get("issues", []))
    status = sq.get("status", "unknown")
    # T13 — confidence gate: only score what we actually observed. A site we
    # couldn't read (down / blocked / unknown / JS-shell) earns ZERO from the
    # site-derived pillars; we never reward our own fetch failure as opportunity.
    # External signals (hiring, reviews, JSON-LD contacts) are exempt below.
    verified = status == "up" and sq.get("confidence") == "high"

    # ── AUTOMATION READINESS (site-derived → gated) ──
    ar = 0
    if verified:
        for g in gaps:
            weight = A["gap_weights"].get(g, A["gap_default"])
            ar += weight
            reasons.append(f"{g} (+{weight})")
        ar = min(ar, A["max"])

    breakdown["automation"] = min(ar, A["max"])

    # ── GROWTH SIGNALS ──
    growth = 0
    trade = biz.get("trade", "")

    # Hiring signals from SearXNG search (stored in biz)
    hiring_signals = biz.get("hiring_signals", [])
    if hiring_signals:
        growth += G["hiring_signal"]
        reasons.append(f"hiring signal detected (+{G['hiring_signal']})")
    elif verified:
        # Trade prior is a guess about a live business — only apply it when we
        # could confirm the site is actually up (T13). No verification → no
        # manufactured growth points from a business we couldn't read.
        if trade in ADMIN_TRADES:
            growth += G["trade_admin"]
            reasons.append(f"admin/ops business — high automation demand (+{G['trade_admin']})")
        elif trade in SCORING["trades_high"]:
            growth += G["trade_high"]
            reasons.append(f"high-demand trade for automation (+{G['trade_high']})")
        elif trade in SCORING["trades_moderate"]:
            growth += G["trade_moderate"]
            reasons.append(f"moderate-demand trade (+{G['trade_moderate']})")
        else:
            growth += G["trade_other"]

    # Negative review signals = business is losing customers (buying signal)
    if biz.get("review_negative"):
        growth += G["review_negative"]
        reasons.append(f"negative reviews — losing customers (+{G['review_negative']})")

    breakdown["growth"] = min(growth, G["max"])

    # ── DIGITAL GAP (site-derived → gated) ──
    dg = 0
    if verified:
        ws = sq.get("website_score", -1)
        if ws <= 1:
            dg += D["ws_low"]
            reasons.append(f"website score {ws}/5 — major digital gap (+{D['ws_low']})")
        elif ws == 2:
            dg += D["ws_2"]
            reasons.append(f"website score 2/5 — clear gaps (+{D['ws_2']})")
        elif ws == 3:
            dg += D["ws_3"]
            reasons.append(f"website score 3/5 — some gaps (+{D['ws_3']})")
        # Score 4-5 = no gap, +0

        # Digital laggard signals (read from the site we just verified)
        if sq.get("has_outdated_email"):
            dg += D["outdated_email"]
            reasons.append(f"outdated email provider (digital laggard) (+{D['outdated_email']})")
        if sq.get("has_fax"):
            dg += D["fax"]
            reasons.append(f"fax number — paper-based operation (+{D['fax']})")

    breakdown["digital"] = min(dg, D["max"])

    # ── CONTACTABILITY ──
    contact = 0
    phones = biz.get("phones", [])
    if phones:
        contact += C["phone"]
        reasons.append(f"phone found (+{C['phone']})")
    emails = biz.get("emails", []) or sq.get("emails", [])
    if emails:
        contact += C["email"]
        reasons.append(f"email found (+{C['email']})")
    if biz.get("has_own_site"):
        contact += C["own_site"]  # Has a website = can find contact page
    if biz.get("snippet") and len(biz.get("snippet", "")) > 50:
        contact += C["snippet"]  # Has a description = more info to work with
    breakdown["contact"] = min(contact, C["max"])

    if not verified:
        reasons.append("site unverified — scored on external signals only")

    # ── TOTAL ──
    total = breakdown["automation"] + breakdown["growth"] + breakdown["digital"] + breakdown["contact"]

    if total >= SCORING["tiers"]["hot"]:
        tier = "Hot"
    elif total >= SCORING["tiers"]["warm"]:
        tier = "Warm"
    else:
        tier = "Cold"

    return {"score": total, "tier": tier, "breakdown": breakdown, "reasons": reasons}


def load_cache():
    """Load the business cache from disk."""
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE) as f:
                cache = json.load(f)
            # Expire entries older than 7 days
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            cache["businesses"] = {
                k: v for k, v in cache.get("businesses", {}).items()
                if v.get("last_seen", "") > cutoff
            }
            return cache
        except (json.JSONDecodeError, KeyError):
            pass
    return {"businesses": {}, "signals": [], "fb_groups": [], "last_group": -1, "runs": 0, "last_run": None}


def save_cache(cache):
    """Save the business cache to disk."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def backup_cache(cache):
    """Write a timestamped backup of the cache. ponytail: keep it simple — one file per run."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    backup = REPORT_DIR / f"cache-backup-{ts}.json"
    with open(backup, "w") as f:
        json.dump(cache, f, indent=2)
    # Keep only the last 10 backups
    backups = sorted(REPORT_DIR.glob("cache-backup-*.json"))
    for old in backups[:-10]:
        old.unlink()
    return backup


# ── HTML REPORT ──────────────────────────────────────────────────────
# North Web Pro brand colors: #D97548 (trail orange), #60CFF4 (sky blue)
# Black bg, two-color accent system. Designed for mobile-first reading.

HTML_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0a0a0a;
    color: #d4d4d4;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
}
.hero {
    background: linear-gradient(135deg, #111 0%, #0a0a0a 50%, #1a1410 100%);
    padding: 36px 20px 28px;
    text-align: center;
    border-bottom: 2px solid #D97548;
}
.hero-logo {
    font-size: 1.5em; font-weight: 700; letter-spacing: 1px;
    color: #fff; margin-bottom: 6px;
}
.hero-logo span { color: #D97548; }
.hero-tagline {
    color: #60CFF4; font-size: 0.82em; font-style: italic;
    letter-spacing: 0.3px; margin-bottom: 14px;
}
.hero-meta {
    color: #666; font-size: 0.72em; line-height: 1.6;
}
.hero-meta strong { color: #999; }
.container { max-width: 860px; margin: 0 auto; padding: 20px 14px; }

/* Stats grid */
.stats {
    display: grid; grid-template-columns: repeat(5, 1fr);
    gap: 8px; margin-bottom: 28px;
}
.stat-box {
    background: #111; border: 1px solid #1a1a1a; border-radius: 8px;
    padding: 14px 8px; text-align: center;
}
.stat-num { font-size: 1.6em; font-weight: 700; color: #fff; }
.stat-num.down { color: #D97548; }
.stat-num.bad { color: #D97548; }
.stat-num.upsell { color: #60CFF4; }
.stat-label {
    font-size: 0.62em; color: #555; margin-top: 3px;
    text-transform: uppercase; letter-spacing: 0.5px;
}

/* Sections */
.section { margin-bottom: 30px; }
.section-title {
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 14px; padding-bottom: 6px;
    border-bottom: 1px solid #1a1a1a;
}
.section-title h2 { font-size: 0.95em; color: #fff; font-weight: 600; }
.badge {
    display: inline-block; padding: 2px 9px; border-radius: 10px;
    font-size: 0.7em; font-weight: 600;
}
.badge-signal { background: rgba(96,207,244,0.1); color: #60CFF4; border: 1px solid rgba(96,207,244,0.2); }
.badge-upsell { background: rgba(96,207,244,0.12); color: #60CFF4; border: 1px solid rgba(96,207,244,0.2); }
.badge-new {
    background: #D97548; color: #0a0a0a; font-size: 0.6em;
    padding: 1px 6px; border-radius: 4px; font-weight: 700; letter-spacing: 0.5px;
}
.badge-hot { background: #D97548; color: #0a0a0a; font-size: 0.65em; font-weight: 700; padding: 2px 8px; }
.badge-warm { background: rgba(217,117,72,0.2); color: #D97548; border: 1px solid rgba(217,117,72,0.3); font-size: 0.65em; padding: 2px 8px; }
.badge-cold { background: #1a1a1a; color: #555; font-size: 0.65em; padding: 2px 8px; }

/* Lead cards */
.lead-card {
    background: #111; border: 1px solid #1a1a1a; border-radius: 8px;
    padding: 14px 16px; margin-bottom: 8px;
    transition: border-color 0.15s, background 0.15s;
}
.lead-card:hover { border-color: #333; background: #141414; }
.lead-card.hot { border-left: 3px solid #D97548; }
.lead-card.warm { border-left: 3px solid rgba(217,117,72,0.4); }
.lead-card.cold { border-left: 3px solid #333; opacity: 0.7; }

.lead-top {
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 8px; margin-bottom: 6px;
}
.lead-name {
    font-weight: 600; color: #fff; font-size: 0.92em;
    text-decoration: none; line-height: 1.3;
}
.lead-name:hover { color: #60CFF4; }

.lead-info {
    display: flex; flex-wrap: wrap; gap: 6px 14px;
    font-size: 0.78em; margin-bottom: 4px;
}
.lead-domain a {
    color: #60CFF4; text-decoration: none; word-break: break-all;
}
.lead-domain a:hover { text-decoration: underline; }
.lead-phone a {
    color: #D97548; text-decoration: none; font-weight: 500;
}
.lead-phone a:hover { text-decoration: underline; }
.lead-trade { color: #555; }
.lead-platform { color: #666; }
.lead-status { font-size: 0.72em; font-weight: 500; }
.status-down { color: #D97548; }
.status-blocked { color: #888; }
.status-up { color: #3fb950; }
.lead-issues {
    color: #D97548; font-size: 0.75em; margin-top: 4px;
    opacity: 0.8;
}
.lead-reasons {
    display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px;
}
.reason-tag {
    background: rgba(217,117,72,0.08); color: #D97548; font-size: 0.7em;
    padding: 2px 8px; border-radius: 4px; border: 1px solid rgba(217,117,72,0.1);
}

/* Signal items */
.signal-card {
    background: #111; border: 1px solid #1a1a1a; border-left: 3px solid #60CFF4;
    border-radius: 6px; padding: 12px 14px; margin-bottom: 6px;
}
.signal-title { color: #fff; font-weight: 500; font-size: 0.85em; margin-bottom: 3px; }
.signal-title a { color: #fff; text-decoration: none; }
.signal-title a:hover { color: #60CFF4; text-decoration: underline; }
.signal-meta { color: #555; font-size: 0.75em; }
.signal-meta a { color: #60CFF4; text-decoration: none; }

/* Footer */
.footer {
    text-align: center; padding: 28px 20px;
    color: #444; font-size: 0.75em;
    border-top: 1px solid #1a1a1a; margin-top: 40px;
}
.footer a { color: #D97548; text-decoration: none; }
.footer .pitch {
    background: linear-gradient(135deg, rgba(217,117,72,0.06), rgba(96,207,244,0.04));
    border: 1px solid rgba(217,117,72,0.15);
    border-radius: 10px; padding: 18px 20px; margin: 0 auto 20px;
    max-width: 480px; color: #888; font-style: italic;
    line-height: 1.6; font-size: 0.85em;
}
.footer .pitch strong { color: #D97548; font-style: normal; }

/* Collapsible details */
details summary {
    cursor: pointer; color: #666; font-size: 0.78em;
    padding: 8px 0; user-select: none;
}
details summary:hover { color: #60CFF4; }

@media (max-width: 600px) {
    .hero-logo { font-size: 1.2em; }
    .stats { grid-template-columns: repeat(3, 1fr); }
    .stat-num { font-size: 1.3em; }
    .lead-info { font-size: 0.72em; }
}
"""


def lead_score_badge(tier, score):
    """Generate a colored badge for the lead qualification tier."""
    if tier == "Hot":
        return f'<span class="badge badge-hot">{score}/100 HOT</span>'
    elif tier == "Warm":
        return f'<span class="badge badge-warm">{score}/100 WARM</span>'
    else:
        return f'<span class="badge badge-cold">{score}/100</span>'


def generate_html_report(cache, zip_code="92562"):
    """Phase 4: HTML report organized by lead qualification score, not website score.
    Hot leads at top, sorted by score. Shows why each lead is qualified."""
    businesses = cache.get("businesses", {})
    signals = cache.get("signals", [])
    fb_groups = cache.get("fb_groups", [])
    now = datetime.now(timezone.utc)
    today = now.strftime("%a %b %d, %Y")
    total_runs = cache.get("runs", 0)
    last_run_iso = cache.get("last_run")
    new_cutoff = last_run_iso or (now - timedelta(hours=24)).isoformat()

    # Ensure every business has a lead_score
    for biz in businesses.values():
        if "lead_score" not in biz:
            biz["lead_score"] = qualify_lead(biz, biz.get("site_quality"))
        is_new = biz.get("first_seen", "") > new_cutoff
        biz["_new"] = is_new

    # Categorize by lead tier
    hot, warm, cold = [], [], []
    for biz in businesses.values():
        ls = biz.get("lead_score", {})
        score = ls.get("score", 0)
        tier = ls.get("tier", "Cold")
        if tier == "Hot":
            hot.append(biz)
        elif tier == "Warm":
            warm.append(biz)
        else:
            cold.append(biz)

    # Sort each tier by score (descending)
    hot.sort(key=lambda b: b.get("lead_score", {}).get("score", 0), reverse=True)
    warm.sort(key=lambda b: b.get("lead_score", {}).get("score", 0), reverse=True)

    cards = []

    # Stats row — qualification-focused
    stats = [
        (len(hot), "Hot Leads", "down"),
        (len(warm), "Warm", "bad"),
        (len(cold), "Cold", ""),
        (sum(1 for b in businesses.values() if b.get("_new")), "New", "down"),
        (sum(1 for b in businesses.values() if b.get("phones")), "Reachable", "upsell"),
    ]
    stats_html = '<div class="stats">'
    for num, label, cls in stats:
        num_cls = f' class="{cls}"' if cls else ''
        stats_html += f'<div class="stat-box"><div class="stat-num"{num_cls}>{num}</div><div class="stat-label">{label}</div></div>'
    stats_html += '</div>'
    cards.append(stats_html)

    def render_lead_card(biz):
        """Render a single lead card with qualification breakdown."""
        ls = biz.get("lead_score", {})
        score = ls.get("score", 0)
        tier = ls.get("tier", "Cold")
        reasons = ls.get("reasons", [])
        domain = biz.get("own_domains", ["?"])[0]
        phone = (biz.get("phones") or [""])[0]
        sq = biz.get("site_quality") or {}
        ws = sq.get("website_score", -1)
        status = sq.get("status", "unknown")
        platform = sq.get("platform", "")
        emails = biz.get("emails", []) or sq.get("emails", [])
        new_badge = ' <span class="badge badge-new">NEW</span>' if biz.get("_new") else ''

        # Phase 2: Build platform/tool summary for info line
        # e.g. "WordPress + HubSpot" or "Wix + no tools"
        tool_parts = []
        if platform and platform not in ("N/A", "Unknown", "Custom"):
            tool_parts.append(platform)
        crm = sq.get("has_crm", [])
        analytics = sq.get("has_analytics", [])
        marketing = sq.get("has_marketing_tools", [])
        booking = sq.get("has_booking_system", [])
        tools_detected = crm + analytics + marketing + booking
        if tools_detected:
            tool_parts.append(" + ".join(tools_detected[:3]))
        elif status == "up" and platform not in ("N/A", "Unknown"):
            tool_parts.append("no tools")
        tools_summary = " + ".join(tool_parts) if tool_parts else ""

        # Tier-based card class
        card_cls = "hot" if tier == "Hot" else "warm" if tier == "Warm" else "cold"

        html = f'<div class="lead-card {card_cls}">'
        html += '<div class="lead-top">'
        html += f'<a class="lead-name" href="https://{domain}" target="_blank">{biz["name"]}</a>{new_badge}'
        html += lead_score_badge(tier, score)
        html += '</div>'

        html += '<div class="lead-info">'
        html += f'<span class="lead-domain"><a href="https://{domain}" target="_blank">{domain}</a></span>'
        if phone:
            html += f'<span class="lead-phone"><a href="tel:{phone}">📞 {phone}</a></span>'
        if emails:
            html += f'<span class="lead-phone">✉️ {emails[0]}</span>'
        html += f'<span class="lead-trade">{biz.get("trade", "")}</span>'
        # Phase 2: show platform + tool info in one combined span
        if tools_summary:
            html += f'<span class="lead-platform">{tools_summary}</span>'
        elif platform and platform not in ("N/A", "Unknown"):
            html += f'<span class="lead-platform">{platform}</span>'
        # Status indicator
        if status == "down":
            html += '<span class="lead-status status-down">● DOWN</span>'
        elif status == "blocked":
            html += '<span class="lead-status status-blocked">● BLOCKED</span>'
        else:
            html += f'<span class="lead-status status-up">● UP {ws}/5</span>'
        html += '</div>'

        # Qualification reasons (why this lead is qualified)
        if reasons:
            html += '<div class="lead-reasons">'
            for r in reasons[:6]:
                html += f'<span class="reason-tag">{r}</span>'
            html += '</div>'

        html += '</div>'
        return html

    # HOT LEADS — show all, sorted by score
    if hot:
        section = '<div class="section">'
        section += '<div class="section-title"><h2>🔥 Hot Leads — Walk In Today</h2>'
        section += f'<span class="badge badge-hot">{len(hot)}</span></div>'
        for biz in hot:
            section += render_lead_card(biz)
        section += '</div>'
        cards.append(section)

    # WARM LEADS — show top 25
    if warm:
        shown = warm[:25]
        section = '<div class="section">'
        section += '<div class="section-title"><h2>📋 Warm Leads — Worth a Call</h2>'
        section += f'<span class="badge badge-warm">{len(warm)}</span></div>'
        if len(warm) > 25:
            section += f'<details open><summary>Showing top 25 of {len(warm)}</summary>'
        for biz in shown:
            section += render_lead_card(biz)
        if len(warm) > 25:
            section += '</details>'
        section += '</div>'
        cards.append(section)

    # COLD LEADS — collapsed, just show count
    if cold:
        section = '<div class="section">'
        section += '<div class="section-title"><h2>❄️ Cold Leads</h2>'
        section += f'<span class="badge badge-cold">{len(cold)}</span></div>'
        section += f'<details><summary>{len(cold)} leads — not enough buying signals yet</summary>'
        for biz in cold[:10]:
            section += render_lead_card(biz)
        if len(cold) > 10:
            section += f'<p style="color:#444;font-size:0.75em;text-align:center;padding:8px;">+ {len(cold)-10} more cold leads...</p>'
        section += '</details></div>'
        cards.append(section)

    # BUYING SIGNALS
    recent = [s for s in signals if s.get("date", "") >
              (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()]
    if recent:
        section = '<div class="section">'
        section += '<div class="section-title"><h2>📡 Buying Signals</h2>'
        section += f'<span class="badge badge-signal">{len(recent)}</span></div>'
        for s in recent[:8]:
            url = s.get("url", "")
            title = s.get("title", "")[:65]
            snippet = s.get("snippet", "")[:90]
            source = s.get("source", "")
            link = f'<a href="https://{url}" target="_blank">{title}</a>' if url else title
            section += '<div class="signal-card">'
            section += f'<div class="signal-title">{link}</div>'
            section += f'<div class="signal-meta">[{source}] {snippet}</div>'
            section += '</div>'
        section += '</div>'
        cards.append(section)

    # FB GROUPS
    if fb_groups:
        section = '<div class="section">'
        section += '<div class="section-title"><h2>👥 Local Groups</h2>'
        section += f'<span class="badge badge-upsell">{len(fb_groups)}</span></div>'
        for g in fb_groups[:5]:
            url = g.get("url", "")
            name = g.get("name", "")[:50]
            link = f'<a href="https://{url}" target="_blank">{name}</a>' if url else name
            section += f'<div class="signal-card"><div class="signal-title">{link}</div></div>'
        section += '</div>'
        cards.append(section)

    body = '\n'.join(cards)
    total_targets = len(hot) + len(warm)
    new_count = sum(1 for b in businesses.values() if b.get("_new"))

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lead Scout — {today}</title>
<style>{HTML_CSS}</style>
</head><body>
<div class="hero">
    <div class="hero-logo">North Web Pro <span>Lead Scout</span></div>
    <div class="hero-tagline">Your Guide Thru The Digital Wilderness</div>
    <div class="hero-meta">ZIP <strong>{zip_code}</strong> · Murrieta · Temecula · Wildomar<br>{today} · {total_runs} runs · {new_count} new · {len(hot)} hot leads</div>
</div>
<div class="container">
{body}
</div>
<div class="footer">
    <div class="pitch">
        <strong>Pitch:</strong> "You're growing, you're busy, and leads are slipping through.
        I install a digital employee that answers calls, books jobs, and follows up — 24/7.
        It knows your business and gets better every week. 48hr setup."
    </div>
    <p>{total_targets} qualified leads from {len(businesses)} businesses scanned</p>
    <p><a href="https://northwebpro.com">northwebpro.com</a></p>
</div>
</body></html>"""


def send_report(cache, zip_code, now):
    """Generate HTML report, write to file, send to Telegram. ponytail: extracted from 2 duplicate blocks."""
    html = generate_html_report(cache, zip_code)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"scout-report-{now.strftime('%Y%m%d_%H%M')}.html"
    with open(report_path, "w") as f:
        f.write(html)
    hermes = shutil.which("hermes") or os.path.expanduser("~/.local/bin/hermes")
    if hermes:
        subprocess.run([hermes, "send", "-t", "telegram:-5131689526",
            f"Lead Scout Report — {now.strftime('%b %d, %H:%M')} PT\nMEDIA:{report_path}"],
            timeout=30)
    print(f"HTML report sent: {report_path}")


def main():
    parser = argparse.ArgumentParser(description="92562 Local Business Scout v8")
    parser.add_argument("--zip", default="92562")
    parser.add_argument("--state", default="CA")
    parser.add_argument("--output", help="Write report to file")
    parser.add_argument("--delay", type=float, default=6.0, help="Delay between queries (seconds)")
    parser.add_argument("--max-checks", type=int, default=20, help="Max website checks per run")
    parser.add_argument("--group", type=int, help="Force a specific query group (0-5)")
    parser.add_argument("--briefing", action="store_true", help="Just print the briefing from cache (no crawl)")
    parser.add_argument("--html", action="store_true", help="Generate HTML report instead of text")
    parser.add_argument("--backup", action="store_true", help="Write a timestamped cache backup")
    args = parser.parse_args()

    cache = load_cache()
    now = datetime.now(timezone.utc)

    # ── BRIEFING-ONLY MODE ──
    if args.briefing:
        if args.html:
            send_report(cache, args.zip, now)
        sys.exit(0)

    # ── PICK NEXT QUERY GROUP ──
    group_idx = args.group if args.group is not None else (cache.get("last_group", -1) + 1) % len(TRADE_GROUPS)
    group = TRADE_GROUPS[group_idx]
    log(f"Using query group {group_idx}: {list(group.keys())}")

    # ── CRAWL: FIND BUSINESSES ──
    searx_ok = searx_empty = 0
    seen_urls = set(b.get("url", "") for b in cache.get("businesses", {}).values())
    checks_done = 0

    log("Crawling businesses...")
    for trade, queries in group.items():
        if trade.startswith("_"):
            continue
        for q in queries:
            results = searx_search(q, limit=15, delay=args.delay)
            if not results:
                searx_empty += 1
                log(f"  Empty: {q}")
                time.sleep(args.delay)
                continue
            searx_ok += 1
            log(f"  Got {len(results)} results: {q}")

            for r in results:
                url = r.get("url", "")
                title = r.get("title", "")
                snippet = r.get("content", "")
                if is_aggregator(title, url):
                    continue
                name = clean_name(title)
                if len(name) < 3:
                    continue
                urlkey = re.sub(r'https?://(www\.)?', '', url.lower()).rstrip('/')
                if urlkey in seen_urls:
                    continue
                seen_urls.add(urlkey)

                phones = extract_phones(title + " " + snippet)
                domain = re.sub(r'https?://(www\.)?', '', url.lower()).split('/')[0]
                is_own_site = not any(agg in domain for agg in AGGREGATOR_DOMAINS)

                # Fix 2: dedup by domain — find existing entry with same domain
                existing_norm = None
                if is_own_site:
                    for en, eb in cache["businesses"].items():
                        if domain in eb.get("own_domains", []):
                            existing_norm = en
                            break
                norm = existing_norm or re.sub(r'[^a-z0-9]', '', name.lower())[:25]

                if norm in cache["businesses"]:
                    b = cache["businesses"][norm]
                    b["last_seen"] = now.isoformat()
                    if is_own_site and domain not in b.get("own_domains", []):
                        b["own_domains"].append(domain)
                        b["has_own_site"] = True
                    if not is_own_site and domain not in b.get("dir_domains", []):
                        b["dir_domains"].append(domain)
                    for p in phones:
                        if p not in b.get("phones", []):
                            b["phones"].append(p)
                    if snippet and len(snippet) > len(b.get("snippet", "")):
                        b["snippet"] = snippet[:200]
                else:
                    cache["businesses"][norm] = {
                        "name": name, "trade": trade, "phones": phones,
                        "snippet": snippet[:200], "has_own_site": is_own_site,
                        "own_domains": [domain] if is_own_site else [],
                        "dir_domains": [] if is_own_site else [domain],
                        "first_seen": now.isoformat(), "last_seen": now.isoformat(),
                        "url": urlkey, "site_quality": None,
                    }
            time.sleep(args.delay)

    # ── CHECK WEBSITES ──
    log("Checking websites...")
    checked_domains = set()
    for norm, biz in cache["businesses"].items():
        if not biz["has_own_site"] or not biz.get("own_domains"):
            continue
        if biz.get("site_quality") and checks_done >= args.max_checks:
            continue
        domain = biz["own_domains"][0]
        if domain in checked_domains:
            continue
        checked_domains.add(domain)
        if biz.get("site_quality") and biz["site_quality"].get("website_score", -2) >= 0:
            continue  # Already successfully checked
        biz["site_quality"] = check_website(domain)
        sq = biz["site_quality"]
        # Merge phones found on the website into the business entry
        if sq and sq.get("phones"):
            for p in sq["phones"]:
                if p not in biz.get("phones", []):
                    biz.setdefault("phones", []).append(p)
        # Phase 3: Merge emails found on the website into the business entry
        if sq and sq.get("emails"):
            existing_emails = biz.get("emails", [])
            for e in sq["emails"]:
                if e not in existing_emails:
                    existing_emails.append(e)
            biz["emails"] = existing_emails
        # Compute lead qualification score
        biz["lead_score"] = qualify_lead(biz, sq)
        checks_done += 1
        time.sleep(0.5)

    log(f"Websites checked: {checks_done}")

    # ── PHASE 2: HIRING + REVIEW SIGNALS for top-scored leads ──
    # Only run signal searches for leads that already have a website check (sq present)
    # and haven't been checked yet. Limit to top 8 leads per run to respect rate limits.
    scored_leads = []
    for norm, biz in cache["businesses"].items():
        sq = biz.get("site_quality")
        if not sq or sq.get("status") not in ("up", "blocked", "down"):
            continue
        if biz.get("hiring_checked") and biz.get("review_checked"):
            continue  # Already checked both
        score = biz.get("lead_score", {}).get("score", 0)
        scored_leads.append((score, norm, biz))
    # Sort by score descending, take top 8
    scored_leads.sort(key=lambda x: x[0], reverse=True)
    signal_checks = 0
    max_signal_checks = 8  # 8 leads × up to 3 queries × 6s delay ≈ 2.5 min max

    for score, norm, biz in scored_leads:
        if signal_checks >= max_signal_checks:
            break
        biz_name = biz.get("name", "")
        if not biz.get("hiring_checked") and len(biz_name) >= 3:
            log(f"  Hiring signals: {biz_name}")
            search_hiring_signals(biz_name, norm, cache)
            signal_checks += 1
            time.sleep(6)
        if signal_checks >= max_signal_checks:
            break
        if not biz.get("review_checked") and len(biz_name) >= 3:
            log(f"  Review signals: {biz_name}")
            search_review_signals(biz_name, norm, cache)
            signal_checks += 1
            time.sleep(6)
        # Recompute lead score with new signals
        biz["lead_score"] = qualify_lead(biz, biz.get("site_quality"))

    if signal_checks:
        log(f"Signal checks done: {signal_checks}")

    # ── BUYING SIGNALS ──
    if group.get("_signals"):
        log("Searching buying signals...")
        signal_queries = [
            "site:reddit.com Murrieta contractor recommend",
            "site:reddit.com Temecula plumber electrician",
            "Murrieta CA new business grand opening 2025 2026",
            "Temecula local business Facebook group",
        ]
        all_seen = set(s.get("url", "") for s in cache.get("signals", []))
        all_seen |= set(g.get("url", "") for g in cache.get("fb_groups", []))

        for q in signal_queries:
            results = searx_search(q, limit=8, delay=args.delay)
            for r in results:
                url = r.get("url", "")
                title = r.get("title", "")
                snippet = r.get("content", "")
                key = re.sub(r'https?://(www\.)?', '', url.lower()).rstrip('/')
                if key in all_seen:
                    continue
                all_seen.add(key)
                domain = re.sub(r'https?://(www\.)?', '', url.lower()).split('/')[0]
                skip = AGGREGATOR_DOMAINS | {".gov", ".edu", "wikipedia.org", "calmatters.org", "bizbuysell.com", "city-data.com"}
                if any(s in domain for s in skip):
                    continue
                if "facebook.com/groups" in url.lower():
                    cache.setdefault("fb_groups", []).append({"name": title[:60], "url": key})
                    continue
                text = (title + " " + snippet).lower()
                is_person = any(d in domain for d in ["reddit.com", "facebook.com"])
                has_buying = any(phrase in text for phrase in [
                    "looking for", "need a", "can anyone recommend", "who does",
                    "any suggestions", "need help with", "grand opening",
                    "new business", "just opened", "hiring",
                ])
                if is_person or has_buying:
                    source = "Reddit" if "reddit" in domain else "FB" if "facebook" in domain else "Web"
                    cache.setdefault("signals", []).append({
                        "title": title[:70], "snippet": snippet[:150],
                        "source": source, "url": key, "date": now.isoformat(),
                    })
            time.sleep(args.delay)

    # ── SAVE CACHE + BACKUP ──
    cache["last_group"] = group_idx
    cache["runs"] = cache.get("runs", 0) + 1
    cache["last_run"] = now.isoformat()
    save_cache(cache)

    if args.backup:
        backup_path = backup_cache(cache)
        log(f"Cache backed up to {backup_path}")

    log(f"Cache: {len(cache['businesses'])} businesses, {len(cache.get('signals', []))} signals, {len(cache.get('fb_groups', []))} groups")
    log(f"Queries: {searx_ok} ok / {searx_empty} empty")

    # ── OUTPUT ──
    if args.html:
        send_report(cache, args.zip, now)
    sys.exit(0)


if __name__ == "__main__":
    main()
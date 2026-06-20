#!/usr/bin/env python3
"""
92562 Local Business Scout v9 — Lead qualification pipeline for North Web Pro.

Scrapes SearXNG for local businesses (trades + admin/ops), audits websites
for automation gaps, scores buying readiness (0-100), delivers HTML report.
Ponytail v9: dead code removed, ~1399 lines.
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

# T6: Roles where hiring = "you're about to pay a human to do agent work"
AUTOMATABLE_ROLES = [
    "receptionist", "front desk", "scheduler", "scheduling", "intake",
    "dispatcher", "dispatch", "administrative assistant", "admin assistant",
    "data entry", "office assistant", "customer service rep",
    "appointment coordinator", "office manager", "billing coordinator",
    "accounts receivable", "accounts payable", "bookkeeper",
]
# T6: Hiring verbs that prove the page is an actual job posting, not a query echo
HIRING_VERBS = [
    "now hiring", "we're hiring", "we are hiring", "join our team",
    "apply now", "apply today", "open position", "job opening",
    "career opportunity", "careers at", "work with us",
]

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
# Edit the ICP philosophy here — see PRD.md §2.
# 5-pillar buying-readiness model; max 100. Contactability is a gate, not a scored pillar.
SCORING = {
    "repetitive_work": {        # T3: Does this biz drown in automatable manual work?
        "max": 35,
        "admin_ops": 25,                # trade in ADMIN_TRADES (verified site only)
        "appointment_no_booking": 10,   # appt trade + no booking system (verified)
    },
    "named_pain": {             # T5: Have customers stated the exact pain we solve?
        "max": 25,
        "review_complaint": 25,         # slow/no-callback/no-response in reviews
    },
    "growth_budget": {          # T5: Can they pay a retainer? Are they straining?
        "max": 25,
        "automatable_role": 25,         # hiring receptionist/scheduler/intake/etc.
        "generic_hiring": 12,           # generic hiring signal
        "multi_signal": 8,              # 2+ phones OR 2+ domains = operational complexity
        "trade_admin": 8,               # ADMIN_TRADES prior (verified only)
        "trade_appt": 5,                # appointment trade prior (verified only)
    },
    "digital_footing": {        # T2: Enough maturity to integrate with? Real gaps?
        "max": 15,
        "site_down": 3,                 # T2: cap — down alone can never reach Hot
        "site_blocked": 5,              # T2: cap — blocked alone can never reach Hot
        "ws_low": 15,                   # website_score <= 1 (verified site)
        "ws_2": 8,
        "ws_3": 4,
        "outdated_email": 3,
        "fax": 3,
    },
    # Appointment-heavy trades where "no booking system" signals manual drag
    "appointment_trades": ("HVAC", "Plumbing", "Auto Repair", "Carpet Cleaning", "Handyman"),
    "tiers": {"hot": 65, "warm": 40},
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
            # Skip area codes that are clearly not real (000 = invalid, 999 = test)
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

# T15/T17: deeper, honester fetching
FETCH_TIMEOUT = 20            # seconds per request (was 12 — slow small-biz hosts)
FETCH_BUDGET = 150000        # bytes read per page (was 12000 — phones live in footers)
MAX_FETCHES = 4              # distinct URL fetches per business (candidates + subpages)
MAX_SUBPAGE_FETCHES = 2      # how many /contact + /about pages to pull in
# T17: SPA bootstrap markers. A near-empty page carrying one of these is a
# JS-rendered shell we couldn't read — UNKNOWN, not a thin/dead site.
JS_SHELL_MARKERS = (
    'id="root"', "id='root'", "__next_data__", "data-reactroot", "ng-version",
    'id="__nuxt"', 'id="app"', "data-react-helmet", "data-server-rendered",
)


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


def parse_jsonld(html):
    """T16: Extract authoritative contact facts from schema.org JSON-LD blocks
    (`<script type="application/ld+json">`). Far more reliable than regex on
    rendered text. Best-effort and never raises — malformed blocks are skipped.
    Returns {"phones","emails","socials","address","hours"}."""
    out = {"phones": [], "emails": [], "socials": [], "address": "", "hours": []}
    blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.I | re.S)
    nodes = []

    def collect(d):
        if isinstance(d, list):
            for x in d:
                collect(x)
        elif isinstance(d, dict):
            if "@graph" in d:
                collect(d["@graph"])
            nodes.append(d)

    for blk in blocks:
        try:
            collect(json.loads(blk.strip()))
        except Exception:
            continue  # malformed JSON-LD — skip, don't crash

    for node in nodes:
        if not isinstance(node, dict):
            continue
        tel = node.get("telephone")
        if isinstance(tel, str):
            out["phones"].append(tel)
        em = node.get("email")
        if isinstance(em, str):
            out["emails"].append(em.replace("mailto:", "").strip().lower())
        same = node.get("sameAs")
        if isinstance(same, str):
            out["socials"].append(same)
        elif isinstance(same, list):
            out["socials"] += [s for s in same if isinstance(s, str)]
        addr = node.get("address")
        if isinstance(addr, dict):
            parts = [addr.get(k, "") for k in
                     ("streetAddress", "addressLocality", "addressRegion", "postalCode")]
            out["address"] = out["address"] or ", ".join(p for p in parts if p)
        elif isinstance(addr, str):
            out["address"] = out["address"] or addr
        hrs = node.get("openingHours")
        if isinstance(hrs, str):
            out["hours"].append(hrs)
        elif isinstance(hrs, list):
            out["hours"] += [h for h in hrs if isinstance(h, str)]
    return out


def _base_result(status, confidence, gaps):
    """Base result dict for check_website — avoids repeating 13 keys 4 times."""
    return {"status": status, "confidence": confidence,
            "website_score": -1, "automation_gaps": gaps,
            "platform": "Unknown" if status != "down" else "N/A",
            "words": 0, "phones": [],
            "has_crm": [], "has_analytics": [], "has_marketing_tools": [],
            "has_booking_system": [], "emails": [],
            "has_outdated_email": False, "has_fax": False, "socials": []}


def _absolutize(href, base_url, base_domain):
    """Resolve an href to a same-domain absolute URL, or None if off-site/non-http."""
    href = href.strip()
    if not href or href[0] == "#" or href.lower().startswith(("mailto:", "tel:", "javascript:")):
        return None
    if href.startswith("//"):
        return None
    if href.lower().startswith(("http://", "https://")):
        host = re.sub(r'https?://(www\.)?', '', href.lower()).split('/')[0]
        return href if base_domain in host else None
    return urllib.parse.urljoin(base_url, href)


def _fetch_html(url, timeout=FETCH_TIMEOUT, retries=1):
    """Fetch one URL, rotating ALL user-agents before giving up (so a single 403
    from the first UA doesn't declare the whole site blocked). One backoff retry
    on transient failure. Returns {"ok":True,"html","final_url"} or
    {"ok":False,"reason":"blocked"|"http"|"unreachable","code":...}."""
    blocked = False
    http_code = None
    for attempt in range(retries + 1):
        for ua in USER_AGENTS:
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": ua, "Accept": "text/html,application/xhtml+xml"})
                with urllib.request.urlopen(req, timeout=timeout, context=_NOVERIFY_CTX) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")[:FETCH_BUDGET]
                    return {"ok": True, "html": html, "final_url": resp.geturl()}
            except urllib.error.HTTPError as e:
                if e.code in (403, 401, 429):
                    blocked = True       # try the other UAs before concluding "blocked"
                    continue
                http_code = e.code       # 404 / 5xx — try next UA too
                continue
            except (urllib.error.URLError, Exception):
                continue
        if attempt < retries:
            time.sleep(3)
    if blocked:
        return {"ok": False, "reason": "blocked", "code": 403}
    if http_code:
        return {"ok": False, "reason": "http", "code": http_code}
    return {"ok": False, "reason": "unreachable", "code": None}


def check_website(domain):
    """Robust, honest website check. Deep read (150KB), 20s timeout + retry,
    www/non-www fallback, and a /contact + /about crawl to fill phone/contact gaps
    so we stop reporting false "no phone / no contact" on pages that are fine.
    Failed fetches return UNKNOWN, never confident "down" (see T13/T14)."""
    base = domain[4:] if domain.lower().startswith("www.") else domain
    candidates = [f"https://{base}", f"https://www.{base}", f"http://{base}"]

    page = None
    blocked = False
    http_err = None
    fetches = 0
    for i, url in enumerate(candidates):
        if fetches >= MAX_FETCHES:
            break
        fetches += 1
        res = _fetch_html(url, retries=1 if i == 0 else 0)
        if res["ok"]:
            page = res
            break
        if res["reason"] == "blocked":
            blocked = True
        elif res["reason"] == "http":
            http_err = res.get("code")

    if page is None:
        if blocked:
            return _base_result("blocked", "low", ["bot-protected — can't verify"])
        if http_err:
            return _base_result("unknown", "low", [f"HTTP {http_err} — can't verify"])
        return _base_result("unknown", "low", ["unreachable — couldn't connect"])

    html = page["html"]
    html_lower = html.lower()

    if any(m in html_lower for m in ("cf-browser-verification", "checking your browser", "cf-challenge")):
        return _base_result("blocked", "low", ["bot-protected — can't verify"])

    # Near-empty page we *connected* to = genuinely dead/parked (T14 reserves "down").
    if len(html_lower) < 200:
        return _base_result("down", "low", ["near-empty page"])

    # T17: JS-rendered shell — lots of <script>, almost no readable text, plus an
    # SPA bootstrap marker → we couldn't read it. UNKNOWN, never "thin content".
    text = re.sub(r'<[^>]+>', ' ', html_lower)
    words = len(text.split())
    if words < 200 and any(mk in html_lower for mk in JS_SHELL_MARKERS):
        return _base_result("unknown", "low", ["JS-rendered — couldn't read content"])

    # ── platform + tools (homepage only) ──
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

    # ── T15: pull in /contact and /about to fill contact/phone gaps ──
    combined = html
    sub_links = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
        if any(k in href.lower() for k in ("contact", "about")):
            full = _absolutize(href, page["final_url"], base)
            if full and full not in sub_links:
                sub_links.append(full)
        if len(sub_links) >= MAX_SUBPAGE_FETCHES:
            break
    for link in sub_links:
        if fetches >= MAX_FETCHES:
            break
        fetches += 1
        sub = _fetch_html(link, retries=0)
        if sub["ok"]:
            combined += "\n" + sub["html"]
    combined_lower = combined.lower()

    # ── T16: schema.org JSON-LD is authoritative — merge its contact facts ──
    jl = parse_jsonld(combined)
    socials = []
    for s in jl["socials"]:
        if s not in socials:
            socials.append(s)

    # ── signals (combined homepage + subpages) ──
    emails = _extract_emails(combined)
    for e in jl["emails"]:
        e = e.strip().lower()
        if "@" in e and len(e) < 80 and e not in emails:
            emails.append(e)
    has_fax = bool(re.search(r'fax[\s:.]*[\(\d]{1,2}[\d\s\-\.\/\(\)]{10,}', combined_lower))

    has_viewport = "viewport" in html_lower
    has_tel = "tel:" in combined_lower
    has_contact = ("contact" in combined_lower) or bool(sub_links)
    has_booking_system = bool(booking_tools)
    has_chat = any(x in html_lower for x in ["chat", "intercom", "tawk", "drift", "olark"])

    gaps = []
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

    if words < 200:
        gaps.append(f"thin content ({words}w)")

    website_score = sum([has_viewport, has_tel, has_contact, words > 200, has_booking_system or has_chat])

    page_phones = extract_phones(combined)
    for tm in re.findall(r'href=["\']tel:([+\d\s()\-\.]+)', combined, re.I):
        digits = re.sub(r'\D', '', tm)
        if len(digits) == 10:
            formatted = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
            if formatted not in page_phones:
                page_phones.append(formatted)
    # T16: JSON-LD telephone is authoritative — normalize and merge
    for t in jl["phones"]:
        for formatted in extract_phones(t):
            if formatted not in page_phones:
                page_phones.append(formatted)

    has_outdated_email = any(any(od in addr for od in OUTDATED_EMAIL_DOMAINS) for addr in emails)

    return {"status": "up", "confidence": "high",
            "website_score": website_score, "automation_gaps": gaps,
            "platform": platform, "words": words, "phones": page_phones,
            "has_crm": crm_tools, "has_analytics": analytics_tools,
            "has_marketing_tools": marketing_tools,
            "has_booking_system": booking_tools, "emails": emails[:5],
            "has_outdated_email": has_outdated_email, "has_fax": has_fax,
            "socials": socials[:6]}


def search_hiring_signals(biz_name, cache_key, cache):
    """T6+T10: Role-aware hiring search. Only flags when a real hiring verb + biz name
    appear together; sets hiring_role_match=True for AUTOMATABLE_ROLES matches.
    T10: indeed/ziprecruiter/linkedin job URLs are intentionally allowed here (they're
    blocked in the crawl loop as 'not real businesses', but they're authoritative signal
    sources for whether a named business is hiring an automatable role)."""
    if not biz_name or len(biz_name) < 3:
        return False
    cached = cache.get("businesses", {}).get(cache_key, {})
    if cached.get("hiring_checked"):
        return bool(cached.get("hiring_signals", []))

    biz_name_lower = biz_name.lower()
    # Short words (≤4 chars) match too broadly in URL/title fragments — skip name check for them
    name_words = [w for w in biz_name_lower.split() if len(w) > 4]

    hiring_found = False
    role_match = False
    hiring_results = []

    for q in [f"{biz_name} hiring", f"{biz_name} jobs"]:
        results = searx_search(q, limit=6, delay=6)
        for r in results:
            title = (r.get("title", "") or "").lower()
            snippet = (r.get("content", "") or "").lower()
            url = (r.get("url", "") or "").lower()
            combined = title + " " + snippet

            # T6: require a real hiring verb, not just the echoed query keyword
            has_verb = any(v in combined for v in HIRING_VERBS)
            # T6: require biz name in title or URL to avoid unrelated aggregator pages
            name_present = any(w in title or w in url for w in name_words) if name_words else (biz_name_lower[:6] in title or biz_name_lower[:6] in url)
            if not (has_verb and name_present):
                continue

            hiring_found = True
            # T6: flag automatable role if mentioned
            if any(role in combined for role in AUTOMATABLE_ROLES):
                role_match = True
            hiring_results.append({
                "title": r.get("title", "")[:70],
                "snippet": (r.get("content", "") or "")[:120],
                "url": r.get("url", ""),
            })
        if hiring_found:
            break
        time.sleep(6)

    biz_entry = cache.setdefault("businesses", {}).setdefault(cache_key, {})
    biz_entry["hiring_signals"] = hiring_results[:5]
    biz_entry["hiring_checked"] = True
    biz_entry["hiring_role_match"] = role_match
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


def corroborated(review_signals):
    """T18: PRD §2b rule 7 — soft signal counts only when seen in ≥2 independent
    sources or from a structured source. Tier-1 job postings are exempt (caller decides)."""
    return sum(1 for r in review_signals if r.get("complaints")) >= 2


def qualify_lead(biz, sq):
    """5-pillar buying-readiness score (0-100). See PRD.md §2 for the model.
    T13 confidence gate: site-derived points require status==up+confidence==high.
    T4 contactability gate: no phone AND no email → tier capped at Cold.
    T5 tier rules: Hot requires contactable + strong qualifier + total ≥ 65.
    T18 corroboration: review complaints require ≥2 review results with complaints."""
    if not sq:
        return {"score": 0, "tier": "Cold", "breakdown": {}, "reasons": ["not yet analyzed"]}

    breakdown = {}
    reasons = []

    RW = SCORING["repetitive_work"]
    NP = SCORING["named_pain"]
    GB = SCORING["growth_budget"]
    DF = SCORING["digital_footing"]

    gaps = sq.get("automation_gaps", [])
    status = sq.get("status", "unknown")
    # T13: only award site-derived points when we actually read the site
    verified = status == "up" and sq.get("confidence") == "high"
    trade = biz.get("trade", "")
    phones_list = biz.get("phones", [])
    emails = biz.get("emails", []) or sq.get("emails", [])

    # ── REPETITIVE-WORK LOAD (T3: admin/ops trade or appt trade + no booking) ──
    rw = 0
    if verified:
        if trade in ADMIN_TRADES:
            rw += RW["admin_ops"]
            reasons.append(f"admin/ops business — high intake/scheduling load (+{RW['admin_ops']})")
        elif trade in SCORING["appointment_trades"] and any(
            g in gaps for g in ("no booking system", "no booking/chat system")
        ):
            rw += RW["appointment_no_booking"]
            reasons.append(f"appointment trade with no booking system (+{RW['appointment_no_booking']})")
    breakdown["repetitive_work"] = min(rw, RW["max"])

    # ── NAMED PAIN (external signal — exempt from verified gate) ──
    # T18: require corroboration — complaint in ≥2 independent review results (PRD §2b rule 7)
    np_score = 0
    review_signals = biz.get("review_signals", [])
    if biz.get("review_negative") and corroborated(review_signals):
        np_score += NP["review_complaint"]
        reasons.append(f"customers report slow/no response — our exact pitch (+{NP['review_complaint']})")
    elif biz.get("review_negative"):
        reasons.append("single complaint mention — needs corroboration to score")
    breakdown["named_pain"] = min(np_score, NP["max"])

    # ── GROWTH & BUDGET (T5) ──
    gb = 0
    hiring_role_match = biz.get("hiring_role_match", False)
    hiring_signals = biz.get("hiring_signals", [])
    if hiring_role_match:
        gb += GB["automatable_role"]
        reasons.append(f"hiring for an automatable role (+{GB['automatable_role']})")
    elif hiring_signals:
        gb += GB["generic_hiring"]
        reasons.append(f"generic hiring signal (+{GB['generic_hiring']})")
    elif verified:
        # Trade prior: operational complexity proxy — only when we read the site (T13)
        if trade in ADMIN_TRADES:
            gb += GB["trade_admin"]
            reasons.append(f"admin/ops trade — budget proxy (+{GB['trade_admin']})")
        elif trade in SCORING["appointment_trades"]:
            gb += GB["trade_appt"]
            reasons.append(f"appointment trade — budget proxy (+{GB['trade_appt']})")
    # Multi-location / multi-phone = operational complexity, independent of site read
    if len(phones_list) > 1 or len(biz.get("own_domains", [])) > 1:
        gb += GB["multi_signal"]
        reasons.append(f"multi-location / multi-phone (+{GB['multi_signal']})")
    breakdown["growth_budget"] = min(gb, GB["max"])

    # ── DIGITAL FOOTING (T2: down/blocked capped low; verified earns full range) ──
    df = 0
    if status == "down":
        df += DF["site_down"]
        reasons.append(f"site appears down (+{DF['site_down']})")
    elif status == "blocked":
        df += DF["site_blocked"]
        reasons.append(f"site bot-protected (+{DF['site_blocked']})")
    elif verified:
        ws = sq.get("website_score", -1)
        if ws <= 1:
            df += DF["ws_low"]
            reasons.append(f"website score {ws}/5 — major gap (+{DF['ws_low']})")
        elif ws == 2:
            df += DF["ws_2"]
            reasons.append(f"website score 2/5 (+{DF['ws_2']})")
        elif ws == 3:
            df += DF["ws_3"]
            reasons.append(f"website score 3/5 (+{DF['ws_3']})")
        if sq.get("has_outdated_email"):
            df += DF["outdated_email"]
            reasons.append(f"outdated email provider (+{DF['outdated_email']})")
        if sq.get("has_fax"):
            df += DF["fax"]
            reasons.append(f"fax number — paper-based (+{DF['fax']})")
    breakdown["digital_footing"] = min(df, DF["max"])

    if not verified and status not in ("down", "blocked"):
        reasons.append("site unverified — scored on external signals only")

    # ── TOTAL ──
    total = (breakdown["repetitive_work"] + breakdown["named_pain"] +
             breakdown["growth_budget"] + breakdown["digital_footing"])

    # ── CONTACTABILITY GATE (T4) ──
    contactable = bool(phones_list) or bool(emails)

    # ── TIER RULES (T5) ──
    # Hot: contactable AND (named pain OR automatable-role hiring OR verified admin/ops) AND ≥65
    hot_qualifier = (
        (biz.get("review_negative") and corroborated(review_signals))
        or hiring_role_match
        or (trade in ADMIN_TRADES and verified)
    )
    if contactable and hot_qualifier and total >= SCORING["tiers"]["hot"]:
        tier = "Hot"
    elif contactable and total >= SCORING["tiers"]["warm"]:
        tier = "Warm"
    else:
        tier = "Cold"
        if not contactable:
            reasons.append("no contact info — can't reach")

    # Unverified: site unreadable + no external signals + not contactable → quarantine
    if (status in ("down", "blocked", "unknown")
            and not biz.get("review_negative")
            and not hiring_signals
            and not contactable):
        tier = "Unverified"

    return {"score": total, "tier": tier, "breakdown": breakdown, "reasons": reasons}


def _test_qualify_lead():
    """ponytail: assert-based acceptance check for T2–T5 scoring rules."""
    _sq_up = {"status": "up", "confidence": "high", "website_score": 2,
               "automation_gaps": ["no booking system"], "emails": []}
    _sq_down = {"status": "down", "confidence": "low", "website_score": -1,
                "automation_gaps": [], "emails": []}
    _sq_unknown = {"status": "unknown", "confidence": "low", "automation_gaps": [], "emails": []}

    # T2: down-only business scores ≤ 10 and is Cold or Unverified (never Hot)
    r = qualify_lead({"trade": "Plumbing", "phones": [], "own_domains": []}, _sq_down)
    assert r["tier"] in ("Cold", "Unverified"), f"T2 fail: down-only → {r['tier']}"
    assert r["score"] <= 10, f"T2 fail: down-only score {r['score']} > 10"

    # T2/T4: down + no contact → Unverified
    r2 = qualify_lead({"trade": "Plumbing", "phones": [], "own_domains": [],
                        "hiring_signals": [], "review_negative": False}, _sq_down)
    assert r2["tier"] == "Unverified", f"T2 fail: down+no-contact → {r2['tier']}"

    # T3: admin/ops + verified site should score repetitive_work=25
    r3 = qualify_lead({"trade": "Accounting", "phones": ["(951) 555-1234"], "own_domains": ["a.com"]}, _sq_up)
    assert r3["breakdown"]["repetitive_work"] == 25, f"T3 fail: admin rw={r3['breakdown']['repetitive_work']}"

    # T4: high score but no contact → Cold
    r4 = qualify_lead({"trade": "Accounting", "phones": [], "own_domains": ["a.com"],
                        "review_negative": True, "hiring_role_match": True,
                        "hiring_signals": [{"title": "x"}]},
                       {"status": "up", "confidence": "high", "website_score": 1,
                        "automation_gaps": [], "emails": []})
    assert r4["tier"] == "Cold", f"T4 fail: no-contact high-score → {r4['tier']}"

    # T5: gap-stacking alone (no admin trade, no complaint, no hiring) cannot reach Hot
    r5 = qualify_lead({"trade": "Plumbing", "phones": ["(951) 555-0001"], "own_domains": ["b.com"],
                        "hiring_signals": [], "review_negative": False},
                       {"status": "up", "confidence": "high", "website_score": 1,
                        "automation_gaps": ["no booking system"], "emails": []})
    assert r5["tier"] != "Hot", f"T5 fail: gap-stacking → Hot (score={r5['score']})"

    # T5+T18: admin + corroborated complaint + phone → Hot
    # Two review_signals entries each with complaints satisfies corroborated()
    r6 = qualify_lead({"trade": "Accounting", "phones": ["(951) 555-0001"],
                        "own_domains": ["c.com"], "review_negative": True,
                        "review_signals": [
                            {"complaints": ["slow"], "title": "r1"},
                            {"complaints": ["no response"], "title": "r2"},
                        ],
                        "hiring_signals": [], "hiring_role_match": False},
                       {"status": "up", "confidence": "high", "website_score": 2,
                        "automation_gaps": [], "emails": []})
    assert r6["tier"] == "Hot", f"T5 fail: admin+corroborated-complaint → {r6['tier']} (score={r6['score']})"

    # T18: single complaint (no corroboration) does NOT earn named_pain points
    r7 = qualify_lead({"trade": "Accounting", "phones": ["(951) 555-0001"],
                        "own_domains": ["d.com"], "review_negative": True,
                        "review_signals": [{"complaints": ["slow"], "title": "r1"}],
                        "hiring_signals": [], "hiring_role_match": False},
                       {"status": "up", "confidence": "high", "website_score": 2,
                        "automation_gaps": [], "emails": []})
    assert r7["breakdown"]["named_pain"] == 0, f"T18 fail: single complaint → named_pain={r7['breakdown']['named_pain']}"
    print("qualify_lead self-check: all assertions passed")


def load_cache():
    """T8: Tier-aware TTL — Hot/Warm kept 30 days, Cold/Unverified 7 days.
    Also prunes signals and fb_groups older than 14 days."""
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
            now_iso = datetime.now(timezone.utc)
            cutoff_hot  = (now_iso - timedelta(days=30)).isoformat()
            cutoff_cold = (now_iso - timedelta(days=7)).isoformat()
            cutoff_sig  = (now_iso - timedelta(days=14)).isoformat()

            def _keep(v):
                tier = v.get("lead_score", {}).get("tier", "Cold")
                cutoff = cutoff_hot if tier in ("Hot", "Warm") else cutoff_cold
                return v.get("last_seen", "") > cutoff

            cache["businesses"] = {k: v for k, v in cache.get("businesses", {}).items() if _keep(v)}
            # Prune stale signals/fb_groups (no date field → keep to be safe)
            cache["signals"] = [s for s in cache.get("signals", []) if s.get("date", "z") > cutoff_sig]
            cache["fb_groups"] = [g for g in cache.get("fb_groups", []) if g.get("date", "z") > cutoff_sig]
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

/* Pitch line */
.pitch-line {
    margin-top: 8px; padding: 7px 10px;
    background: rgba(96,207,244,0.05); border-left: 2px solid #60CFF4;
    border-radius: 0 4px 4px 0; font-size: 0.8em; color: #60CFF4;
    line-height: 1.4;
}
.pitch-label { font-weight: 600; color: #60CFF4; margin-right: 4px; }

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


def pitch_for(biz):
    """T9: Derive a plain-English pitch line from the top buying signal.
    ponytail: priority table, first match wins."""
    trade = biz.get("trade", "")
    if biz.get("review_negative"):
        return "24/7 digital receptionist that answers + books every call — fix the slow-response complaints"
    if biz.get("hiring_role_match"):
        return "replace the role you're hiring for with a digital worker (no salary, no sick days)"
    if trade in ADMIN_TRADES:
        return "digital worker for intake, scheduling & follow-up — frees your staff for billable work"
    if biz.get("hiring_signals"):
        return "automate the role you're hiring for before you post the job"
    sq = biz.get("site_quality") or {}
    gaps = sq.get("automation_gaps", [])
    if any(g in gaps for g in ("no booking system", "no booking/chat system")):
        return "automated booking + reminders — capture every call that goes to voicemail"
    return "digital worker to handle intake, scheduling & follow-up"


def lead_score_badge(tier, score):
    """Generate a colored badge for the lead qualification tier."""
    if tier == "Hot":
        return f'<span class="badge badge-hot">{score}/100 HOT</span>'
    elif tier == "Warm":
        return f'<span class="badge badge-warm">{score}/100 WARM</span>'
    else:
        return f'<span class="badge badge-cold">{score}/100</span>'


def generate_html_report(cache, zip_code="92562", prev_run=None):
    """Phase 4: HTML report organized by lead qualification score, not website score.
    prev_run: the last_run value BEFORE this run started, used for NEW badges (T7)."""
    businesses = cache.get("businesses", {})
    signals = cache.get("signals", [])
    fb_groups = cache.get("fb_groups", [])
    now = datetime.now(timezone.utc)
    today = now.strftime("%a %b %d, %Y")
    total_runs = cache.get("runs", 0)
    # T7: use prev_run (captured before cache["last_run"] was overwritten) so NEW badges work
    new_cutoff = prev_run or (now - timedelta(hours=24)).isoformat()

    # Ensure every business has a lead_score
    for biz in businesses.values():
        if "lead_score" not in biz:
            biz["lead_score"] = qualify_lead(biz, biz.get("site_quality"))
        is_new = biz.get("first_seen", "") > new_cutoff
        biz["_new"] = is_new

    # T9: Categorize by lead tier — Unverified is its own bucket, not Cold
    hot, warm, cold, unverified = [], [], [], []
    for biz in businesses.values():
        ls = biz.get("lead_score", {})
        tier = ls.get("tier", "Cold")
        if tier == "Hot":
            hot.append(biz)
        elif tier == "Warm":
            warm.append(biz)
        elif tier == "Unverified":
            unverified.append(biz)
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
        elif status == "up":
            html += f'<span class="lead-status status-up">● UP {ws}/5</span>'
        else:
            # T14: unknown / unreachable — couldn't read, so don't claim "UP -1/5"
            html += '<span class="lead-status status-blocked">● UNVERIFIED</span>'
        html += '</div>'

        # T9: "Pitch this:" — the concrete offer, derived from signals (not scoring internals)
        if tier in ("Hot", "Warm"):
            pitch = pitch_for(biz)
            html += f'<div class="pitch-line"><span class="pitch-label">Pitch this:</span>{pitch}</div>'

        # T9: scoring reasons go into a collapsible detail, not the headline
        signal_reasons = [r for r in reasons if not r.startswith("no contact info")]
        if signal_reasons:
            html += '<details><summary>Why this score</summary><div class="lead-reasons">'
            for r in signal_reasons[:8]:
                html += f'<span class="reason-tag">{r}</span>'
            html += '</div></details>'

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

    # T9: UNVERIFIED — site unreadable, no external signals, no contact info
    # Collapsed by default; don't pollute the actionable lead stream
    if unverified:
        section = '<div class="section">'
        section += '<div class="section-title"><h2>🛰️ Unverified — couldn\'t confirm</h2>'
        section += f'<span class="badge badge-cold">{len(unverified)}</span></div>'
        section += f'<details><summary>{len(unverified)} businesses — site unreadable, no external signal, low confidence</summary>'
        for biz in unverified[:8]:
            section += render_lead_card(biz)
        if len(unverified) > 8:
            section += f'<p style="color:#444;font-size:0.75em;text-align:center;padding:8px;">+ {len(unverified)-8} more...</p>'
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


def send_report(cache, zip_code, now, prev_run=None):
    """Generate HTML report, write to file, send to Telegram. ponytail: extracted from 2 duplicate blocks."""
    html = generate_html_report(cache, zip_code, prev_run=prev_run)
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
    # T7: capture before any crawl so the report can mark truly-new businesses
    prev_run = cache.get("last_run")

    # ── BRIEFING-ONLY MODE ──
    if args.briefing:
        if args.html:
            send_report(cache, args.zip, now, prev_run=prev_run)
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
                # T11: no-domain businesses get a hash suffix so distinct firms with similar names don't collide
                norm = existing_norm or (re.sub(r'[^a-z0-9]', '', name.lower())[:20]
                                         + ("" if is_own_site else f"-{abs(hash(name)) % 1000}"))

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
        # T14: include "unknown" (unreachable) — those leads can ONLY be scored on
        # external signals, so they need the hiring/review search the most.
        if not sq or sq.get("status") not in ("up", "blocked", "down", "unknown"):
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
                    cache.setdefault("fb_groups", []).append({"name": title[:60], "url": key, "date": now.isoformat()})
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
        send_report(cache, args.zip, now, prev_run=prev_run)
    sys.exit(0)


if __name__ == "__main__":
    main()
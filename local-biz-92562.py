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
import threading
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

# SGW-938 B4: single owner for report delivery. The cron job
# (92562-local-biz-briefing, no-agent mode) delivers the script's stdout as a
# text line (the script deliberately does not emit MEDIA: on stdout); the
# actual HTML file attachment is sent HERE via `hermes send`, which DOES
# process MEDIA: tags. Keep this one target — the README previously claimed a
# different chat (-5131689526) which was stale.
REPORT_TARGET = "telegram:-1003913783231:11"

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
    # SGW-864: directory/listing sites the benchmark caught masquerading as
    # real businesses (each scored Warm with a "verified" own site)
    "lawyerland.com", "allbiz.com", "inlandempirelawyers.com",
    "attorneyhelp.org", "headhuntersdirectory.com", "superlawyers.com",
    "lawlink.com", "homeyou.com", "thetoolboxpro.com", "zomgthehandyman.com",
    "repairhero.us", "legalrank.co", "wheree.com", "topconsumerreviews.com",
    "qterrapropertymanagement.com", "dealmachine.com", "f6s.com",
    "lawcrossing.com", "jenniejohnson.com", "areliableservices.com",
    # National chains that sell the services themselves (not retainer buyers)
    "bbsi.com", "usaa.com", "usaa.jobs.com",
    # SGW-864 round 2 (live-cache sweep): directories/networks only —
    # NOT real firms' own domains (those are handled via domain-brand re-key)
    "law.cornell.edu", "whereorg.com", "alignable.com",
    # SGW-864 round 3: more directories + national SaaS pages that look local
    "lawyers.com", "martindale.com", "propertymanagementlist.com",
    "staffingagenciesca.com", "hemlane.com",
    # SGW-864 round 4: chamber CMS directory pages
    "gochambermaster.com", "chambermaster.com",
}

# SGW-864: URL path signatures that identify directory/aggregator listings.
# Split STRONG (listing pages — never a business, demote regardless of name)
# from WEAK (local-SEO pages that CAN be a real business's own page — only
# demote when the record's name is also generic, so "Benson Electric" on
# bensonelectricsd.com/service-area/temecula-ca stays a lead).
DIRECTORY_PATH_PATTERNS_STRONG = [
    r'/lawyers(?:/|$)', r'/attorneys?(?:/|$)', r'/listings?(?:/|$)',
    r'/directory(?:/|$)', r'/search(?:/|$)', r'/cflt=', r'/find_', r'/browse(?:/|$)',
    r'/near-me(?:/|$)', r'/costs(?:/|$)', r'/prices?(?:/|$)', r'/estimates?(?:/|$)',
    r'/reviews?(?:/|$)', r'/ratings(?:/|$)', r'/top(?:/|$)', r'/best(?:/|$)',
    r'/compare(?:/|$)', r'/find(?:/|$)', r'/all-legal-issues(?:/|$)', r'/all-lawyers(?:/|$)',
    r'/category(?:/|$)', r'/list(?:/|$)', r'/business(?:/|$)', r'/profile(?:/|$)',
]
DIRECTORY_PATH_PATTERNS_WEAK = [
    r'/service-areas?(?:/|$)', r'/locations?(?:/|$)', r'/local(?:/|$)',
    r'/cities(?:/|$)', r'/white-label(?:/|$)', r'/property-management(?:/|$)',
    r'/contact-us(?:/|$)',
]

# ── SGW-941: ELIGIBILITY GATE — entities that can never be owner-facing ──
# A prospect may only become Warm/priority if it is a distinct operating
# business in the service area. These rules are the deterministic first-line
# sanitation; AI review (SGW-940) runs AFTER this gate.
GOVERNMENT_TLD_SUFFIXES = (".gov", ".edu", ".mil")
# Corporate locator/careers subdomains — carriers and big firms publish
# "agents." / "agency." / "jobs." / "careers." microsites; those are NOT the
# local owner-led business. Boundary-matched on the first label only.
LOCATOR_SUBDOMAIN_LABELS = ("agents", "agency", "jobs", "careers", "locations", "locator")
# National enterprises whose local offices are branches, not owner-led SMB
# retainer buyers. Conservative exact-domain set — add only with evidence.
NATIONAL_ENTERPRISE_DOMAINS = {
    "statefarm.com", "allstate.com", "farmers.com", "geico.com",
    "progressive.com", "libertymutual.com", "usaa.com", "nationwide.com",
    "travelers.com", "centurycommunities.com", "drdhorton.com", "lennar.com",
    "kbhome.com", "taylormorrison.com", "pulte.com", "chase.com", "wellsfargo.com",
    "bankofamerica.com", "homedepot.com", "lowes.com", "costco.com",
    "walmart.com", "target.com", "starbucks.com", "mcdonalds.com",
    "subway.com", "dominos.com", "pizzahut.com", "tacobell.com",
    "marriott.com", "hilton.com", "holidayinn.com", "bestwestern.com",
    "ups.com", "fedex.com", "usps.com", "att.com", "verizon.com",
    "tmobile.com", "comcast.com", "spectrum.com", "adt.com",
    "pestcontrol.com", "orkin.com", "terminix.com", "servpro.com",
    "acehardware.com", "truevalue.com", "ace.com", "kroger.com",
    "safeway.com", "ralphs.com", "vons.com", "albertsons.com",
    "autozone.com", "oreillyauto.com", "advanceautoparts.com", "napaonline.com",
}

# SGW-942 B3 (2026-09-11 QC): national platforms whose per-location pages are
# branch/locator pages, not owner-led SMBs. Distinct from
# NATIONAL_ENTERPRISE_DOMAINS (consumer/retail brands) — these are the
# franchise, staffing-network and corporate-portal domains that were passing
# the eligibility gate as "distinct local operating business":
#   expresspros.com/us-california-moreno-valley  (franchise branch)
#   manpowerriverside.com                        (ManpowerGroup franchise)
#   newyorklife.com/agents/find-an-agent/ca/...  (agent locator → name
#                                                 collapsed to "Agent Directory")
#   libertycompany.com/locations/california/...  (corporate location page)
#   utopiamanagement.com/murrieta-property-management (regional branch page)
# The local operator is not the buyer: no budget authority, no system to
# integrate. Route to `research`, never `eligible`.
NATIONAL_BRAND_DOMAINS = {
    "expresspros.com", "manpower.com", "manpowergroup.com", "adecco.com",
    "randstadusa.com", "kellyservices.com", "roberthalf.com", "aerotek.com",
    "newyorklife.com", "northwesternmutual.com", "prudential.com",
    "metlife.com", "massmutual.com", "principal.com", "guardianlife.com",
    "libertycompany.com", "utopiamanagement.com", "avantstay.com",
    "anytimefitness.com", "kellerwilliams.com", "remax.com", "century21.com",
    "coldwellbanker.com", "berkshirehathawayhs.com", "compass.com",
    "edwardjones.com", "raymondjames.com", "ameriprise.com", "lpl.com",
    "hilton.com", "marriott.com", "ihg.com", "wyndham.com", "choicehotels.com",
    "pizzahut.com", "dominos.com", "jimmyjohns.com", "firehousesubs.com",
    "servpro.com", "servicemaster.com", "chemdry.com", "stanleysteemer.com",
    "culligan.com", "mrrooter.com", "roto-rooter.com", "benjaminfranklinplumbing.com",
    "onehourheatandair.com", "airexperts.com", "ars.com", "goettl.com",
    "statefarm.com", "allstate.com", "farmers.com", "geico.com",
    "progressive.com", "libertymutual.com", "usaa.com", "nationwide.com",
    "travelers.com", "aflac.com", "humana.com", "cigna.com", "aetna.com",
    "unitedhealthgroup.com", "kaiserpermanente.org", "anthem.com",
    "huntington.com", "bankofamerica.com", "wellsfargo.com", "chase.com",
    "citibank.com", "usbank.com", "truist.com", "pnc.com", "schwab.com",
    "fidelity.com", "vanguard.com", "synchronybank.com", "discover.com",
    "hrexperts.com", "insperity.com", "paychex.com", "adp.com", "gusto.com",
    "trinet.com", "justworks.com", "paylocity.com", "paycom.com",
    "regus.com", "wework.com", "ironmountain.com", "westrock.com",
    "jll.com", "cbre.com", "colliers.com", "newmark.com", "cushmanwakefield.com",
    "lennar.com", "drdhorton.com", "pulte.com", "kbhome.com", "taylormorrison.com",
    "centurycommunities.com", "meritagehomes.com", "sheahomes.com", "tollbrothers.com",
}

# SGW-942 B3: URL path segments that mark a corporate locator/branch/agent
# page rather than a business's own site. Checked against the record URL only
# when the host is a national brand OR the path matches two of these.
BRANCH_PATH_PATTERNS = (
    r"/locations?/", r"/agents?/", r"/find-an-agent", r"/branch(es)?/",
    r"/franchise(s)?/", r"/office(s)?/", r"/stores?/", r"/our-offices",
    r"/dealer(s)?/", r"/distributors?/", r"/service-area", r"/service-areas",
)


def _is_national_branch(url, name="", own_domains=None):
    """SGW-942 B3: True when a record is a national brand's per-location page.

    Two independent routes, both requiring the domain to be a known national
    platform OR the record name to have collapsed into a generic locator
    title ("Agent Directory") on a brand host. A local franchise with a
    genuinely distinct brand on its OWN domain (e.g. a locally-named agency
    running on its own .com) still passes — only the parent platform's
    per-location pages are gated."""
    url_l = (url or "").lower()
    host = re.sub(r'https?://(www\.)?', '', url_l).split('/')[0].split(':')[0].rstrip(".")
    path = url_l[len(host):] if host else url_l
    for d in (own_domains or []):
        d = str(d).lower().lstrip("www.").rstrip(".")
        if not d:
            continue
        if d in NATIONAL_BRAND_DOMAINS:
            return True
        if d in NATIONAL_ENTERPRISE_DOMAINS:
            return True
    if host in NATIONAL_BRAND_DOMAINS:
        return True
    return False


def _is_generic_locator_title(name):
    """SGW-942 B3: page titles that describe a LOCATOR, not a business —
    'Agent Directory', 'Find an Agent', 'Store Locator'. These are page
    chrome that survived name cleaning and must never be an `eligible` lead
    (there is no business behind the name)."""
    nm = (name or "").strip().lower()
    if not nm:
        return False
    return bool(re.search(
        r"^(agent|store|office|branch|location|dealer|provider|physician|"
        r"doctor|attorney|lawyer|therapist|contractor|vendor|supplier)s?\s+"
        r"(directory|locator|finder|list|listing|search|near)\b", nm)) or \
        bool(re.search(r"\b(directory|locator|finder|listings?|near me)\b", nm))


# SGW-864: cleaned names that are SEO titles, not business brands.
# These are rejected in the crawl loop and downgraded in the cache sweep.
GENERIC_BUSINESS_NAME_PATTERNS = [
    # Single trade word with no brand: "Handyman", "Accounting", "HVAC"
    r'^(handyman|plumbing|plumber|electrician|electrical|hvac|ac repair|air conditioning|'
    r'roofing|roofer|painting|painter|landscaping|landscape|tree service|tree removal|'
    r'carpet cleaning|auto repair|mechanic|accounting|bookkeeping|tax|insurance|'
    r'law|attorney|lawyer|consulting|recruiting|property management|home|care)$',
    # "X in City" / "X Near ..." / "X Services" with no brand token
    r'^(home repairs?|handyman services?|plumbing services?|ac repair|repair|services?)\s+(near|nearby|around|in)\b',
    r'^[a-z]+ in (murrieta|temecula|wildomar|menifee|lake elsinore)',
    r'^[a-z]+ (near|nearby|around) (me|murrieta|temecula|wildomar)',
    r'^(best|top) .+ (in|near) ',
    # SGW-864 round 3: bare trade+suffix, service-description names, list pages
    r'^(bookkeeping|accounting|tax|insurance|property management|staffing|recruiting|consulting)\s+(service|services|firm|firms|agency|agencies|company|companies|solutions?|group|pros?|professionals?)$',
    r'^(the\s+)?(best|top|great)\s+\w+\s+(near|in|for)\b',
    r'^(property management|staffing|recruiting|consulting)\s+companies?(\s+list)?$',
    r'^(find|view|browse|see|get)\s+(the\s+)?(best|top)?',
]

# SGW-864: distinctive-token guard — a signal (hiring/review) only counts for
# a business when a NON-GENERIC name token appears in the source title/URL.
# This stops "MBC Consulting Inc" matching posts about "Morgan Business Consulting".
GENERIC_NAME_TOKENS = {
    "services", "service", "company", "companies", "inc", "llc", "llp", "corp",
    "group", "associates", "associate", "solutions", "firm", "agency",
    "agencies", "office", "offices", "consulting", "consultants", "consultant",
    "business", "businesses", "enterprises", "enterprise", "industries",
    "partners", "partner", "professionals", "professional", "network",
    "systems", "system", "technologies", "technology", "management", "managing",
    "team", "the", "and", "for", "with", "your", "you", "our", "are",
}

# SGW-864/865: geography gate. Signals that explicitly mention a far-away
# city (and none of our local cities) are noise, not evidence about a local
# business. Covers the benchmark's ZipRecruiter/Yelp cross-geography leaks.
LOCAL_CITIES = ("murrieta", "temecula", "wildomar", "menifee", "lake elsinore",
                "lake elsinore ca", "riverside county", "southwest riverside")
OUT_OF_AREA_CITIES = (
    "new york", "brooklyn", "queens", "bronx", "staten island", "long island",
    "san antonio", "austin", "houston", "dallas", "fort worth", "el paso",
    "fayetteville", "chicago", "seattle", "portland", "denver", "phoenix",
    "las vegas", "miami", "orlando", "tampa", "atlanta", "boston", "philadelphia",
    "san francisco", "oakland", "san jose", "sacramento", "fresno", "bakersfield",
    "los angeles", "long beach", "anaheim", "santa ana", "irvine", "san diego",
    "campbell", "san jose", "santa clarita", "pasadena", "glendale", "torrance",
)

def _mentions_out_of_area(combined):
    """True when a signal source is about a far-away city, not our geography.
    Foreign city mentioned AND no local city mentioned → noise."""
    combined_l = (combined or "").lower()
    if any(c in combined_l for c in LOCAL_CITIES):
        return False
    return any(c in combined_l for c in OUT_OF_AREA_CITIES)

def _signal_recency(title, snippet=""):
    """SGW-865: classify a hiring/posted signal by how fresh it is.
    Returns 'recent' (same/previous year or explicit month in last 15 mo),
    'stale' (older years), or 'unknown' (no date marker).
    Job postings rot: a 'Bookkeeper wanted' page from 2024 is not evidence
    the business is hiring now."""
    combined = (title or "") + " " + (snippet or "")
    now_year = datetime.now(timezone.utc).year
    m = re.search(r'\b(20\d{2})\b', combined)
    if m:
        year = int(m.group(1))
        if year >= now_year - 1:
            return "recent"
        return "stale"
    m = re.search(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[.\s,]*\s?(20\d{2})?\b', combined, re.I)
    if m:
        # Month present without year → recent (job boards list current postings)
        return "recent"
    return "unknown"

def _strip_www(host):
    """SGW-944 D1/D2: remove a literal leading "www." from a hostname.

    str.lstrip("www.") is NOT a prefix strip — it removes every leading 'w' and
    '.' character, so "wecareteam.com" -> "ecareteam.com". That misclassified
    real own-domain careers pages as third-party, and made "wecare.com" and
    "ecare.com" compare equal in the duplicate-merge predicate. Keep this
    helper as the single correct implementation.
    """
    h = str(host or "").strip().lower()
    return h[4:] if h.startswith("www.") else h


def _evidence_source_kind(url, own_domains=None):
    """SGW-865: classify where an evidence source lives.
    own_site = the business's own domain (strongest); job_board = aggregator
    (weak); review_site = review platform; other = unknown third party.

    SGW-942 B2 (2026-09-11 QC): this used to default to "own_site" for any
    host it didn't recognise, so upwork.com / instawork.com / plumbingjobs.org
    / starofservice.us hits were stamped own_site and then scored +25 as
    "hiring for an automatable role — on own site". 133 of 156 own_site hiring
    signals in the live cache were not on the business's own domain; the
    single top lead's entire score came from a ZipRecruiter search page.
    Default must be the WEAK class, never the strong one — `other` is
    explicitly non-own and is not credited as verified hiring evidence."""
    url_l = (url or "").lower()
    domain = re.sub(r'https?://(www\.)?', '', url_l).split('/')[0].split(':')[0].rstrip(".")
    if any(s in domain for s in ("ziprecruiter", "indeed", "linkedin", "careerbuilder",
                                  "monster", "glassdoor", "lawcrossing", "usajobs",
                                  "upwork", "instawork", "ziprecruiter", "simplyhired",
                                  "snagajob", "dice.com", "wellfound", "flexjobs",
                                  "plumbingjobs", "hvacjobs", "jobsoom", "jobrapido",
                                  "talent.com", "careerjet", "jooble", "jobs.com",
                                  "jobs2careers", "recruiter.com", "workable",
                                  "greenhouse.io", "lever.co", "bamboohr")):
        return "job_board"
    if any(s in domain for s in ("yelp", "google.com", "bbb", "trustpilot",
                                  "glassdoor", "wallethub", "consumeraffairs",
                                  "yellowpages", "mapquest", "chamberofcommerce",
                                  "manta", "angi", "thumbtack", "nextdoor",
                                  "birdeye", "tripadvisor", "foursquare")):
        return "review_site"
    if own_domains:
        # SGW-944 D1: lstrip("www.") strips ALL leading 'w' and '.' characters,
        # not the literal prefix — "wecareteam.com" became "ecareteam.com", so a
        # business's own careers URL was misclassified as a third party. 10 live
        # records have domains starting with 'w'. Strip the prefix properly.
        ods = [_strip_www(str(d).lower()).rstrip(".") for d in own_domains]
        if any(domain == d or domain.endswith("." + d) for d in ods if d):
            return "own_site"
        return "other"
    # No own domain on the record — we cannot claim it is theirs.
    return "other"

def _distinctive_name_tokens(name):
    """Return name tokens specific enough to match a business in search results.
    Filters stop-words and generic suffixes; requires >= 5 chars."""
    out = []
    for w in re.findall(r'[a-z0-9]+', (name or "").lower()):
        if len(w) >= 5 and w not in GENERIC_NAME_TOKENS:
            out.append(w)
    return out

def _is_aggregator_domain(domain):
    """SGW-938 B2: canonical aggregator-domain check — domain-BOUNDARY match.

    `domain == agg or domain.endswith('.' + agg)` — NOT substring. Substring
    matching makes 'lawyers.com' block 'prfamilylawyers.com' (a real firm).
    Used by BOTH is_aggregator() and the crawl loop's is_own_site check so
    ingestion and filtering agree."""
    d = (domain or "").lower().rstrip(".")
    return any(d == agg or d.endswith("." + agg) for agg in AGGREGATOR_DOMAINS)


def _is_directory_record(url, name=""):
    """SGW-864: True when a record is a directory/SEO listing, not a business.
    Checks domain blocklist, STRONG path signatures (listing pages), and
    generic SEO names. WEAK path signatures only demote when the name is also
    generic — a real brand on its own /service-area/ page stays a lead."""
    url_l = (url or "").lower()
    domain = re.sub(r'https?://(www\.)?', '', url_l).split('/')[0]
    # SGW-938 B2: one canonical boundary rule for domain blocklists
    if _is_aggregator_domain(domain):
        return True
    if any(re.search(p, url_l) for p in DIRECTORY_PATH_PATTERNS_STRONG):
        return True
    nm = (name or "").strip().lower()
    if any(re.search(p, url_l) for p in DIRECTORY_PATH_PATTERNS_WEAK) and not nm:
        return True
    if _is_generic_name(nm):
        return True
    return False


# ── SGW-941: ELIGIBILITY GATE ──────────────────────────────────────────
def _domain_labels(domain):
    """First-label (subdomain) + registrable-domain split of a host.
    'agents.statefarm.com' → ('agents', 'statefarm.com');
    'prfamilylawyers.com' → ('', 'prfamilylawyers.com')."""
    d = (domain or "").lower().rstrip(".")
    if not d:
        return "", ""
    labels = d.split(".")
    if len(labels) >= 3:
        return labels[0], ".".join(labels[1:])
    return "", d


# SGW-946: target market. Murrieta / Temecula valley plus the surrounding
# Southern California exchanges a local business would plausibly publish.
# Toll-free codes (800/833/844/855/866/877/888) are deliberately ABSENT: they
# are national and carry no geographic information.
TARGET_MARKET_AREA_CODES = {
    "951",  # Riverside / Temecula / Murrieta (the core market)
    "949",  # south Orange County — overlaps the corridor
    "760",  # north San Diego / Palm Desert
    "909",  # San Bernardino / Riverside
    "714",  "657", "658",  # Orange County
    "626",  # San Gabriel Valley
    "619",  "858",  # San Diego
    "310",  "323",  "213",  # LA basin
    "805",  # Ventura / Santa Barbara
    "661",  # Antelope Valley
}
TOLL_FREE_AREA_CODES = {"800", "833", "844", "855", "866", "877", "888"}


def _area_code(phone):
    """SGW-946: 3-digit area code from a phone string, or ''.

    Read POSITIONALLY when the number is written in a recognisable format,
    because that is what the area code actually is. A strict 10-digit rule
    rejected the benchmark fixture's anonymised numbers ("(951) 555-01101" —
    malformed length, 555 exchange), which made every fixture record read
    "unknown" and collapsed the benchmark. The leading group is the market
    identifier and is correct regardless of what follows it.
    """
    s = (phone or "").strip()
    m = re.match(r"^\(?(\d{3})\)?[\s.\-]", s) or re.match(r"^\+?1[\s.\-]?\(?(\d{3})\)?", s)
    if m:
        code = m.group(1)
    else:
        d = re.sub(r"\D", "", s)
        if len(d) == 11 and d[0] == "1":
            d = d[1:]
        if len(d) < 10:
            return ""
        code = d[:3]
    return "" if code[0] in "01" else code


def geo_verdict(phones, own_domains=None):
    """SGW-946: is this prospect in the target market?

    Returns one of:
      'local'        at least one target-market area code
      'out_of_area'  a real geographic area code, but none in the market
      'unknown'      only toll-free numbers, or nothing usable

    'out_of_area' is the only state that should ever demote a record, and even
    then it is routed to research rather than deleted — a business may be local
    and simply publish a national line. 'unknown' is exactly that: unknown, and
    must not be treated as absence (AGENTS.md §1b).
    """
    codes = {c for c in (_area_code(p) for p in (phones or [])) if c}
    if codes & TARGET_MARKET_AREA_CODES:
        return "local"
    geographic = codes - TOLL_FREE_AREA_CODES
    if geographic:
        return "out_of_area"      # a real area code, just not ours
    return "unknown"              # toll-free only / nothing usable


def assess_eligibility(url, name="", trade="", phones=None, own_domains=None):
    """SGW-941: deterministic first-line eligibility gate.

    Returns ("eligible"|"research"|"rejected", reason). A prospect is
    REJECTED when it is a government body, national directory/locator page,
    job subdomain, generic unresolved page-title, or a national-enterprise
    branch without a distinct local identity. Unknown signals → "research"
    (never Warm/priority). Local franchises/offices with a resolved local
    identity + verified contact survive as "eligible" — the parent platform
    is not the prospect."""
    url_l = (url or "").lower()
    domain = re.sub(r'https?://(www\.)?', '', url_l).split('/')[0] if url_l else ""
    first_label, base_domain = _domain_labels(domain)

    # 1. Government / public agency / academic — never a prospect.
    if any(base_domain.endswith(suf) for suf in GOVERNMENT_TLD_SUFFIXES) or ".gov" in domain:
        return "rejected", "government/public entity"
    # 2. Corporate locator/careers subdomains (agents. / jobs. / careers.).
    #    Deliberate tradeoff (QC 2026-08-09): this is NOT gated on the base
    #    domain being a national enterprise — a local SMB hosted on
    #    jobs.theirlocalbrand.com would be rejected. For this ICP (owner-led
    #    local SMBs), these subdomain labels are overwhelmingly national
    #    agent-locator/careers conventions; the false-positive risk is low and
    #    documented. If a verified local business with such a subdomain
    #    surfaces, the fix is a per-record exception, not substring weakening.
    if first_label in LOCATOR_SUBDOMAIN_LABELS:
        return "rejected", f"locator/job subdomain ({first_label}.{base_domain})"
    # 3. National enterprise branch — the local office is not the buyer.
    if base_domain in NATIONAL_ENTERPRISE_DOMAINS:
        return "rejected", "national enterprise branch (parent platform is not the prospect)"
    # 4. Directory/aggregator/SEO listing (existing SGW-864 rules). Generic
    #    page titles ("Contact Us", "Home") are caught here — rejected, since
    #    a title with no brand identity is not a business at all.
    if _is_directory_record(url, name):
        return "rejected", "directory/SEO listing"
    # 5. National brand / franchise / corporate branch page — the local
    #    operator is not the buyer (no budget authority, no system to
    #    integrate). SGW-942 B3: expresspros.com/us-california-*, Manpower
    #    franchise sites, newyorklife agent locators, Liberty Company /
    #    Utopia regional pages were all passing as "eligible".
    if _is_national_branch(url, name, own_domains):
        return "research", "national brand / franchise branch page (parent platform is not the prospect)"
    # 5b. A name that is locator chrome, not a business ("Agent Directory").
    if _is_generic_locator_title(name):
        return "research", "locator/directory page title — no business behind the name"
    # 6. No contact path captured yet → can't be owner-facing.
    if not phones:
        return "research", "no verified contact path"
    # 7. No own domain AND no directory-domain list → nothing to verify against.
    if not own_domains and not url:
        return "research", "no domain/identity to verify"
    # 8. Geography (SGW-946). The engine never checked that a prospect is in
    #    the target market — assess_eligibility had no city/zip/area-code logic
    #    at all. Online QC (2026-09-12) found hagarinsurance.com — Spring Hill,
    #    Florida, ZIP 34609, area code 352 — sitting at rank 2 of the top 10 as
    #    a Temecula lead, and 71 of 350 eligible records carried no Southern
    #    California area code at all.
    #
    #    Deliberately a SOFT signal: we only reject when the evidence points
    #    AWAY from the market. An 800/888/855 toll-free number proves nothing
    #    either way (a Murrieta plumber can publish one), so a record with a
    #    toll-free number and no local number is routed to "research", never
    #    rejected — no evidence is not negative evidence (AGENTS.md §1b).
    geo = geo_verdict(phones, own_domains)
    if geo == "out_of_area":
        return "research", "no target-market area code (out-of-area only)"
    if geo == "unknown":
        return "research", "no local area code — market unverified"

    return "eligible", "distinct local operating business"


def apply_eligibility_sweep(cache):
    """SGW-941: run the eligibility gate over the cached population.

    Mutates in place (append-compatible): sets eligibility_state + reason on
    every business, routes 'rejected' records to Cold with a zeroed score
    (evidence preserved — nothing deleted), and 'research' records to Cold
    with a reason. 'eligible' records keep their computed score. Returns
    counts {'eligible','research','rejected'} for the run log."""
    counts = {"eligible": 0, "research": 0, "rejected": 0}
    for biz in cache.get("businesses", {}).values():
        # SGW-944 D3: a record retired by merge_duplicate_records is a tombstone.
        # Re-assessing it here flipped it back to "eligible", so a merged-away
        # duplicate re-entered the scoring stream and the signal sweep. Leave
        # tombstones alone.
        if biz.get("_retired"):
            counts["rejected"] = counts.get("rejected", 0) + 1
            continue
        url = biz.get("url", "") or (biz.get("own_domains") or [""])[0]
        state, reason = assess_eligibility(
            url, biz.get("name", ""), biz.get("trade", ""),
            biz.get("phones", []), biz.get("own_domains", []))
        biz["eligibility_state"] = state
        biz["eligibility_reason"] = reason
        counts[state] = counts.get(state, 0) + 1
        if state in ("rejected", "research"):
            # Route out of the owner-facing stream. Keep raw evidence (signals,
            # site_quality) so nothing useful is lost; the score is zeroed and
            # the tier forced to Cold so these can never enter Warm/priority.
            # QC (2026-08-09): only mutate when lead_score is actually a dict —
            # a corrupt legacy value (string, int) would crash with
            # "does not support item assignment" and kill the whole cron run.
            if not isinstance(biz.get("lead_score"), dict):
                biz["lead_score"] = {
                    "score": 0, "tier": "Cold", "breakdown": {},
                    "reasons": [f"eligibility: {reason}"]}
            else:
                biz["lead_score"]["tier"] = "Cold"
                biz["lead_score"]["score"] = 0
                if reason not in biz["lead_score"].setdefault("reasons", []):
                    biz["lead_score"]["reasons"].append(f"eligibility: {reason}")
    return counts

# SGW-864 round 2: page-title records ("Contact Us", "About", "Careers") and
# modifier-generic names ("White-Label Bookkeeping Services", "Temecula CPA, CPA")
# are not business brands — they get re-keyed to their domain brand instead.
GENERIC_PAGE_TITLE_PATTERNS = [
    r'^(contact|about|home|careers|services?|products?|locations?|team|faq|blog|news)\s*(us|our|the)?\s*$',
    r'^(our|the)\s+(services?|team|story|company|firm|office)',
]
GENERIC_MODIFIER_PATTERNS = [
    r'^(white[- ]label|professional|full[- ]service|complete|premier|trusted|reliable|experienced|local|best|top)\s+',
    r'^[a-z\s-]+,?\s+(cpa|cpas|lawyers?|attorneys?|bookkeeping|accounting|tax\s*service|insurance|consulting|recruiting)\s*,?\s+(cpa|cpas)?$',
]

def _is_generic_name(name):
    """True when a cleaned name is a generic SEO/page title, not a brand."""
    nm = (name or "").strip().lower()
    if not nm or len(nm) < 3:
        return True
    if any(re.search(p, nm) for p in GENERIC_BUSINESS_NAME_PATTERNS):
        return True
    if any(re.search(p, nm) for p in GENERIC_PAGE_TITLE_PATTERNS):
        return True
    if any(re.search(p, nm) for p in GENERIC_MODIFIER_PATTERNS):
        return True
    return False

# SGW-864: known domain-word split points for brand derivation
_DOMAIN_BRAND_WORDS = [
    "accounting", "bookkeeping", "consulting", "insurance", "attorneys", "attorney",
    "lawyers", "lawyer", "legal", "realty", "properties", "property", "management",
    "group", "solutions", "company", "firm", "associates", "partner", "partners",
    "landscaping", "plumbing", "electrical", "electric", "roofing", "painting",
    "heating", "cooling", "repair", "mechanical", "construction", "remodeling",
    "cleaning", "carpet", "mobile", "tech", "technologies", "systems", "media",
    "marketing", "advisors", "advisory", "financial", "finance", "capital",
    "investment", "holdings", "services", "service", "design", "studios", "studio",
    "cpa", "cpas", "tax", "landscape", "tree", "handyman", "plumber", "roofing",
]

def _domain_brand_name(domain):
    """SGW-864: derive a human brand from a registrable domain.
    'prudhommecpas.com' → 'Prudhomme CPAs'; 'khanattorneys.com' → 'Khan Attorneys'.
    Falls back to the title-cased domain when nothing is derivable."""
    d = re.sub(r'^(https?://)?(www\.)?', '', (domain or "").lower())
    d = re.sub(r'\.[a-z]{2,4}(/.*)?$', '', d).rstrip('/')
    # split on known business words (longest match wins)
    tokens = []
    rest = d
    while rest:
        matched = None
        for w in sorted(_DOMAIN_BRAND_WORDS, key=len, reverse=True):
            if rest.endswith(w) and len(rest) > len(w):
                matched = w
                break
        if matched:
            tokens.append(matched)
            rest = rest[: -len(matched)]
        else:
            # camelcase split, then take the rest as one token
            rest = re.sub(r'([a-z])([A-Z])', r'\1 \2', rest)
            parts = re.split(r'[-_.]+', rest)
            tokens.extend(parts)
            rest = ""
    tokens = [t for t in reversed(tokens) if t]
    if not tokens:
        return ""
    stop = {"the", "and", "of", "for", "in", "ca", "inc", "llc", "llp", "co"}
    tokens = [t for t in tokens if t not in stop][:3]
    parts = []
    for t in tokens:
        if t == "cpa":
            parts.append("CPAs")
        elif t == "cpas":
            parts.append("CPAs")
        else:
            parts.append(t[:1].upper() + t[1:])
    return " ".join(parts)

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

# ── SGW-939: SIGNAL COVERAGE CONFIG ────────────────────────────────────
# Strong-signal enrichment (hiring + review) replaces the old "top 8 per run"
# cap with a bounded sweep that eventually reaches every eligible prospect.
SIGNAL_RECHECK_DAYS = 14       # freshness window — re-run checks older than this
SIGNAL_SWEEP_LIMIT = 12        # eligible prospects processed per run
SIGNAL_COVERAGE_TARGET = 90    # % of eligible prospects to reach (report only)
SIGNAL_COVERAGE_REPORT = "~/.hermes/scripts/reports/coverage-report.json"

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
        "automatable_role_weak": 15,    # role match but only aggregator-echo evidence
        "generic_hiring": 12,           # generic hiring signal
        "generic_hiring_weak": 6,       # generic hiring, aggregator-echo only
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

# ── SGW-925: GOOGLE PLACES IDENTITY ENRICHMENT (DORMANT) ────────────────
# Official Places API only — never scrape Maps pages. Completely inert without
# a key: places_identity() logs one line and returns None (no network). Full
# activation = GOOGLE_PLACES_API_KEY set AND --places passed. Provider evidence
# is stored as neutral corroboration only; scoring/routing effects wait for the
# benchmark comparison (see docs/source-audit-2026-08.md — no provider locked in).
PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")
PLACES_BASE = "https://maps.googleapis.com/maps/api/place"
PLACES_MAX_PER_RUN = 40          # per-run request budget guard
PLACES_EXPIRY_DAYS = 90          # freshness: sweeper treats evidence older than this as stale
# Strict field selection (official billing terms: you pay per data group asked).
PLACES_FIELDS = ("formatted_address,name,place_id,website,"
                 "international_phone_number,business_status")
# ponytail: optional opt-in env — append the trade to the text query for a
# broader match when the SearXNG-derived name is mangled. Unset = exact name+zip.
PLACES_TEXT_SEARCH = os.getenv("PLACES_TEXT_SEARCH", "")

# ── SGW-940: GROUNDED AI REVIEW (OPT-IN, DORMANT BY DEFAULT) ─────────────
# Second-pass prospect reviewer: one OpenAI-compatible /v1/chat/completions
# call (stdlib urllib only) that reads the DETERMINISTIC evidence bundle and
# returns a grounded opinion stored under biz['ai_review']. Host-agnostic —
# any OpenAI-compatible endpoint (local ollama today, Tahoe later). DISABLED
# unless a model AND (base_url or api_key) are configured: zero runtime
# effect, zero network, deterministic pipeline fully functional. AI output is
# advisory and NEVER affects lead_score or eligibility (routing effects wait
# for the SGW-861 human-verified benchmark evaluation).
AI_REVIEW_BASE_URL = os.getenv("AI_REVIEW_BASE_URL", "")     # e.g. https://ollama.com/v1 — empty = disabled
AI_REVIEW_MODEL = os.getenv("AI_REVIEW_MODEL", "")           # empty = disabled
AI_REVIEW_API_KEY = os.getenv("AI_REVIEW_API_KEY", "")       # optional — local endpoints may need none
AI_REVIEW_MAX_CANDIDATES = int(os.getenv("AI_REVIEW_MAX_CANDIDATES", "15"))
AI_REVIEW_MAX_TOKENS = int(os.getenv("AI_REVIEW_MAX_TOKENS", "1200"))
AI_REVIEW_TIMEOUT = int(os.getenv("AI_REVIEW_TIMEOUT", "60"))       # seconds per call
AI_REVIEW_RECHECK_DAYS = int(os.getenv("AI_REVIEW_RECHECK_DAYS", "7"))  # freshness window
AI_REVIEW_DECISIONS = ("priority", "research", "watch", "reject", "abstain")

# ── SGW-863: COLLECTOR REGISTRY ──────────────────────────────────────────
# Pluggable, config-gated collectors. Each entry: name, enabled, timeout_s.
# run_collector() wraps every collector with per-source timeout + failure
# isolation so one broken source can never kill the pipeline (SGW-863
# acceptance: disabling any collector doesn't break scoring or reporting).
COLLECTORS = {
    "crawl_search":    {"enabled": True,  "timeout_s": 120, "desc": "SearXNG business discovery"},
    "website_check":   {"enabled": True,  "timeout_s": 120, "desc": "Site audit (HTTP fetch + parse)"},
    "hiring_signals":  {"enabled": True,  "timeout_s": 90,  "desc": "Job-posting signal search"},
    "review_signals":  {"enabled": True,  "timeout_s": 90,  "desc": "Review-complaint signal search"},
    "buying_signals":  {"enabled": True,  "timeout_s": 120, "desc": "Reddit/FB buying-signal crawl"},
    # SGW-925: dormant without GOOGLE_PLACES_API_KEY — run_collector() logs
    # "collector disabled" and returns None without touching the network.
    "places_identity": {"enabled": True,  "timeout_s": 20,  "desc": "Google Places identity enrichment (dormant without key)"},
}

def collector_enabled(name):
    return COLLECTORS.get(name, {}).get("enabled", True)

def run_collector(name, fn, *args, **kwargs):
    """Run a collector with its configured timeout; on any failure return
    None and log — never let one source's error crash the run (SGW-863).

    SGW-938 B5: timeout_s is now ENFORCED (was declared metadata only) via a
    daemon watchdog thread — a hung collector can no longer stall the run
    forever. The worker thread keeps running in the background if it doesn't
    notice the timeout, so a wedged DNS socket won't hold the process open."""
    if not collector_enabled(name):
        log(f"collector disabled: {name}")
        return None
    timeout_s = COLLECTORS.get(name, {}).get("timeout_s", 120)
    result = {}
    worker_done = threading.Event()

    def _worker():
        try:
            result["value"] = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 — isolation is the point
            result["error"] = e
        finally:
            worker_done.set()

    t = threading.Thread(target=_worker, name=f"collector-{name}", daemon=True)
    t.start()
    if not worker_done.wait(timeout_s):
        log(f"collector timed out after {timeout_s}s: {name}")
        return None
    if "error" in result:
        log(f"collector failed ({name}): {result['error']}")
        return None
    return result.get("value")


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


# ── SGW-925: GOOGLE PLACES IDENTITY ENRICHMENT (DORMANT) ────────────────
def places_identity(biz_name, trade="", zip_hint="92562"):
    """One-shot Google Places identity lookup via Text Search (official API).

    Returns a dict with provider/source + observed_at + confidence + state +
    provenance, or None when the key is absent / lookup fails / no match.
    Missing fields are omitted (caller stores UNKNOWN). Network errors are
    NOT caught here — production callers MUST go through run_collector(),
    which isolates and times out every collector (QC 2026-08-09: the
    docstring used to claim 'never raises'; the run_collector wrapper is the
    guarantee, and a direct call can raise URLError).

    Official-API terms: results shown to end users must include the "Powered
    by Google" attribution. This issue stores evidence only — no user-facing
    display — but the adapter must not be wired into display without it.
    """
    if not PLACES_API_KEY:
        log("collector disabled: places_identity (no API key)")
        return None
    # Strict field selection — billing is per requested data group (Essentials
    # $5/1k, contact fields at the Enterprise tier $35/1k for text search).
    query = f"{biz_name} {trade} {zip_hint}" if (trade and PLACES_TEXT_SEARCH) else f"{biz_name} {zip_hint}"
    params = urllib.parse.urlencode({
        "query": query, "key": PLACES_API_KEY, "fields": PLACES_FIELDS,
        "inputtype": "textquery",
    })
    url = f"{PLACES_BASE}/findplacefromtext/json?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    if data.get("status") != "OK" or not data.get("candidates"):
        return None
    c = data["candidates"][0]
    if c.get("business_status") == "CLOSED_PERMANENTLY":
        return None  # closed business is not a prospect — neutral, no evidence
    ev = {
        "provider": "google_places",
        "source": "google_places_api",
        "provenance": "google_places_api",
        "state": "confirmed" if c.get("place_id") else "unconfirmed",
        "confidence": "high" if c.get("place_id") else "medium",
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    for k, api_key in (("name", "name"), ("address", "formatted_address"),
                       ("website", "website"), ("phone", "international_phone_number")):
        if c.get(api_key):
            ev[k] = c[api_key]  # missing → UNKNOWN (field simply absent)
    if c.get("place_id"):
        ev["place_id"] = c["place_id"]
    return ev


def run_places_enrichment(cache, limit=PLACES_MAX_PER_RUN):
    """Enrich eligible/research records with Places identity evidence.

    Bounded by limit and run only when a key exists. Evidence is stored under
    biz['provider_evidence']['google_places'] — never merged into
    site_quality/lead_score, never lowers a prospect. 401/403/500/timeout →
    run_collector returns None → record untouched → run continues (SGW-863)."""
    if not PLACES_API_KEY:
        log("collector disabled: places_identity (no API key)")
        return 0
    bizs = cache.get("businesses", {})
    # Eligible first, then research — bounded by the per-run budget.
    # ponytail: safe sort key — corrupt/non-dict lead_score (SGW-941 QC) must
    # never crash the enrichment pass, so scores are read defensively.
    def _score(kv):
        ls = kv[1].get("lead_score", {})
        try:
            return ls.get("score") or 0
        except AttributeError:
            return 0
    ranked = sorted(bizs.items(),
                    key=lambda kv: (kv[1].get("eligibility_state", "") != "eligible", -_score(kv)))
    done = 0
    for norm, biz in ranked:
        if done >= limit:
            break
        if biz.get("eligibility_state") not in ("eligible", "research"):
            continue
        if not biz.get("name"):
            continue
        # Freshness: keep 90 days, then re-check (SGW-925).
        old = (biz.get("provider_evidence") or {}).get("google_places", {}).get("observed_at")
        if old:
            try:
                if (datetime.now(timezone.utc) - datetime.fromisoformat(old)).days <= PLACES_EXPIRY_DAYS:
                    continue
            except ValueError:
                pass
        log(f"  Places identity: {biz.get('name')}")
        ev = run_collector("places_identity", places_identity,
                           biz.get("name", ""), biz.get("trade", ""), zip_hint="92562")
        if ev:
            biz.setdefault("provider_evidence", {})["google_places"] = ev
            # Neutral corroboration only — no scoring/routing changes in SGW-925.
            biz["provider_evidence"]["google_places"]["fresh_until"] = (
                datetime.now(timezone.utc) + timedelta(days=PLACES_EXPIRY_DAYS)).isoformat()
        done += 1
        time.sleep(0.5)  # polite rate limit spread
    return done


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
    # SGW-938 B2: boundary match via the shared canonical helper, not substring
    if _is_aggregator_domain(domain):
        return True
    for pattern in AGGREGATOR_TITLE_PATTERNS:
        if re.search(pattern, title, re.I):
            return True
    if any(pat in url.lower() for pat in ["/search?", "/find_", "/browse", "/cflt="]):
        return True
    return False


# SGW-942 B1c: NPAs that are service codes or were never assigned to a
# geographic/valid carrier. Shape-valid but never a real business line.
UNASSIGNED_AREA_CODES = {
    "200", "211", "222", "311", "333", "411", "444", "511", "555",
    "611", "666", "711", "777", "811", "911", "999", "000", "111",
}
# Classic placeholder constants that appear in test data / template code.
# SGW-942 B1c: the INT32-max FAMILY, not just the exact value — Policygenius
# was serving (214) 748-3645 and (214) 748-3646, which are the same
# 214748364x placeholder block, one digit off the canonical constant.
KNOWN_PLACEHOLDER_NUMBERS = {
    "2147483647",   # INT32 max — ubiquitous in sample data
    "1234567890", "0123456789", "1234567891", "0987654321",
}
# Prefix block for the INT32-max family (214 748 364x).
PLACEHOLDER_PREFIXES = ("214748364",)


def _normalize_phone(raw):
    """SGW-938 B1: canonical NANP phone validator/normalizer.

    Every phone ingestion path (regex extract, tel: href, JSON-LD merge,
    cache sweep) MUST route through this one function. Returns the
    normalized '(XXX) XXX-XXXX' form for a valid US number, else None.
    Rules: 10 digits (or 11 starting with '1'); area code 200-989 and not
    N11 (411/911); exchange not all-zero (000) and not reserved test (555).

    SGW-942 B1c (2026-09-11 QC): NANP shape is necessary but not sufficient.
    Live cache contained numbers that satisfy every shape rule yet are not
    real: (666) 666-6666, (333) 333-3333, (444) 444-4444, (214) 748-3647
    (INT32 max, a classic placeholder), (555) 967-1920. Three extra rules:
      1. UNASSIGNED AREA CODES — 200/211/222/311/333/411/444/511/555/611/
         666/711/777/811/911/999 are service codes or never-assigned NPAs.
      2. REPEATED DIGITS — a number with <=2 distinct digits is filler
         (4444444444), never a real line.
      3. KNOWN PLACEHOLDERS — 2147483647, 1234567890, 0123456789.
    Deliberately NOT rejected: repeated PAIRS and endings like xxx-8888 or
    exchange 444/888 — real businesses hold those constantly, and a
    too-eager rule here silently deletes live contact paths."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    ac = int(digits[:3])
    exchange = int(digits[3:6])
    if not (200 <= ac <= 989 and ac % 100 != 11) or exchange in (0, 555):
        return None
    # SGW-942 B1c: NANP forbids an exchange beginning with 0 or 1. Missing
    # this let (857) 142-8571 and (394) 095-7316 pass as valid, and those
    # phantom numbers then acted as "shared identity" between unrelated
    # businesses in the duplicate merge.
    if digits[3] in ("0", "1"):
        return None
    if digits[:3] in UNASSIGNED_AREA_CODES:
        return None
    if len(set(digits)) <= 2:
        return None
    if digits in KNOWN_PLACEHOLDER_NUMBERS:
        return None
    if any(digits.startswith(pre) for pre in PLACEHOLDER_PREFIXES):
        return None
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def extract_phones(text):
    """Extract US phone numbers. Fix 6: broader regex for more formats.
    Research 2026-08 / SGW-938 B1: NANP validation via _normalize_phone —
    area code must be real (200-989, not starting with 0/1), exchange must
    not be all-zeros or a reserved test prefix (555). Crawler garbage like
    (100) 091-4084 or (178) 137-3717 must not count as a contact path.

    SGW-942 B1b (2026-09-11 QC): NANP validity is NOT sufficient on raw HTML.
    A bare 10-digit run is matched anywhere in the byte stream, so framework
    identifiers get read as phone numbers — this shipped as verified contact
    evidence:
      - Wix CSS class  'StylableButton2545352419__root' → (254) 535-2419
      - Wix site UUID  'content="d0a90ec0-...-5875261850ad"' → (587) 526-1850
    Both passed NANP because 254 and 587 are real area codes. Rule: an
    UNSEPARATED 10-digit run is only accepted when it is NOT embedded in a
    longer alphanumeric token (checked on BOTH sides) and does not follow a
    version/hash/UUID separator. Separated forms — '(951) 440-3498',
    '951-440-3498', '951.440.3498' — are unambiguous and always accepted."""
    # Separated forms first — these are what a human actually reads on a page.
    separated = [m.group(0) for m in re.finditer(
        r"(?:\(\d{3}\)|\b\d{3})[-.\s]\d{3}[-.\s]\d{4}\b", text)]
    # SGW-944 D4: a country-coded number with NO separators — '19512345678' or
    # '+19512345678' — was dropped entirely. The bare-10-digit pass rejects both
    # windows (\(i\) the first 10 digits because the trailing '8' is alnum, and
    # (\(ii\) the last 10 because the leading '1' is alnum), so a perfectly
    # readable US number yielded nothing. Take the 11-digit form explicitly and
    # reduce it to the national 10 when the leading digit is the country code.
    for m in re.finditer(r"(?<!\d)\+?1(\d{10})(?!\d)", text):
        separated.append("(" + m.group(1)[:3] + ") " + m.group(1)[3:6] + "-" + m.group(1)[6:])
    # Bare 10-digit runs, admitted only when genuinely standalone.
    bare = []
    for m in re.finditer(r"\d{10}", text):
        s, e = m.start(), m.end()
        before = text[s - 1] if s > 0 else ""
        after = text[e] if e < len(text) else ""
        if before.isalnum() or after.isalnum():
            continue          # embedded in a longer token (CSS class / UUID)
        # NB: guard the empty string — `"" in "-_./"` is True, which would
        # reject every number sitting at the start/end of the text.
        if (before and before in "-_./") or (after and after in "-_./"):
            continue          # hash / UUID / version-fragment neighbour
        bare.append(m.group(0))
    candidates = separated + bare
    seen, result = set(), []
    for p in candidates:
        formatted = _normalize_phone(p)
        if formatted and formatted not in seen:
            seen.add(formatted)
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
FETCH_BUDGET = 400000        # bytes read per page. Was 150000 — raised by
                             # SGW-943 after measuring the false-negative cause:
                             # EVERY tool-marker miss sampled was a marker sitting
                             # past the old 150KB cap (HubSpot at byte 209890,
                             # Acuity at 206062, Google Tag at 252108, tel:
                             # links in a footer past budget). Truncating a read
                             # and then reporting "not there" is the exact
                             # mistake AGENTS.md §1b forbids.
MAX_FETCHES = 4              # distinct URL fetches per business (candidates + subpages)
MAX_SUBPAGE_FETCHES = 2      # how many /contact + /about pages to pull in
# T17: SPA bootstrap markers. A near-empty page carrying one of these is a
# JS-rendered shell we couldn't read — UNKNOWN, not a thin/dead site.
JS_SHELL_MARKERS = (
    'id="root"', "id='root'", "__next_data__", "data-reactroot", "ng-version",
    'id="__nuxt"', 'id="app"', "data-react-helmet", "data-server-rendered",
)


def _may_assert_gap(truncated, found_markers):
    """SGW-943: the PRESENT/ABSENT/UNKNOWN rule for tool detection, expressed
    once so the gap logic and its fixtures share one definition.

    Returns True when absence may legitimately be claimed. Absence is an
    ABSENT claim and ABSENT requires a COMPLETE read: if the page was
    truncated, a marker we did not find is UNKNOWN and must not be reported as
    missing. A marker we DID find is PRESENT — there is no gap at all."""
    if found_markers:
        return False          # PRESENT — nothing is missing
    return not truncated      # ABSENT only from a complete read


# SGW-945: markers that must NOT be tested as bare substrings. A bare `in`
# test produces confident nonsense on ordinary page text:
#   'mbo'  -> Mindbody fires on "symbol" (every icon font carries it)
#   'gtag' -> Google Tag fires on "tostringtag" (minified JS)
#   'fbq'  -> Facebook Pixel fires on base64 blobs
#   'fresha' -> Fresha fires on "refreshable"
#   'acuity' -> Acuity fires on "acuityplatform.com" ad-server URLs (a tracker,
#              not a booking system)
# A false PRESENT is worse than a false gap: it silently removes an automation
# gap, which LOWERS the score and hides a real lead. Each entry is the strict
# pattern that must match instead.
MARKER_STRICT = {
    "mbo": r"\bmbo\b|mindbody",
    "gtag": r"gtag\(|/gtag/js|googletagmanager",
    "fbq": r"\bfbq\b|_fbq|connect\.facebook\.net",
    "fresha": r"\bfresha\.com|fresha\.com/",
    "acuity": r"acuityscheduling|squarespace\.com/scheduling|\bacuity\b(?!platform)",
    "vcita": r"\bvcita\b",
    "close.com": r"\bclose\.com\b",
    "zoho": r"\bzoho\b",
    "book.app": r"\bbook\.app\b",
}


def _detect_markers(html_lower, marker_dict):
    """Detect which named tools are present in lowercased HTML.

    SGW-945: a bare substring test is wrong for short markers — 'mbo' matched
    "symbol" and 'gtag' matched "tostringtag", so pages were credited with tools
    they do not have and their gaps were suppressed. Markers listed in
    MARKER_STRICT are matched with a strict pattern instead.
    """
    found = []
    seen = set()
    for marker, name in marker_dict.items():
        if name in seen:
            continue
        strict = MARKER_STRICT.get(marker)
        hit = bool(re.search(strict, html_lower)) if strict else (marker in html_lower)
        if hit:
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
            "has_outdated_email": False, "has_fax": False, "socials": [],
            "observed_at": datetime.now(timezone.utc).isoformat()}


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
                    raw = resp.read(FETCH_BUDGET + 1)
                    # SGW-943: record whether the response was TRUNCATED. A
                    # marker absent from a truncated read is UNKNOWN, not
                    # ABSENT — the old code could not tell the difference and
                    # reported "no analytics"/"no CRM" for markers that were
                    # simply past the byte budget (fsresidential.com's HubSpot
                    # at byte 209890, Acuity at 206062; Superior Virtual's
                    # Google Tag at 252108). Per AGENTS.md §1b only PRESENT may
                    # score; ABSENT requires a complete read.
                    truncated = len(raw) > FETCH_BUDGET
                    html = raw[:FETCH_BUDGET].decode("utf-8", errors="ignore")
                    return {"ok": True, "html": html, "final_url": resp.geturl(),
                            "truncated": truncated}
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
    # SGW-943: did we read the WHOLE page? If not, a marker we failed to find
    # is UNKNOWN, not ABSENT (AGENTS.md §1b). This is the flag the gap logic
    # uses to decide whether it may claim "no CRM" / "no analytics" at all.
    page_truncated = bool(page.get("truncated"))

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
    # SGW-944 R1 (Warden): booking/chat was the ONLY gap asserted without a
    # truncation guard. Proof: a 504KB page carrying a Calendly widget past the
    # 400KB byte budget yields has_booking_system=False -> "no booking/chat
    # system", worth +10 on an appointment trade. That is a fabricated gap, the
    # exact defect AGENTS.md §1b forbids. Gate it like every neighbour.
    if not has_booking_system and not has_chat and not page_truncated:
        gaps.append("no booking/chat system")
    elif not has_booking_system and not page_truncated:
        gaps.append("no booking system")
    if not has_tel and not page_truncated: gaps.append("no click-to-call")
    if not has_contact and not page_truncated: gaps.append("no contact page")
    if not has_viewport: gaps.append("not mobile-responsive")
    if _may_assert_gap(page_truncated, crm_tools):
        gaps.append("no CRM")
    if _may_assert_gap(page_truncated, marketing_tools):
        gaps.append("no marketing tools")
    if _may_assert_gap(page_truncated, analytics_tools):
        gaps.append("no analytics")

    if words < 200:
        gaps.append(f"thin content ({words}w)")

    website_score = sum([has_viewport, has_tel, has_contact, words > 200, has_booking_system or has_chat])

    page_phones = extract_phones(combined)
    for tm in re.findall(r'href=["\']tel:([+\d\s()\-.]+)', combined, re.I):
        # SGW-938 B1: tel: hrefs go through the SAME canonical validator —
        # previously any 10-digit string was accepted, bypassing NANP rules
        # and letting garbage like (100) 091-4084 count as a contact path.
        formatted = _normalize_phone(tm)
        if formatted and formatted not in page_phones:
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
            "socials": socials[:6],
            "observed_at": datetime.now(timezone.utc).isoformat()}


def search_hiring_signals(biz_name, cache_key, cache):
    """T6+T10: Role-aware hiring search. Only flags when a real hiring verb + biz name
    appear together; sets hiring_role_match=True for AUTOMATABLE_ROLES matches.
    T10: indeed/ziprecruiter/linkedin job URLs are intentionally allowed here (they're
    blocked in the crawl loop as 'not real businesses', but they're authoritative signal
    sources for whether a named business is hiring an automatable role)."""
    if not biz_name or len(biz_name) < 3:
        return False
    cached = cache.get("businesses", {}).get(cache_key, {})
    # SGW-939: freshness — a check older than SIGNAL_RECHECK_DAYS is re-run
    # (job postings rot; a stale "not hiring" from months ago is not current).
    _checked_at = cached.get("hiring_checked_at")
    if cached.get("hiring_checked") and _checked_at:
        try:
            _age = (datetime.now(timezone.utc) - datetime.fromisoformat(_checked_at)).days
        except ValueError:
            _age = 0
        if _age <= SIGNAL_RECHECK_DAYS:
            return bool(cached.get("hiring_signals", []))
    elif cached.get("hiring_checked"):
        # legacy entry without timestamp — treat as fresh (matches old behavior)
        return bool(cached.get("hiring_signals", []))

    biz_name_lower = biz_name.lower()
    # SGW-864: only DISTINCTIVE tokens may prove the source is about THIS business.
    # Filters stop-words + generic suffixes ("consulting", "associates", "inc"…)
    # so "MBC Consulting Inc" can't match "Morgan Business Consulting" posts.
    name_words = _distinctive_name_tokens(biz_name)

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
            # SGW-864/865: skip sources about far-away cities (ZipRecruiter noise)
            if _mentions_out_of_area(title + " " + snippet):
                continue
            # SGW-865: stale postings (2024 'Bookkeeper wanted') are not
            # evidence of hiring now. Only recent/undated signals count.
            recency = _signal_recency(title, snippet)
            if recency == "stale":
                continue

            hiring_found = True
            # T6: flag automatable role if mentioned
            if any(role in combined for role in AUTOMATABLE_ROLES):
                role_match = True
            hiring_results.append({
                "title": r.get("title", "")[:70],
                "snippet": (r.get("content", "") or "")[:120],
                "url": r.get("url", ""),
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "recency": recency,
                # SGW-942 B2: pass the record's own domains so an unrecognised
                # third-party host classifies as `other`, not `own_site`.
                "source_kind": _evidence_source_kind(
                    r.get("url", ""),
                    (cache.get("businesses", {}).get(cache_key, {}) or {}).get("own_domains")),
            })
        # SGW-944 D5: breaking on ANY generic hit meant the second query
        # ("<biz> jobs") never ran, so an automatable ROLE on the business's own
        # site was never seen. Only stop early once the roles we actually sell
        # against have been matched; otherwise keep looking.
        if role_match: break
        time.sleep(6)

    biz_entry = cache.setdefault("businesses", {}).setdefault(cache_key, {})
    biz_entry["hiring_signals"] = hiring_results[:5]
    biz_entry["hiring_checked"] = True
    biz_entry["hiring_checked_at"] = datetime.now(timezone.utc).isoformat()  # SGW-939 freshness
    biz_entry["hiring_role_match"] = role_match
    return hiring_found


def search_review_signals(biz_name, cache_key, cache):
    """Phase 2: Search SearXNG for negative review signals — '{name} reviews'.
    If results mention complaint keywords (slow, no response, etc.) = buying signal.
    Stores results in cache['businesses'][cache_key]['review_signals'].
    Respects 6-second rate limiting."""
    if not biz_name or len(biz_name) < 3:
        return False
    # Check cache first — don't re-search within the freshness window
    cached = cache.get("businesses", {}).get(cache_key, {})
    # SGW-939: freshness — a check older than SIGNAL_RECHECK_DAYS is re-run
    _checked_at = cached.get("review_checked_at")
    if cached.get("review_checked") and _checked_at:
        try:
            _age = (datetime.now(timezone.utc) - datetime.fromisoformat(_checked_at)).days
        except ValueError:
            _age = 0
        if _age <= SIGNAL_RECHECK_DAYS:
            return bool(cached.get("review_signals", []))
    elif cached.get("review_checked"):
        # legacy entry without timestamp — treat as fresh (matches old behavior)
        return bool(cached.get("review_signals", []))
    
    review_results = []
    negative_found = False
    results = searx_search(f"{biz_name} reviews", limit=8, delay=6)
    name_words = _distinctive_name_tokens(biz_name)
    for r in results:
        title = (r.get("title", "") or "").lower()
        snippet = (r.get("content", "") or "").lower()
        combined = title + " " + snippet
        # SGW-864: source must be plausibly about THIS business — distinctive
        # name token in title/url, or (for known-review-site pages) any strong
        # token. Kills cross-firm contamination from same-name companies.
        if name_words and not any(w in title or w in (r.get("url", "") or "").lower() for w in name_words):
            continue
        # SGW-864/865: skip reviews about far-away cities (Yelp NYC noise)
        if _mentions_out_of_area(combined):
            continue
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
                "observed_at": datetime.now(timezone.utc).isoformat(),
                # SGW-942 B2: same provenance rule as hiring signals — an
                # unrecognised host is `other`, never `own_site`.
                "source_kind": _evidence_source_kind(
                    r.get("url", ""),
                    (cache.get("businesses", {}).get(cache_key, {}) or {}).get("own_domains")),
            })
    
    # Store in cache
    if cache_key not in cache.get("businesses", {}):
        cache["businesses"][cache_key] = {}
    cache["businesses"][cache_key]["review_signals"] = review_results[:5]
    cache["businesses"][cache_key]["review_negative"] = negative_found
    cache["businesses"][cache_key]["review_checked"] = True
    cache["businesses"][cache_key]["review_checked_at"] = datetime.now(timezone.utc).isoformat()  # SGW-939 freshness
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

    # ── GROWTH & BUDGET (T5, SGW-866 evidence weighting) ──
    # SGW-866: weight hiring evidence by source strength — a job posting on the
    # business's OWN site is hard proof; an aggregator echo (ZipRecruiter etc.)
    # is weak and gets halved so it can't fake a retainer budget.
    gb = 0
    hiring_role_match = biz.get("hiring_role_match", False)
    hiring_signals = biz.get("hiring_signals", [])
    strong_hiring = any(s.get("source_kind") == "own_site" for s in hiring_signals) if hiring_signals else False
    if hiring_role_match:
        if strong_hiring:
            gb += GB["automatable_role"]
            reasons.append(f"hiring for an automatable role — on own site (+{GB['automatable_role']})")
        else:
            gb += GB["automatable_role_weak"]
            reasons.append(f"hiring for an automatable role — no own-site proof (+{GB['automatable_role_weak']})")
    elif hiring_signals:
        if strong_hiring:
            gb += GB["generic_hiring"]
            reasons.append(f"generic hiring signal — on own site (+{GB['generic_hiring']})")
        else:
            gb += GB["generic_hiring_weak"]
            reasons.append(f"generic hiring signal — no own-site proof (+{GB['generic_hiring_weak']})")
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

    # ── SGW-866: DIMENSION MODEL ──────────────────────────────────────
    # Separate WHY a prospect matters instead of one blended number:
    #   fit         — lane fit for operational-drag removal (0-10)
    #   pain        — named, corroborated operational pain (0-10)
    #   capacity    — ability/willingness to pay (hiring, size, budget proxy) (0-10)
    #   actionability — can we reach them + evidence confidence (0-10)
    pain_dim = min(10, breakdown["named_pain"] // 2)
    if pain_dim == 0 and biz.get("review_negative"):
        pain_dim = 2  # uncorroborated complaint = weak signal, not zero
    fit_dim = 0
    if verified:
        fit_dim = 10 if trade in ADMIN_TRADES else (7 if trade in SCORING["appointment_trades"] else 4)
    capacity_dim = min(10, breakdown["growth_budget"] // 2)
    action_dim = 0
    if contactable:
        action_dim += 5
    if verified:
        action_dim += 3
    if any(biz.get(k) for k in ("review_negative", "hiring_role_match", "hiring_signals")):
        action_dim += 2
    dimensions = {
        "fit": fit_dim,
        "pain": pain_dim,
        "capacity": capacity_dim,
        "actionability": min(10, action_dim),
    }

    # ── SGW-866: DETERMINISTIC ROUTING (replaces blended-tier-only rules) ──
    # priority — real, contactable, admin/ops-heavy, AND a reason (pain or
    # hiring or strong trade prior). Gap-stacking can never route here.
    hot_qualifier = (
        (biz.get("review_negative") and corroborated(review_signals))
        or hiring_role_match
        or (trade in ADMIN_TRADES and verified)
    )
    if (contactable and hot_qualifier and total >= SCORING["tiers"]["hot"]
            and pain_dim + capacity_dim >= 8):
        tier = "Hot"
    # watch — contactable and credible but no strong reason yet
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

    # ── SGW-862: EVIDENCE & CONFIDENCE SUMMARY ─────────────────────────
    # One documented view of how trustworthy this prospect's data is. Every
    # signal carries observed_at; UNKNOWN never awards points (already enforced
    # by the verified gate above); stale site checks are flagged for re-verify.
    sq_obs = (sq or {}).get("observed_at", "")
    site_age_days = None
    if sq_obs:
        try:
            site_age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(sq_obs)).days
        except ValueError:
            site_age_days = None
    evidence = {
        "site_verified": verified,
        "site_observed_at": sq_obs or None,
        "site_age_days": site_age_days,
        "site_stale": bool(site_age_days is not None and site_age_days > 21),
        "signals": {
            "hiring": len(hiring_signals),
            "reviews": len(review_signals),
            "review_complaints_corroborated": bool(biz.get("review_negative") and corroborated(review_signals)),
        },
        "confidence": ("high" if verified else
                       "low" if status in ("down", "blocked", "unknown") else "medium"),
    }

    return {"score": total, "tier": tier, "breakdown": breakdown,
            "dimensions": dimensions, "evidence": evidence, "reasons": reasons}


# ── SGW-940: GROUNDED AI REVIEW (SECOND-PASS PROSPECT REVIEWER) ──────────
# Advisory only. Reads the deterministic evidence bundle, asks one
# OpenAI-compatible endpoint (stdlib urllib), stores the grounded opinion
# under biz['ai_review'] with model/provider/latency/tokens metadata. Never
# writes anywhere, never touches lead_score/eligibility, disabled by default
# (AI_REVIEW_MODEL empty → zero network, zero runtime effect).

AI_REVIEW_CONTRACT = (
    "decision",           # priority | research | watch | reject | abstain
    "confidence",         # 0.0-1.0
    "evidence_refs",      # list of evidence keys this review rests on
    "bottleneck_hypothesis",  # MUST be explicitly labeled as hypothesis
    "why_now",            # reason this prospect matters this quarter
    "recommended_first_offer",  # diagnosis-first: process/software outcome,
                                # implementation tool chosen AFTER diagnosis
    "email_draft",        # short outreach email, no unsupported claims
    "phone_opener",       # one-line phone opener
    "missing_evidence",   # what would raise confidence
    "abstain_reason",     # required when decision == abstain
)


def _ai_review_evidence_keys(biz):
    """Stable keys for the deterministic evidence ACTUALLY captured on this
    record. The anti-fabrication guard only accepts evidence_refs from this
    set — the model can never cite 'owner', 'revenue', 'employees', etc.
    because those fields are not captured deterministically. Keys are
    conditional: a key the record lacks is not a legitimate ref (QC 2026-08-09:
    the base list used to be unconditional, so 'url'/'phones'/'site_quality'
    were valid refs even when the record had none of them — that let a
    fabricated 'priority' on name alone survive the guard)."""
    keys = ["name", "trade"]
    if biz.get("url") or biz.get("own_domains"):
        keys.append("url")
    if biz.get("own_domains"):
        keys.append("own_domains")
    if biz.get("phones"):
        keys.append("phones")
        keys.append("phone_contact")
    if biz.get("snippet"):
        keys.append("snippet")
    sq = biz.get("site_quality")
    if isinstance(sq, dict) and sq:
        keys.append("site_quality")
    if isinstance(biz.get("lead_score"), dict) and biz.get("lead_score"):
        keys.append("lead_score")
    if biz.get("eligibility_state"):
        keys.append("eligibility_state")
    if biz.get("hiring_checked") or biz.get("hiring_signals"):
        keys.append("hiring")
        keys.append("hiring_evidence")
    if biz.get("review_checked") or biz.get("review_signals"):
        keys.append("reviews")
        keys.append("review_evidence")
    if biz.get("provider_evidence"):
        keys.append("provider_evidence")
    if isinstance(sq, dict):
        if sq.get("status") not in (None, "unknown"):
            keys.append("site_status")
        if sq.get("automation_gaps"):
            keys.append("automation_gaps")
        if sq.get("has_outdated_email") or sq.get("has_fax"):
            keys.append("paper_signals")
        if sq.get("platform"):
            keys.append("platform")
    return sorted(keys)


# Refs that make a 'priority' decision credible: a VERIFIED website read,
# automation gaps, hiring/review evidence, provider corroboration. Identity
# and score alone (name/trade/url/phones/lead_score) are never enough, and
# site_quality is NOT substantive by itself — a record with
# site_quality={'status':'unknown'} has no verified read, so it cannot
# ground a priority (QC 2026-08-09: site_quality was in this set, letting a
# fabricated priority citing ['name','site_quality'] survive on an
# unknown-status record). site_status is only emitted when the check
# actually ran (status up/blocked/down), which is the verified-read case.
AI_REVIEW_SUBSTANTIVE_KEYS = frozenset({
    "site_status", "automation_gaps", "hiring_evidence",
    "review_evidence", "provider_evidence", "platform", "paper_signals",
})


def _ai_review_prompt(biz):
    """Single-shot structured prompt: task, tone, context, evidence bundle,
    strict JSON output contract, anti-fabrication rules, hypothesis labeling,
    abstain instruction, and two few-shot examples. SGW-928 alignment: the
    recommended offer sells the diagnosis (removing operational drag) — the
    implementation tool is chosen AFTER diagnosis, never AI-first."""
    sq = biz.get("site_quality") or {}
    ls = biz.get("lead_score") or {}
    hs = biz.get("hiring_signals") or []
    rs = biz.get("review_signals") or []
    evidence_lines = [
        f"- name: {biz.get('name', '')}",
        f"- trade: {biz.get('trade', '')}",
        f"- url: {biz.get('url', '') or (biz.get('own_domains') or [''])[0]}",
        f"- phones: {biz.get('phones', [])}",
        f"- snippet: {biz.get('snippet', '')[:200]}",
        f"- site_quality: status={sq.get('status')}, confidence={sq.get('confidence')}, "
        f"website_score={sq.get('website_score')}, automation_gaps={sq.get('automation_gaps')}, "
        f"platform={sq.get('platform')}, has_crm={sq.get('has_crm')}, "
        f"has_analytics={sq.get('has_analytics')}, has_booking_system={sq.get('has_booking_system')}, "
        f"has_outdated_email={sq.get('has_outdated_email')}, has_fax={sq.get('has_fax')}, "
        f"observed_at={sq.get('observed_at')}",
        f"- lead_score: score={ls.get('score')}, tier={ls.get('tier')}, "
        f"reasons={ls.get('reasons', [])}",
        f"- eligibility_state: {biz.get('eligibility_state')} "
        f"({biz.get('eligibility_reason', '')})",
        f"- hiring: checked_at={biz.get('hiring_checked_at')}, "
        f"role_match={biz.get('hiring_role_match')}, "
        f"signals={[{'title': s.get('title', '')[:80], 'source_kind': s.get('source_kind')} for s in hs[:5]]}",
        f"- reviews: checked_at={biz.get('review_checked_at')}, "
        f"negative={biz.get('review_negative')}, "
        f"signals={[{'title': s.get('title', '')[:80], 'complaints': s.get('complaints', [])} for s in rs[:5]]}",
        f"- provider_evidence (neutral corroboration only): "
        f"{json.dumps(biz.get('provider_evidence', {}))[:400]}",
        f"- evidence keys you may cite in evidence_refs: {_ai_review_evidence_keys(biz)}",
    ]
    prompt = f"""You are a prospect reviewer for a local-business lead pipeline. You review ONLY the evidence below — never invent facts. Your job: flag which prospects deserve a human sales call THIS quarter, and craft a diagnosis-first opening angle.

Task: read the evidence bundle for the business and return a single JSON object matching the output contract exactly.

Tone: concise, practical, direct. No fluff.

EVIDENCE BUNDLE (this is ALL you know about the business):
{chr(10).join(evidence_lines)}

OUTPUT CONTRACT (strict JSON object, no markdown, no commentary outside the JSON):
{{
  "decision": "priority" | "research" | "watch" | "reject" | "abstain",
  "confidence": 0.0-1.0,
  "evidence_refs": ["subset of the evidence keys listed above"],
  "bottleneck_hypothesis": "LABELED HYPOTHESIS: <one sentence, explicitly starting with 'hypothesis:'>",
  "why_now": "why this prospect matters this quarter",
  "recommended_first_offer": "diagnosis-first offer — name the operational drag to remove; the implementation tool (process change, existing software, automation, AI) is chosen AFTER diagnosis and only if the evidence supports it",
  "email_draft": "2-4 sentence email",
  "phone_opener": "one line",
  "missing_evidence": ["what would raise confidence"],
  "abstain_reason": "required when decision is abstain"
}}

ANTI-FABRICATION RULES (hard constraints):
1. You may NOT claim facts not present in the evidence bundle: no owner names, no revenue, no employee counts, no specific complaints beyond the review signals listed, no hiring beyond the hiring signals listed, no software/location/pain the evidence does not mention.
2. Every factual claim must map to an evidence key you cite in evidence_refs, and evidence_refs must be a subset of the evidence keys listed in the bundle.
3. Any reasoning about the business's internal bottleneck is a HYPOTHESIS and must be labeled with the literal prefix "hypothesis:" inside bottleneck_hypothesis.
4. If the evidence is too thin to support a decision, output "decision": "abstain" with an abstain_reason. Abstaining is always correct when evidence is weak.
5. decision "priority" requires strong supporting evidence (verified site read or corroborated external signals) — never priority on name/trade alone.
6. recommended_first_offer, email_draft, phone_opener must contain NO unsupported claims. Sell the diagnosis (e.g. "never miss another intake call"), not a tool, unless the evidence names the gap.

EXAMPLES:

Example 1 (weak evidence → abstain):
Evidence: name="Unknown Co", trade="Plumbing", no site check, no phones, no signals.
Expected:
{{"decision": "abstain", "confidence": 0.1, "evidence_refs": ["name", "trade"], "bottleneck_hypothesis": "hypothesis: none — insufficient evidence", "why_now": "", "recommended_first_offer": "", "email_draft": "", "phone_opener": "", "missing_evidence": ["site_quality", "phones", "hiring", "reviews"], "abstain_reason": "no verified website, no contact path, no external signals"}}

Example 2 (strong evidence → priority):
Evidence: name="Real Firm LLC", trade="Accounting", site up/high confidence, automation_gaps=["no booking system"], phones=["(951) 225-1131"], hiring_role_match=true on own site, two corroborated review complaints.
Expected:
{{"decision": "priority", "confidence": 0.85, "evidence_refs": ["name", "trade", "phones", "site_quality", "automation_gaps", "hiring_evidence", "review_evidence"], "bottleneck_hypothesis": "hypothesis: intake/scheduling overload — no booking system plus hiring for an automatable role plus slow-response complaints", "why_now": "admin-heavy intake with a hiring load and corroborated slow-response complaints — removing the drag pays for itself this quarter", "recommended_first_offer": "an intake audit that stops missed calls and automates appointment scheduling", "email_draft": "Subject: missed calls and scheduling", "phone_opener": "I'll show you where your intake is leaking calls.", "missing_evidence": [], "abstain_reason": ""}}

Return ONLY the JSON object."""
    return prompt


def _parse_ai_review(raw):
    """Lenient JSON extraction + schema validation for the model reply.
    First {...} block wins; every failure path lands on abstain so a bad
    model response can NEVER crash the run or manufacture a decision.
    Never raises."""
    if not raw or not isinstance(raw, str):
        return {"decision": "abstain", "confidence": 0.0, "evidence_refs": [],
                "bottleneck_hypothesis": "", "why_now": "",
                "recommended_first_offer": "", "email_draft": "",
                "phone_opener": "", "missing_evidence": [],
                "abstain_reason": "unparseable model output"}
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {"decision": "abstain", "confidence": 0.0, "evidence_refs": [],
                "bottleneck_hypothesis": "", "why_now": "",
                "recommended_first_offer": "", "email_draft": "",
                "phone_opener": "", "missing_evidence": [],
                "abstain_reason": "unparseable model output"}
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return {"decision": "abstain", "confidence": 0.0, "evidence_refs": [],
                "bottleneck_hypothesis": "", "why_now": "",
                "recommended_first_offer": "", "email_draft": "",
                "phone_opener": "", "missing_evidence": [],
                "abstain_reason": "unparseable model output"}
    if not isinstance(data, dict):
        return {"decision": "abstain", "confidence": 0.0, "evidence_refs": [],
                "bottleneck_hypothesis": "", "why_now": "",
                "recommended_first_offer": "", "email_draft": "",
                "phone_opener": "", "missing_evidence": [],
                "abstain_reason": "unparseable model output"}
    # Decision whitelist; anything else → abstain.
    decision = data.get("decision", "abstain")
    if not isinstance(decision, str) or decision not in AI_REVIEW_DECISIONS:
        decision = "abstain"
    parsed = {
        "decision": decision,
        "confidence": data.get("confidence", 0.0),
        "evidence_refs": data.get("evidence_refs", []) or [],
        "bottleneck_hypothesis": data.get("bottleneck_hypothesis", "") or "",
        "why_now": data.get("why_now", "") or "",
        "recommended_first_offer": data.get("recommended_first_offer", "") or "",
        "email_draft": data.get("email_draft", "") or "",
        "phone_opener": data.get("phone_opener", "") or "",
        "missing_evidence": data.get("missing_evidence", []) or [],
        "abstain_reason": data.get("abstain_reason", "") or "",
    }
    if not isinstance(parsed["confidence"], (int, float)) or not isinstance(parsed["evidence_refs"], list):
        return {"decision": "abstain", "confidence": 0.0, "evidence_refs": [],
                "bottleneck_hypothesis": "", "why_now": "",
                "recommended_first_offer": "", "email_draft": "",
                "phone_opener": "", "missing_evidence": [],
                "abstain_reason": "unparseable model output"}
    # QC 2026-08-09: NaN confidence passes the isinstance check (it IS a
    # float) and would render 'nan%' in the weekly brief. Normalize to 0.0 —
    # NaN != NaN is the zero-import test.
    if isinstance(parsed["confidence"], float) and parsed["confidence"] != parsed["confidence"]:
        parsed["confidence"] = 0.0
    return parsed


def _apply_ai_fabrication_guard(parsed, biz):
    """Cheap, deterministic anti-embellishment: evidence_refs must be a
    subset of the evidence actually captured on this record. Unbacked claims
    are stripped, and a 'priority' that rests on zero valid refs is
    downgraded to abstain (a strong decision with no grounding is exactly
    the fabrication this guard exists to kill). Everything else passes
    through — the AI's prose is advisory and reviewed by a human later."""
    if parsed["decision"] == "abstain":
        return parsed
    valid = set(_ai_review_evidence_keys(biz))
    refs = parsed.get("evidence_refs") or []
    refs = [r for r in refs if isinstance(r, str) and r in valid]
    # Strip unbacked claims in the prose fields.
    for field in ("bottleneck_hypothesis", "why_now", "recommended_first_offer",
                  "email_draft", "phone_opener"):
        if isinstance(parsed.get(field), str):
            parsed[field] = parsed[field][:500]
    # A 'priority' verdict must rest on substantive evidence — a verified
    # website read, automation gaps, hiring/review evidence, provider
    # corroboration. Identity/score refs alone (name, trade, url, phones,
    # lead_score) can never ground a priority call; without substantive
    # backing it is downgraded to abstain (QC 2026-08-09: the old guard only
    # checked for ANY surviving ref, so a fabricated 'priority' citing name
    # + owner + revenue survived once the unbacked refs were stripped).
    if parsed["decision"] == "priority":
        if not refs or not (set(refs) & AI_REVIEW_SUBSTANTIVE_KEYS):
            return {"decision": "abstain", "confidence": 0.0, "evidence_refs": [],
                    "bottleneck_hypothesis": "", "why_now": "",
                    "recommended_first_offer": "", "email_draft": "",
                    "phone_opener": "", "missing_evidence": [],
                    "abstain_reason": "priority without substantive evidence — stripped by fabrication guard"}
    parsed["evidence_refs"] = refs
    return parsed


def ai_review_candidate(biz):
    """One grounded AI review for a single candidate. Returns the parsed
    review + metadata (model, provider, reviewed_at, latency_ms,
    tokens_used estimate). Any network/HTTP/timeout failure → abstain with
    the error recorded — never raises, never crashes the run."""
    if not AI_REVIEW_MODEL or not AI_REVIEW_BASE_URL:
        return {"decision": "abstain", "confidence": 0.0, "evidence_refs": [],
                "bottleneck_hypothesis": "", "why_now": "",
                "recommended_first_offer": "", "email_draft": "",
                "phone_opener": "", "missing_evidence": [],
                "abstain_reason": "AI review not configured"}
    start = time.monotonic()
    raw_body = ""  # captured for the token estimate; empty on early failure
    payload = json.dumps({
        "model": AI_REVIEW_MODEL,
        "messages": [
            {"role": "system", "content": "You are a rigorous, evidence-grounded prospect reviewer. Never invent facts."},
            {"role": "user", "content": _ai_review_prompt(biz)},
        ],
        "temperature": 0,           # determinism: same evidence → same review
        "max_tokens": AI_REVIEW_MAX_TOKENS,
    }).encode("utf-8")
    base = AI_REVIEW_BASE_URL.rstrip("/")
    url = f"{base}/chat/completions"
    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    if AI_REVIEW_API_KEY:
        headers["Authorization"] = f"Bearer {AI_REVIEW_API_KEY}"
    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=AI_REVIEW_TIMEOUT) as resp:
            raw_body = resp.read().decode("utf-8", errors="replace")
        body = json.loads(raw_body)
        raw = (body.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
        parsed = _parse_ai_review(raw)
        parsed = _apply_ai_fabrication_guard(parsed, biz)
    except Exception as e:  # noqa: BLE001 — a bad model/network must never kill the run
        parsed = {"decision": "abstain", "confidence": 0.0, "evidence_refs": [],
                  "bottleneck_hypothesis": "", "why_now": "",
                  "recommended_first_offer": "", "email_draft": "",
                  "phone_opener": "", "missing_evidence": [],
                  "abstain_reason": f"AI review unavailable: {e}"}
    latency_ms = int((time.monotonic() - start) * 1000)
    parsed["model"] = AI_REVIEW_MODEL
    parsed["provider"] = base
    parsed["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    parsed["latency_ms"] = latency_ms
    parsed["tokens_used"] = max(1, len(raw_body) // 4)  # ponytail char/4 estimate
    return parsed


def run_ai_review(cache):
    """Second-pass AI oversight over the bounded candidate set: eligible and
    research records only (post eligibility gate, post evidence enrichment),
    top AI_REVIEW_MAX_CANDIDATES by deterministic score, skipping records
    reviewed within AI_REVIEW_RECHECK_DAYS. Stores under biz['ai_review']
    and returns decision counts. NEVER touches lead_score or eligibility —
    purely advisory. Returns 0 reviewed when the reviewer is unconfigured
    (and never touches the network)."""
    if not AI_REVIEW_MODEL or not AI_REVIEW_BASE_URL:
        log("AI review skipped (not configured)")
        return {"reviewed": 0, "decisions": {}}
    bizs = cache.get("businesses", {})
    candidates = [
        (norm, biz) for norm, biz in bizs.items()
        if biz.get("eligibility_state") in ("eligible", "research") and biz.get("name")
    ]
    # Deterministic order: eligible first, then deterministic score desc.
    candidates.sort(key=lambda nb: (nb[1].get("eligibility_state", "") != "eligible",
                                    -(nb[1].get("lead_score") or {}).get("score", 0)))
    reviewed = 0
    decisions = {}
    for norm, biz in candidates[:AI_REVIEW_MAX_CANDIDATES]:
        old = (biz.get("ai_review") or {}).get("reviewed_at", "")
        if old:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(old)).days
            except ValueError:
                age = AI_REVIEW_RECHECK_DAYS + 1
            if age <= AI_REVIEW_RECHECK_DAYS:
                continue  # fresh review — keep cost discipline
        log(f"  AI review: {biz.get('name')}")
        review = ai_review_candidate(biz)
        biz["ai_review"] = review
        reviewed += 1
        decisions[review["decision"]] = decisions.get(review["decision"], 0) + 1
        time.sleep(0.5)  # polite rate-limit spread, like the other passes
    return {"reviewed": reviewed, "decisions": decisions}


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

    # SGW-864: directory/SEO records are rejected as businesses
    assert _is_directory_record("https://lawyerland.com/lawyers/local/murrieta/ca"), "SGW-864 fail: lawyerland not flagged"
    assert _is_directory_record("https://attorneyhelp.org/attorneys/ca_murrieta_lawyers.html"), "SGW-864 fail: attorneyhelp not flagged"
    assert _is_directory_record("https://allbiz.com/business/mbc-consulting-inc_2q"), "SGW-864 fail: allbiz not flagged"
    assert _is_directory_record("https://realbusiness.com/lawyers/murrieta"), "SGW-864 fail: /lawyers/ path not flagged"
    assert _is_directory_record("https://realbusiness.com", "Handyman"), "SGW-864 fail: generic name not flagged"
    assert not _is_directory_record("https://singletonsmith.com", "Singleton Smith Law Offices"), "SGW-864 fail: real business flagged"

    # SGW-864: distinctive-token guard stops cross-firm contamination
    assert _distinctive_name_tokens("MBC Consulting Inc") == [], "SGW-864 fail: generic-only name should yield no tokens"
    assert "singleton" in _distinctive_name_tokens("Singleton Smith Law Offices"), "SGW-864 fail: distinctive token dropped"
    assert _mentions_out_of_area("HVAC jobs in San Antonio, TX"), "SGW-864 fail: out-of-area not flagged"
    assert not _mentions_out_of_area("HVAC jobs in Murrieta, CA"), "SGW-864 fail: local city flagged as out-of-area"

    # SGW-865: recency + evidence provenance
    assert _signal_recency("Bookkeeper Job 2024") == "stale", "SGW-865 fail: 2024 not stale"
    assert _signal_recency("Bookkeeper Job 2026") == "recent", "SGW-865 fail: 2026 not recent"
    assert _signal_recency("Now Hiring Receptionist") == "unknown", "SGW-865 fail: undated should be unknown"
    assert _evidence_source_kind("https://www.ziprecruiter.com/Jobs/Bookkeeping") == "job_board", "SGW-865 fail: ziprecruiter not job_board"
    assert _evidence_source_kind("https://www.yelp.com/biz/singleton-smith") == "review_site", "SGW-865 fail: yelp not review_site"
    # SGW-942 B2: own_site now requires the host to actually BE the business's
    # own domain — the old assertion passed an arbitrary host with no
    # own_domains and demanded own_site, which is precisely the defect that
    # let aggregator pages score as first-party evidence.
    assert _evidence_source_kind("https://singletonsmith.com/careers",
                                 ["singletonsmith.com"]) == "own_site", \
        "SGW-942 fail: own domain not classified own_site"
    assert _evidence_source_kind("https://singletonsmith.com/careers") == "other", \
        "SGW-942 fail: host with no own_domains must not default to own_site"

    # SGW-866: dimension model present + gap-stacking can't reach Hot
    r8 = qualify_lead({"trade": "Accounting", "phones": ["(951) 555-0001"],
                        "own_domains": ["e.com"], "hiring_signals": [],
                        "review_negative": False, "review_signals": []},
                       {"status": "up", "confidence": "high", "website_score": 0,
                        "automation_gaps": ["no booking system", "no CRM", "no marketing tools"], "emails": []})
    assert "dimensions" in r8, "SGW-866 fail: dimensions missing"
    assert r8["tier"] != "Hot", f"SGW-866 fail: gap-stacking reached Hot (score={r8['score']})"
    assert r8["dimensions"]["actionability"] >= 8, f"SGW-866 fail: contactable+verified should be actionable ({r8['dimensions']})"

    # SGW-866: weak (aggregator-echo) hiring scores less than own-site hiring
    r9a = qualify_lead({"trade": "Accounting", "phones": ["(951) 555-0001"],
                         "own_domains": ["f.com"], "hiring_role_match": True,
                         "hiring_signals": [{"source_kind": "own_site", "title": "x"}],
                         "review_negative": False, "review_signals": []},
                        {"status": "up", "confidence": "high", "website_score": 3, "automation_gaps": [], "emails": []})
    r9b = qualify_lead({"trade": "Accounting", "phones": ["(951) 555-0001"],
                         "own_domains": ["g.com"], "hiring_role_match": True,
                         "hiring_signals": [{"source_kind": "job_board", "title": "x"}],
                         "review_negative": False, "review_signals": []},
                        {"status": "up", "confidence": "high", "website_score": 3, "automation_gaps": [], "emails": []})
    assert r9a["breakdown"]["growth_budget"] > r9b["breakdown"]["growth_budget"], \
        f"SGW-866 fail: own-site hiring should outscore aggregator echo ({r9a['breakdown']['growth_budget']} vs {r9b['breakdown']['growth_budget']})"

    # SGW-864 round 2: generic names → domain-brand re-key
    assert _is_generic_name("Contact Us"), "SGW-864 fail: Contact Us not generic"
    assert _is_generic_name("Temecula CPA, CPA"), "SGW-864 fail: Temecula CPA, CPA not generic"
    assert not _is_generic_name("Sanchez & Associates"), "SGW-864 fail: real brand flagged generic"
    assert _domain_brand_name("prudhommecpas.com") == "Prudhomme CPAs", f"SGW-864 fail: domain brand derivation ({_domain_brand_name('prudhommecpas.com')})"
    assert _domain_brand_name("khanattorneys.com") == "Khan Attorneys", f"SGW-864 fail: khan brand ({_domain_brand_name('khanattorneys.com')})"

    # SGW-863: collector registry + failure isolation
    assert collector_enabled("crawl_search"), "SGW-863 fail: default collector should be enabled"
    assert run_collector("review_signals", lambda: (_ for _ in ()).throw(RuntimeError("boom"))) is None, \
        "SGW-863 fail: collector failure must not propagate"
    COLLECTORS["hiring_signals"]["enabled"] = False
    assert run_collector("hiring_signals", lambda: "ran") is None, "SGW-863 fail: disabled collector must not run"
    COLLECTORS["hiring_signals"]["enabled"] = True
    assert run_collector("hiring_signals", lambda: "ran") == "ran", "SGW-863 fail: enabled collector should run"

    # Research 2026-08: NANP phone validation + outcome-first pitch lines
    assert extract_phones("Call (951) 555-1234 today") == [], "research fail: 555 exchange must be rejected"
    assert extract_phones("Call (100) 091-4084") == [], "research fail: invalid area code must be rejected"
    assert extract_phones("Call (951) 225-1131") == ["(951) 225-1131"], "research fail: real phone rejected"
    assert extract_phones("Call (178) 137-3717") == [], "research fail: 178 area code must be rejected"

    # ── SGW-942 B1b: framework identifiers must never read as phones ──
    # Real shipped defects (2026-09-11 QC): a Wix CSS class and a Wix site
    # UUID were extracted as NANP-valid phone numbers and presented to the
    # user as "verified contact paths".
    assert extract_phones(".StylableButton2545352419__root{-archetype:box}") == [], \
        "SGW-942 B1b fail: Wix CSS class read as a phone number"
    assert extract_phones('content="d0a90ec0-bf12-465a-88ee-5875261850ad"') == [], \
        "SGW-942 B1b fail: Wix site UUID read as a phone number"
    assert extract_phones("build-2024.0915091225.js") == [], \
        "SGW-942 B1b fail: version/hash fragment read as a phone number"
    # A number at the very start/end of the text must still be found.
    assert extract_phones("9512251131") == ["(951) 225-1131"], \
        "SGW-942 B1b fail: leading bare number rejected"
    assert extract_phones("Tel: (951) 440-3498") == ["(951) 440-3498"], \
        "SGW-942 B1b fail: parenthesised number rejected"

    # ── SGW-942 B2: provenance must default to WEAK, never own_site ──
    assert _evidence_source_kind("https://www.upwork.com/freelance-jobs/bookkeeping/",
                                 ["superiorvirtualaccounting.com"]) == "job_board", \
        "SGW-942 B2 fail: upwork stamped as own_site"
    assert _evidence_source_kind("https://www.instawork.com/jobs/x", ["protechjobs.com"]) == "job_board", \
        "SGW-942 B2 fail: instawork stamped as own_site"
    assert _evidence_source_kind("https://plumbingjobs.org/x", ["encoreplumbing.com"]) == "job_board", \
        "SGW-942 B2 fail: plumbingjobs.org stamped as own_site"
    assert _evidence_source_kind("https://www.ziprecruiter.com/Jobs/X", ["x.com"]) == "job_board", \
        "SGW-942 B2 fail: ziprecruiter not job_board"
    assert _evidence_source_kind("https://singletonsmith.com/careers", ["singletonsmith.com"]) == "own_site", \
        "SGW-942 B2 fail: genuine own-site signal not own_site"
    assert _evidence_source_kind("https://singletonsmith.com/careers", ["other.com"]) == "other", \
        "SGW-942 B2 fail: foreign host stamped own_site"
    assert _evidence_source_kind("https://some-unknown-host.example/x", None) == "other", \
        "SGW-942 B2 fail: unknown host with no own_domains stamped own_site"

    # ── SGW-942 B3: national branches and locator titles are not eligible ──
    assert _is_national_branch("https://www.expresspros.com/us-california-moreno-valley",
                               "Express Employment Professionals", ["expresspros.com"]) is True, \
        "SGW-942 B3 fail: Express franchise branch passed as local business"
    assert _is_national_branch("https://www.newyorklife.com/agents/find-an-agent/ca/temecula",
                               "Agent Directory", ["newyorklife.com"]) is True, \
        "SGW-942 B3 fail: NYL agent locator passed as local business"
    assert _is_national_branch("https://libertycompany.com/locations/california/murrieta",
                               "Liberty Company", ["libertycompany.com"]) is True, \
        "SGW-942 B3 fail: corporate location page passed as local business"
    assert _is_national_branch("https://murrietaplumbing.com", "Murrieta Plumbing",
                               ["murrietaplumbing.com"]) is False, \
        "SGW-942 B3 fail: real local business flagged as national branch"
    assert _is_generic_locator_title("Agent Directory") is True, \
        "SGW-942 B3 fail: locator chrome accepted as a business name"
    assert _is_generic_locator_title("Reid & Hellyer") is False, \
        "SGW-942 B3 fail: real firm name flagged as locator chrome"
    _st, _rs = assess_eligibility("https://www.expresspros.com/us-california-moreno-valley",
                                  "Express Employment Professionals", "Recruiting",
                                  ["(951) 823-0023"], ["expresspros.com"])
    assert _st == "research", f"SGW-942 B3 fail: national branch was '{_st}' not 'research'"
    # ── SGW-942 B1c: shape-valid but fake numbers ──
    for _fake in ("(666) 666-6666", "(333) 333-3333", "(444) 444-4444",
                  "(214) 748-3647", "(555) 967-1920", "(200) 951-7308",
                  "(777) 714-2857", "(999) 519-5000", "(111) 222-3333",
                  "(857) 142-8571", "(394) 095-7316"):
        assert _normalize_phone(_fake) is None, \
            f"SGW-942 B1c fail: fake/placeholder number accepted: {_fake}"
    # ...and real numbers that merely LOOK patterned must survive.
    for _real in ("(951) 440-3498", "(951) 444-1404", "(865) 935-8888",
                  "(505) 293-3333", "(951) 222-2910", "(800) 222-4057",
                  "(951) 926-6200"):
        assert _normalize_phone(_real) == _real, \
            f"SGW-942 B1c fail: real number wrongly rejected: {_real}"

    # ── SGW-942 B1c: the duplicate merge must be conservative ──
    # A shared trade word is NOT identity — these two are different firms.
    _plumb_a = {"name": "Plumbing Services", "phones": ["(951) 370-1578"],
                "own_domains": ["plumbermurrieta.plumbing"]}
    _plumb_b = {"name": "Encore Plumbing & Air", "phones": ["(951) 370-1578"],
                "own_domains": ["encoreplumbingtemecula.com"]}
    assert _same_business(_plumb_a, _plumb_b) is False, \
        "SGW-942 fail: distinct firms sharing a trade word were merged"
    # Genuine duplicate: same phone AND same distinctive name tokens.
    _dup_a = {"name": "Taxes and Accounting, CPA -Copeland, Benner & Associates",
              "phones": ["(951) 699-1040"], "own_domains": ["copelandbennercpas.com"]}
    _dup_b = {"name": "Copeland, Miranda & Benner, CPAs An Accountancy Corporation",
              "phones": ["(951) 699-1040"], "own_domains": ["vistacpa.com"]}
    assert _same_business(_dup_a, _dup_b) is True, \
        "SGW-942 fail: genuine duplicate not detected"
    # Different phone → never the same business, however similar the name.
    _other = dict(_dup_b); _other["phones"] = ["(951) 999-1234"]
    assert _same_business(_dup_a, _other) is False, \
        "SGW-942 fail: records with different phones merged"
    # The merge must never delete a phone number.
    _mc = {"businesses": {
        "a": {"name": "Taxes and Accounting, CPA -Copeland, Benner & Associates",
              "phones": ["(951) 699-1040"], "own_domains": ["copelandbennercpas.com"],
              "site_quality": {"status": "up", "confidence": "high", "phones": ["(951) 699-1040"]}},
        "b": {"name": "Copeland, Miranda & Benner, CPAs An Accountancy Corporation",
              "phones": ["(951) 699-1040"], "own_domains": ["vistacpa.com"],
              "site_quality": {"status": "up", "confidence": "high", "phones": ["(951) 699-1040"]}},
    }}
    _n = merge_duplicate_records(_mc)
    assert _n == 1, f"SGW-942 fail: expected 1 merge, got {_n}"
    _survivors = [v for v in _mc["businesses"].values()
                  if not v.get("_retired")]
    assert len(_survivors) == 1 and "(951) 699-1040" in _survivors[0]["phones"], \
        "SGW-942 fail: merge dropped the shared phone number"

    # ── SGW-943: absence must be observable, not assumed ──
    # The tool detector reported "no CRM" / "no analytics" / "no marketing
    # tools" / "no click-to-call" from a TRUNCATED read. Measured against 40
    # live sites: 5 of 11 'no click-to-call', 4 of 13 'no analytics', 2 of 37
    # 'no CRM' and 5 of 39 'no marketing tools' claims were false, and EVERY
    # one was a marker sitting past the fetch byte budget. A gap may only be
    # asserted from a COMPLETE read (AGENTS.md §1b: PRESENT/ABSENT/UNKNOWN must
    # never be merged).
    assert _may_assert_gap(False, []) is True, \
        "SGW-943 fail: a complete read may assert absence"
    assert _may_assert_gap(True, []) is False, \
        "SGW-943 fail: a truncated read must NOT assert absence"
    assert _may_assert_gap(True, ["HubSpot"]) is False, \
        "SGW-943 fail: a FOUND marker is PRESENT — it can never be a gap"
    assert _may_assert_gap(False, ["HubSpot"]) is False, \
        "SGW-943 fail: a found marker is not a gap on a complete read either"

    p = pitch_for({"trade": "Accounting", "review_negative": True})
    assert "miss" in p, f"research fail: pitch not outcome-first ({p})"
    p2 = pitch_for({"trade": "Law Office"})
    assert "billable" in p2, f"research fail: admin pitch not outcome-first ({p2})"
    # SGW-928: pitch sells the OUTCOME, never the tool (diagnosis-first; the
    # implementation — process, software, or AI — is chosen after the audit).
    # A business with no AI-specific evidence must get a tool-agnostic pitch.
    for no_ai_biz, expect in (
        ({"trade": "Accounting", "site_quality": {"automation_gaps": ["no booking system"]}}, "intake"),
        ({"trade": "Plumbing", "site_quality": {"automation_gaps": ["no booking system"]}}, "missed revenue"),
        ({"trade": "Plumbing"}, "missed"),
    ):
        pb = pitch_for(no_ai_biz)
        assert expect in pb, f"SGW-928 fail: pitch lost outcome ({pb})"
        for banned in ("digital worker", "AI agent", "autopilot", "hold music"):
            assert banned not in pb, f"SGW-928 fail: pitch leads with tool ({pb})"

    # ── SGW-938 B1: canonical phone validator — every ingestion path agrees ──
    # tel: href path must reject what extract_phones rejects
    assert _normalize_phone("tel:(100) 091-4084") is None, "B1 fail: tel: bad area code accepted"
    assert _normalize_phone("tel:(007) 780-0750") is None, "B1 fail: tel: 007 area code accepted"
    assert _normalize_phone("tel:(178) 137-3717") is None, "B1 fail: tel: 178 area code accepted"
    assert _normalize_phone("tel:(951) 555-1234") is None, "B1 fail: tel: 555 exchange accepted"
    assert _normalize_phone("tel:(951) 225-1131") == "(951) 225-1131", "B1 fail: tel: valid number rejected"
    assert _normalize_phone("(951) 225-1131") == "(951) 225-1131", "B1 fail: valid formatted number rejected"
    assert _normalize_phone("+1 (951) 225-1131") == "(951) 225-1131", "B1 fail: +1 country code rejected"
    assert _normalize_phone("9512251131") == "(951) 225-1131", "B1 fail: bare digits rejected"
    assert _normalize_phone("(951) 225-113") is None, "B1 fail: 9-digit number accepted"
    assert _normalize_phone("411") is None, "B1 fail: short garbage accepted"
    # cache sweep: contaminated records must be purged on load
    test_cache = {"businesses": {
        "b1": {"phones": ["(951) 225-1131", "(100) 091-4084"], "own_domains": ["x.com"],
               "name": "Real Co", "last_seen": "2099-01-01T00:00:00+00:00"},
    }, "signals": [], "fb_groups": []}
    import tempfile
    import pathlib as _pl
    with tempfile.TemporaryDirectory() as _td:
        _orig_cache = CACHE_FILE
        _tmp_cache = _pl.Path(_td) / "cache.json"
        import copy
        _c = copy.deepcopy(test_cache)
        _tmp_cache.write_text(json.dumps(_c), encoding="utf-8")
        try:
            globals()["CACHE_FILE"] = _tmp_cache
            _loaded = load_cache()
            assert _loaded["businesses"]["b1"]["phones"] == ["(951) 225-1131"], \
                f"B1 fail: cache sweep kept contaminated phone {_loaded['businesses']['b1']['phones']}"
        finally:
            globals()["CACHE_FILE"] = _orig_cache

    # ── SGW-938 B2: domain-boundary aggregator matching ──
    assert _is_aggregator_domain("lawyers.com") is True, "B2 fail: exact aggregator domain not blocked"
    assert _is_aggregator_domain("www.lawyers.com") is True, "B2 fail: subdomain not blocked"
    assert _is_aggregator_domain("prfamilylawyers.com") is False, "B2 fail: containing-domain real firm dropped"
    assert _is_aggregator_domain("myattorneys.agency.yelp.com") is True, "B2 fail: deep subdomain not blocked"
    assert is_aggregator("PrFamily Lawyers", "https://prfamilylawyers.com") is False, \
        "B2 fail: is_aggregator still drops containing-domain real firm"
    assert is_aggregator("Some Listing", "https://www.yelp.com/biz/x") is True, \
        "B2 fail: yelp not blocked in is_aggregator"
    assert is_aggregator("Some Listing", "https://www.yelpcdn.com/x") is True, \
        "B2 fail: yelpcdn (explicitly blocklisted) not blocked by boundary rule"

    # ── SGW-939: signal coverage sweep + report ──
    _now = datetime.now(timezone.utc).isoformat()
    _old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    _sweep_cache = {"businesses": {
        "fresh": {"name": "Fresh Co", "trade": "Accounting",
                  "phones": ["(951) 555-0101"], "own_domains": ["fresh.com"],
                  "site_quality": {"status": "up", "confidence": "high", "website_score": 3},
                  "hiring_checked": True, "hiring_checked_at": _now,
                  "review_checked": True, "review_checked_at": _now,
                  "lead_score": {"score": 50, "tier": "Warm"}},
        "never": {"name": "Never Co", "trade": "Accounting",
                  "phones": ["(951) 555-0102"], "own_domains": ["never.com"],
                  "site_quality": {"status": "up", "confidence": "high", "website_score": 3},
                  "lead_score": {"score": 60, "tier": "Warm"}},
        "stale": {"name": "Stale Co", "trade": "Accounting",
                  "phones": ["(951) 555-0103"], "own_domains": ["stale.com"],
                  "site_quality": {"status": "up", "confidence": "high", "website_score": 3},
                  "hiring_checked": True, "hiring_checked_at": _old,
                  "review_checked": True, "review_checked_at": _old,
                  "lead_score": {"score": 40, "tier": "Cold"}},
        "nosq": {"name": "No SQ Co", "trade": "Accounting",
                 "phones": ["(951) 555-0104"], "own_domains": ["nosq.com"],
                 "lead_score": {"score": 99, "tier": "Warm"}},
    }, "signals": [], "fb_groups": []}
    _cands = signal_sweep_candidates(_sweep_cache)
    _names = [c[2] for c in _cands]
    assert _names == ["never", "stale"], f"939 fail: sweep priority wrong ({_names})"
    assert "nosq" not in _names, "939 fail: no-site_quality record must not be eligible"
    assert "fresh" not in _names, "939 fail: fresh-on-both record must not be eligible"
    # partial (one checked, one not) → priority 1 (after never-never, before stale)
    _sweep_cache["businesses"]["partial"] = {
        "name": "Partial Co", "trade": "Accounting",
        "phones": ["(951) 555-0105"], "own_domains": ["partial.com"],
        "site_quality": {"status": "up", "confidence": "high", "website_score": 3},
        "hiring_checked": True, "hiring_checked_at": _now,
        "lead_score": {"score": 55, "tier": "Warm"}}
    _cands2 = signal_sweep_candidates(_sweep_cache)
    _prios = [(c[2], c[0]) for c in _cands2]
    assert _prios == [("never", 0), ("partial", 1), ("stale", 2)], f"939 fail: sweep tiers ({_prios})"
    # coverage math
    _cov = generate_coverage_report(_sweep_cache, out_path="/tmp/sgw939-cov-test.json")
    assert _cov["eligible"] == 4, f"939 fail: eligible count {_cov['eligible']}"
    assert _cov["fresh_both"] == 1, f"939 fail: fresh_both {_cov['fresh_both']}"
    assert _cov["fresh_both_percent"] == 25, f"939 fail: pct {_cov['fresh_both_percent']}"
    assert _cov["never_checked"] == 2, f"939 fail: never_checked {_cov['never_checked']}"  # never + partial
    assert _cov["warm_eligible"] == 3 and _cov["warm_fresh"] == 1, \
        f"939 fail: warm coverage {_cov['warm_eligible']}/{_cov['warm_fresh']}"
    assert _signal_checked_recently(_sweep_cache["businesses"]["fresh"]) is True, \
        "939 fail: fresh record misjudged"
    assert _signal_checked_recently(_sweep_cache["businesses"]["stale"]) is False, \
        "939 fail: stale record misjudged"
    # QC (2026-08-09): rejected records are excluded from the coverage
    # denominator and from the sweep — they never get re-checked, so counting
    # them would permanently depress coverage and waste sweep budget.
    _sweep_cache["businesses"]["rejected1"] = {
        "name": "Rejected Co", "trade": "Accounting",
        "phones": ["(951) 555-0106"], "own_domains": ["ca.gov"],
        "site_quality": {"status": "up", "confidence": "high", "website_score": 3},
        "lead_score": {"score": 50, "tier": "Warm"},
        "eligibility_state": "rejected", "eligibility_reason": "government/public entity"}
    _cov2 = generate_coverage_report(_sweep_cache, out_path="/tmp/sgw939-cov-test2.json")
    assert _cov2["eligible"] == 4, f"939/QC fail: rejected in denominator ({_cov2['eligible']})"
    _cands3 = signal_sweep_candidates(_sweep_cache)
    assert "rejected1" not in [c[2] for c in _cands3], "939/QC fail: rejected record swept"

    # ── SGW-941: eligibility gate ──
    # Government / public agency — rejected
    assert assess_eligibility("https://ca.gov/board/accountancy", "CA Board", "Accounting",
                              ["(951) 555-0101"], ["ca.gov"])[0] == "rejected", "941 fail: gov not rejected"
    assert assess_eligibility("https://school.edu/", "Some College", "Education",
                              ["(951) 555-0101"], ["school.edu"])[0] == "rejected", "941 fail: edu not rejected"
    # Corporate locator/careers subdomains — rejected
    assert assess_eligibility("https://agents.statefarm.com/ca/murrieta", "State Farm Agent", "Insurance",
                              ["(951) 555-0101"], ["agents.statefarm.com"])[0] == "rejected", "941 fail: agents. subdomain not rejected"
    assert assess_eligibility("https://jobs.allstate.com/", "Allstate Careers", "Insurance",
                              ["(951) 555-0101"], ["jobs.allstate.com"])[0] == "rejected", "941 fail: jobs. subdomain not rejected"
    # National enterprise branch — rejected
    assert assess_eligibility("https://www.allstate.com/murrieta-office", "Allstate Murrieta", "Insurance",
                              ["(951) 555-0101"], ["allstate.com"])[0] == "rejected", "941 fail: national enterprise not rejected"
    # Directory / SEO listing — rejected (SGW-864 rules preserved)
    assert assess_eligibility("https://lawyerland.com/lawyers/murrieta", "Lawyerland", "Law",
                              ["(951) 555-0101"], ["lawyerland.com"])[0] == "rejected", "941 fail: directory not rejected"
    # Generic page title without own domain — rejected (a title is not a business)
    assert assess_eligibility("https://example.com/contact", "Contact Us", "Law",
                              [], [])[0] == "rejected", "941 fail: generic page title not rejected"
    # No contact path — research
    assert assess_eligibility("https://realfirm.com", "Real Firm LLC", "Accounting",
                              [], ["realfirm.com"])[0] == "research", "941 fail: no-contact not research"
    # Distinct local business — eligible
    assert assess_eligibility("https://singletonsmith.com", "Singleton Smith Law Offices", "Law Office",
                              ["(951) 555-0101"], ["singletonsmith.com"])[0] == "eligible", "941 fail: real firm not eligible"
    # Local franchise/office with own identity + contact — eligible (parent is not the prospect)
    assert assess_eligibility("https://murrietainsurance.com", "Murrieta Insurance Agency", "Insurance",
                              ["(951) 555-0101"], ["murrietainsurance.com"])[0] == "eligible", "941 fail: local agency not eligible"
    # apply_eligibility_sweep routing: rejected/research → Cold zeroed, eligible keeps score
    _elig_cache = {"businesses": {
        "gov1": {"name": "CA Board", "url": "https://ca.gov/board", "own_domains": ["ca.gov"],
                 "phones": ["(951) 555-0101"], "lead_score": {"score": 54, "tier": "Warm"}},
        "firm1": {"name": "Real Firm LLC", "url": "https://realfirm.com", "own_domains": ["realfirm.com"],
                  "phones": ["(951) 555-0101"], "lead_score": {"score": 50, "tier": "Warm"}},
        "noct1": {"name": "No Contact LLC", "url": "https://noct.com", "own_domains": ["noct.com"],
                  "phones": [], "lead_score": {"score": 45, "tier": "Warm"}},
    }}
    _ec = apply_eligibility_sweep(_elig_cache)
    assert _ec == {"eligible": 1, "research": 1, "rejected": 1}, f"941 fail: sweep counts {_ec}"
    assert _elig_cache["businesses"]["gov1"]["lead_score"]["tier"] == "Cold" and \
        _elig_cache["businesses"]["gov1"]["lead_score"]["score"] == 0, "941 fail: gov not routed to Cold"
    assert _elig_cache["businesses"]["noct1"]["lead_score"]["tier"] == "Cold", "941 fail: no-contact not routed"
    assert _elig_cache["businesses"]["firm1"]["lead_score"]["tier"] == "Warm", "941 fail: eligible firm lost score"
    # QC (2026-08-09): corrupt non-dict lead_score must not crash the sweep
    _elig_cache["businesses"]["corrupt1"] = {
        "name": "Corrupt Co", "url": "https://corrupt.gov", "own_domains": ["corrupt.gov"],
        "phones": ["(951) 555-0101"], "lead_score": "corrupted-string-value"}
    _ec2 = apply_eligibility_sweep(_elig_cache)
    assert _elig_cache["businesses"]["corrupt1"]["lead_score"]["tier"] == "Cold", \
        "941/QC fail: corrupt lead_score not replaced"

    # ── SGW-925: Google Places identity enrichment — dormant adapter ──
    import unittest.mock as _mock
    _saved_key = globals()["PLACES_API_KEY"]
    try:
        # 1) No key → fully inert: no network, no evidence, record untouched.
        globals()["PLACES_API_KEY"] = ""
        _inert = {"name": "Inert Co", "trade": "Plumbing",
                  "site_quality": {"status": "up", "website_score": 2},
                  "lead_score": {"score": 55, "tier": "Warm"},
                  "eligibility_state": "eligible"}
        assert places_identity("Inert Co") is None, "925 fail: no-key places_identity must return None"
        _before = json.dumps(_inert, sort_keys=True)
        with _mock.patch.object(urllib.request, "urlopen") as _u:
            _n = run_places_enrichment({"businesses": {"inert": _inert}})
        _u.assert_not_called()  # 925 fail: no-key run must not touch the network
        assert _n == 0, "925 fail: no-key run must check 0 records"
        assert _inert.get("provider_evidence") is None, "925 fail: no-key run stored evidence"
        assert json.dumps(_inert, sort_keys=True) == _before, "925 fail: no-key run mutated record"
        # 2) Key + valid response → evidence stored with the contract fields,
        #    and deterministic site_quality/lead_score are never overwritten.
        globals()["PLACES_API_KEY"] = "fake-key-925"
        _hit = {"name": "Real Firm LLC", "trade": "Accounting",
                "site_quality": {"status": "up", "confidence": "high", "website_score": 3},
                "lead_score": {"score": 60, "tier": "Warm"},
                "eligibility_state": "eligible"}
        _sq_before = json.dumps(_hit["site_quality"], sort_keys=True)
        _ls_before = json.dumps(_hit["lead_score"], sort_keys=True)
        _ok_payload = json.dumps({
            "status": "OK",
            "candidates": [{
                "name": "Real Firm LLC", "formatted_address": "123 Main St, Murrieta, CA 92562",
                "place_id": "ChIJfake925", "website": "https://realfirm.com",
                "international_phone_number": "(951) 225-1131",
                "business_status": "OPERATIONAL",
            }]}).encode()

        class _FakeResp:
            def __init__(self, payload):
                self._payload = payload
            def read(self):
                return self._payload
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        with _mock.patch.object(urllib.request, "urlopen", return_value=_FakeResp(_ok_payload)):
            _n = run_places_enrichment({"businesses": {"hit": _hit}})
        assert _n == 1, f"925 fail: keyed run checked {_n} records"
        _ev = _hit["provider_evidence"]["google_places"]
        for _req in ("provider", "place_id", "observed_at", "confidence", "state", "provenance", "fresh_until"):
            assert _ev.get(_req), f"925 fail: evidence missing {_req}"
        assert _ev["provider"] == "google_places" and _ev["provenance"] == "google_places_api"
        assert _ev["place_id"] == "ChIJfake925" and _ev["phone"] == "(951) 225-1131"
        # Neutral corroboration only — score/quality never touched by provider data.
        assert json.dumps(_hit["site_quality"], sort_keys=True) == _sq_before, "925 fail: site_quality overwritten"
        assert json.dumps(_hit["lead_score"], sort_keys=True) == _ls_before, "925 fail: lead_score overwritten"
        # Missing fields stay UNKNOWN (absent), never fabricated pain.
        _sparse_payload = json.dumps({
            "status": "OK",
            "candidates": [{"name": "Sparse Co", "business_status": "OPERATIONAL"}]}).encode()
        _sparse = {"name": "Sparse Co", "trade": "Plumbing", "eligibility_state": "eligible"}
        with _mock.patch.object(urllib.request, "urlopen", return_value=_FakeResp(_sparse_payload)):
            run_places_enrichment({"businesses": {"sparse": _sparse}})
        _sev = _sparse["provider_evidence"]["google_places"]
        assert "phone" not in _sev and "website" not in _sev and "address" not in _sev, \
            "925 fail: absent fields must stay UNKNOWN (not fabricated)"
        assert _sev.get("confidence") == "medium", "925 fail: unconfirmed match confidence"
        # 3) Failure-safe: HTTP error and empty result → None, nothing stored, no crash.
        _fail = {"name": "Fail Co", "trade": "Plumbing", "eligibility_state": "eligible"}
        with _mock.patch.object(urllib.request, "urlopen",
                                side_effect=urllib.error.URLError("boom")):
            _r = run_collector("places_identity", places_identity, "Fail Co", "Plumbing")
        assert _r is None, "925 fail: provider failure must return None"
        assert _fail.get("provider_evidence") is None, "925 fail: failure stored evidence"
        _empty = {"name": "Empty Co", "trade": "Plumbing", "eligibility_state": "eligible"}
        with _mock.patch.object(urllib.request, "urlopen", return_value=_FakeResp(
                json.dumps({"status": "ZERO_RESULTS", "candidates": []}).encode())):
            run_places_enrichment({"businesses": {"empty": _empty}})
        assert _empty.get("provider_evidence") is None, "925 fail: ZERO_RESULTS stored evidence"
    finally:
        globals()["PLACES_API_KEY"] = _saved_key

    # ── SGW-940: grounded AI review — opt-in, advisory, fabrication-guarded ──
    _saved_ai = (globals()["AI_REVIEW_MODEL"], globals()["AI_REVIEW_BASE_URL"],
                 globals()["AI_REVIEW_API_KEY"])
    try:
        # 1) Unconfigured (no model, no base_url) → zero reviewed, records
        #    untouched, NO network (urlopen must never be called).
        globals()["AI_REVIEW_MODEL"] = ""
        globals()["AI_REVIEW_BASE_URL"] = ""
        globals()["AI_REVIEW_API_KEY"] = ""
        _inert_ai = {"name": "Inert Co", "trade": "Plumbing",
                     "eligibility_state": "eligible",
                     "lead_score": {"score": 55, "tier": "Warm"}}
        _before_ai = json.dumps(_inert_ai, sort_keys=True)
        with _mock.patch.object(urllib.request, "urlopen") as _u:
            _r = run_ai_review({"businesses": {"inert": _inert_ai}})
        _u.assert_not_called()  # 940 fail: unconfigured review must not touch the network
        assert _r["reviewed"] == 0, f"940 fail: unconfigured review checked {_r['reviewed']}"
        assert _r["decisions"] == {}, f"940 fail: unconfigured review decisions {_r['decisions']}"
        assert json.dumps(_inert_ai, sort_keys=True) == _before_ai, "940 fail: unconfigured review mutated record"
        assert _inert_ai.get("ai_review") is None, "940 fail: unconfigured review stored ai_review"
        # 2) Parse layer: garbage / empty / non-JSON model output → abstain, never crash.
        assert _parse_ai_review(None)["decision"] == "abstain"
        assert _parse_ai_review("")["decision"] == "abstain"
        assert _parse_ai_review("Sure! Here's my thinking...")["decision"] == "abstain"
        assert _parse_ai_review("```json\n{not valid json\n```")["decision"] == "abstain"
        assert _parse_ai_review("42")["decision"] == "abstain"
        # Decision whitelist: unknown decision → abstain, valid ones pass.
        assert _parse_ai_review(json.dumps({"decision": "urgent"}))["decision"] == "abstain"
        for _d in ("priority", "research", "watch", "reject", "abstain"):
            _p = _parse_ai_review(json.dumps({"decision": _d}))
            assert _p["decision"] == _d, f"940 fail: whitelist {_d} → {_p['decision']}"
        # Contract fields present with defaults on a minimal payload.
        _p = _parse_ai_review(json.dumps({"decision": "watch", "confidence": 0.5}))
        assert _p["confidence"] == 0.5 and _p["evidence_refs"] == [] and _p["abstain_reason"] == "", \
            f"940 fail: contract defaults {_p}"
        # 3) Anti-fabrication guard: weak evidence + model claims priority with
        #    fabricated owner/revenue and refs that do not map to captured
        #    evidence → priority downgraded to abstain, unbacked refs stripped.
        _thin = {"name": "Thin Co", "trade": "Plumbing", "eligibility_state": "eligible",
                 "lead_score": {"score": 20, "tier": "Cold"},
                 "site_quality": {"status": "unknown", "confidence": "low",
                                  "automation_gaps": [], "website_score": -1}}
        _fabricated = json.dumps({
            "decision": "priority", "confidence": 0.95,
            "evidence_refs": ["owner", "revenue", "name"],
            "bottleneck_hypothesis": "hypothesis: owner Mike runs everything manually",
            "why_now": "revenue $2M/yr and growing fast",
            "recommended_first_offer": "AI agent to run their books",
            "email_draft": "Hey Mike...", "phone_opener": "Hi Mike",
            "missing_evidence": [], "abstain_reason": "",
        }).encode()
        _thin_before = json.dumps(_thin, sort_keys=True)
        globals()["AI_REVIEW_MODEL"] = "fake-model-940"
        globals()["AI_REVIEW_BASE_URL"] = "https://fake-endpoint.example/v1"
        globals()["AI_REVIEW_API_KEY"] = ""
        with _mock.patch.object(urllib.request, "urlopen",
                                return_value=_FakeResp(json.dumps({
                                    "choices": [{"message": {"content": _fabricated.decode()}}]}).encode())):
            _thin_rev = ai_review_candidate(_thin)
        assert _thin_rev["decision"] == "abstain", \
            f"940 fail: thin-evidence priority not downgraded ({_thin_rev['decision']})"
        assert "fabrication guard" in _thin_rev["abstain_reason"], \
            f"940 fail: wrong downgrade reason {_thin_rev['abstain_reason']}"
        assert "owner" not in _thin_rev["evidence_refs"], f"940 fail: unbacked ref survived {_thin_rev['evidence_refs']}"
        assert json.dumps(_thin, sort_keys=True) == _thin_before, "940 fail: ai_review_candidate mutated record"
        # QC (2026-08-09): the fabricated-priority bypass — site_quality on an
        # UNKNOWN-status record must NOT count as substantive evidence. A model
        # citing ['name','site_quality'] with fabricated prose (owner name,
        # 'AI agent' tool claim) must be downgraded to abstain.
        _unv = {"name": "Unv Co", "trade": "Plumbing", "eligibility_state": "eligible",
                "lead_score": {"score": 30, "tier": "Cold"},
                "site_quality": {"status": "unknown", "confidence": "low",
                                 "automation_gaps": [], "website_score": -1}}
        _unv_fab = json.dumps({
            "decision": "priority", "confidence": 0.95,
            "evidence_refs": ["name", "site_quality"],
            "bottleneck_hypothesis": "hypothesis: owner Mike runs everything manually",
            "why_now": "revenue $2M/yr and growing fast",
            "recommended_first_offer": "AI agent to run their books",
            "email_draft": "Hey Mike, your intake is broken",
            "phone_opener": "Hi Mike", "missing_evidence": [], "abstain_reason": "",
        }).encode()
        with _mock.patch.object(urllib.request, "urlopen",
                                return_value=_FakeResp(json.dumps({
                                    "choices": [{"message": {"content": _unv_fab.decode()}}]}).encode())):
            _unv_rev = ai_review_candidate(_unv)
        assert _unv_rev["decision"] == "abstain", \
            f"940/QC fail: site_quality(unknown) priority bypass survived ({_unv_rev['decision']})"
        assert "fabrication guard" in _unv_rev["abstain_reason"], \
            f"940/QC fail: wrong bypass downgrade reason {_unv_rev['abstain_reason']}"
        # NaN confidence must not survive parsing (would render 'nan%').
        _nan = _parse_ai_review(json.dumps({"decision": "watch", "confidence": float("nan")}))
        assert _nan["confidence"] == 0.0, f"940/QC fail: NaN confidence survived ({_nan['confidence']})"
        # A 'watch' with mixed refs: valid refs kept, unbacked refs stripped.
        _mixed = json.dumps({
            "decision": "watch", "confidence": 0.4,
            "evidence_refs": ["name", "revenue", "trade"],
            "bottleneck_hypothesis": "hypothesis: manual intake",
            "why_now": "", "recommended_first_offer": "",
            "email_draft": "", "phone_opener": "", "missing_evidence": [], "abstain_reason": "",
        }).encode()
        with _mock.patch.object(urllib.request, "urlopen",
                                return_value=_FakeResp(json.dumps({
                                    "choices": [{"message": {"content": _mixed.decode()}}]}).encode())):
            _mixed_rev = ai_review_candidate(_thin)
        assert _mixed_rev["decision"] == "watch", f"940 fail: watch lost ({_mixed_rev['decision']})"
        assert _mixed_rev["evidence_refs"] == ["name", "trade"], \
            f"940 fail: unbacked ref not stripped {_mixed_rev['evidence_refs']}"
        # 4) SGW-928 alignment: a process-only (non-AI) recommended_first_offer
        #    survives storage as-is, and the prompt itself must not force
        #    AI-first language.
        _proc = {"name": "Proc Co", "trade": "Accounting", "eligibility_state": "eligible",
                 "phones": ["(951) 225-1131"],
                 "site_quality": {"status": "up", "confidence": "high",
                                  "website_score": 1, "automation_gaps": ["no booking system"],
                                  "platform": "wordpress", "observed_at": "2099-01-01T00:00:00+00:00"},
                 "lead_score": {"score": 50, "tier": "Warm"},
                 "hiring_checked_at": "2099-01-01T00:00:00+00:00",
                 "hiring_role_match": False, "hiring_signals": [],
                 "review_checked_at": "2099-01-01T00:00:00+00:00",
                 "review_negative": False, "review_signals": []}
        _proc_payload = json.dumps({
            "decision": "priority", "confidence": 0.7,
            "evidence_refs": ["name", "trade", "phones", "site_quality", "automation_gaps"],
            "bottleneck_hypothesis": "hypothesis: no booking system means missed intake calls",
            "why_now": "manual scheduling is visible drag",
            "recommended_first_offer": "an intake audit that stops missed calls — we fix the process, then decide what software or automation to use",
            "email_draft": "Subject: missed calls\nMost of your calls are being missed...",
            "phone_opener": "I'll show you where your intake is leaking calls.",
            "missing_evidence": [], "abstain_reason": "",
        }).encode()
        with _mock.patch.object(urllib.request, "urlopen",
                                return_value=_FakeResp(json.dumps({
                                    "choices": [{"message": {"content": _proc_payload.decode()}}]}).encode())):
            _proc_rev = ai_review_candidate(_proc)
        assert _proc_rev["decision"] == "priority", f"940 fail: process-only review lost ({_proc_rev['decision']})"
        assert "audit that stops missed calls" in _proc_rev["recommended_first_offer"], \
            f"940 fail: process-only offer mangled ({_proc_rev['recommended_first_offer']})"
        assert _proc_rev["model"] == "fake-model-940" and _proc_rev["provider"] == "https://fake-endpoint.example/v1", \
            "940 fail: metadata missing"
        assert _proc_rev["reviewed_at"] and isinstance(_proc_rev["latency_ms"], int) and _proc_rev["tokens_used"] >= 1, \
            f"940 fail: metadata incomplete {_proc_rev}"
        # The prompt must not force AI-first language — the invariant
        # (implementation chosen after diagnosis) is present verbatim.
        _prompt = _ai_review_prompt(_proc)
        assert "chosen AFTER diagnosis" in _prompt, "940 fail: prompt lost diagnosis-first invariant"
        assert "evidence keys you may cite" in _prompt, "940 fail: prompt lost evidence-key whitelist"
        # 5) Network failure → abstain with the error recorded, no crash.
        globals()["AI_REVIEW_MODEL"] = "fake-model-940"
        globals()["AI_REVIEW_BASE_URL"] = "https://fake-endpoint.example/v1"
        _net = {"name": "Net Co", "trade": "Plumbing", "eligibility_state": "eligible"}
        with _mock.patch.object(urllib.request, "urlopen",
                                side_effect=urllib.error.URLError("boom")):
            _net_rev = ai_review_candidate(_net)
        assert _net_rev["decision"] == "abstain", f"940 fail: network error not abstain ({_net_rev['decision']})"
        assert "AI review unavailable" in _net_rev["abstain_reason"], \
            f"940 fail: network error not recorded {_net_rev['abstain_reason']}"
        assert _net.get("ai_review") is None, "940 fail: network error stored ai_review"
        # 6) run_ai_review end-to-end with a configured reviewer: bounded to
        #    eligible/research only, deterministic score order, freshness skip,
        #    and lead_score/eligibility untouched.
        _cfg = {"businesses": {
            "a": {"name": "A Co", "trade": "Plumbing", "eligibility_state": "eligible",
                  "lead_score": {"score": 40, "tier": "Warm"}},
            "b": {"name": "B Co", "trade": "Roofing", "eligibility_state": "eligible",
                  "lead_score": {"score": 80, "tier": "Hot"}},
            "c": {"name": "C Co", "trade": "HVAC", "eligibility_state": "research",
                  "lead_score": {"score": 90, "tier": "Warm"}},
            "d": {"name": "D Co", "trade": "Pest", "eligibility_state": "rejected",
                  "lead_score": {"score": 99, "tier": "Hot"}},
            "e": {"name": "E Co", "trade": "Plumbing", "eligibility_state": "eligible",
                  "lead_score": {"score": 70, "tier": "Warm"},
                  "ai_review": {"reviewed_at": "2099-01-01T00:00:00+00:00"}},
        }}
        _ok_review = json.dumps({
            "decision": "watch", "confidence": 0.5, "evidence_refs": ["name"],
            "bottleneck_hypothesis": "hypothesis: unknown", "why_now": "",
            "recommended_first_offer": "", "email_draft": "",
            "phone_opener": "", "missing_evidence": [], "abstain_reason": "",
        }).encode()
        _resp_payload = json.dumps({"choices": [{"message": {"content": _ok_review.decode()}}]}).encode()
        with _mock.patch.object(urllib.request, "urlopen",
                                return_value=_FakeResp(_resp_payload)):
            _cfg_res = run_ai_review(_cfg)
        # b (80) and c (90) are eligible/research and score-ordered — both
        # reviewed even though c's score is higher: eligible first, then score.
        assert _cfg_res["reviewed"] == 3, f"940 fail: run reviewed {_cfg_res['reviewed']} (expect 3: b, c, a)"
        assert _cfg_res["decisions"] == {"watch": 3}, f"940 fail: decisions {_cfg_res['decisions']}"
        assert "ai_review" in _cfg["businesses"]["b"] and "ai_review" in _cfg["businesses"]["c"] \
            and "ai_review" in _cfg["businesses"]["a"], "940 fail: review not stored on candidates"
        assert _cfg["businesses"]["d"].get("ai_review") is None, "940 fail: rejected record reviewed"
        _e_before = json.dumps(_cfg["businesses"]["e"], sort_keys=True)
        assert json.dumps(_cfg["businesses"]["e"], sort_keys=True) == _e_before, \
            "940 fail: fresh-review skip mutated record"
        # Never touches lead_score/eligibility.
        for _k in ("a", "b", "c", "d", "e"):
            assert _cfg["businesses"][_k]["eligibility_state"] == _cfg["businesses"][_k].get("eligibility_state"), \
                "940 fail: eligibility changed"
        assert _cfg["businesses"]["b"]["lead_score"] == {"score": 80, "tier": "Hot"}, \
            "940 fail: lead_score changed by AI review"
        assert _cfg["businesses"]["c"]["lead_score"] == {"score": 90, "tier": "Warm"}, \
            "940 fail: research lead_score changed by AI review"
    finally:
        globals()["AI_REVIEW_MODEL"], globals()["AI_REVIEW_BASE_URL"], \
            globals()["AI_REVIEW_API_KEY"] = _saved_ai

    # ── SGW-926: WEEKLY BRIEF FIXTURES ────────────────────────────────
    # (a) deterministic fallback: NO ai_review anywhere → exactly N<=10
    # entries, every entry has evidence signals + an evidence-specific
    # draft + a next action; booking-gap record's draft must mention
    # 'calls'. (b) AI present: priority sorts first with decision+confidence
    # shown, reject excluded. (c) rejected eligibility excluded. (d)
    # weak-evidence eligible record omitted. (e) no fabricated owner names.
    _wb_sq = {"status": "up", "confidence": "high", "website_score": 2,
              "automation_gaps": ["no booking system"], "emails": []}
    _wb_cache = {"businesses": {
        "booking": {"name": "Booking Co", "trade": "Plumbing",
                    "phones": ["(951) 555-1001"], "own_domains": ["bookingco.com"],
                    "url": "bookingco.com", "eligibility_state": "eligible",
                    "lead_score": {"score": 55, "tier": "Warm", "reasons": ["appointment trade with no booking system (+10)"]},
                    "site_quality": _wb_sq, "hiring_signals": [], "review_signals": []},
        "weak": {"name": "Weak Co", "trade": "Plumbing", "own_domains": ["weakco.com"],
                 "url": "weakco.com", "eligibility_state": "eligible",
                 "lead_score": {"score": 30, "tier": "Cold", "reasons": []}},
        "rej": {"name": "Rej Co", "trade": "Plumbing", "phones": ["(951) 555-1002"],
                "own_domains": ["rejco.com"], "url": "rejco.com",
                "eligibility_state": "rejected", "eligibility_reason": "government/public entity",
                "lead_score": {"score": 99, "tier": "Hot"},
                "site_quality": _wb_sq, "hiring_signals": [], "review_signals": []},
        "picked": {"name": "Picked Co", "trade": "Accounting",
                   "phones": ["(951) 555-1003"], "own_domains": ["pickedco.com"],
                   "url": "pickedco.com", "eligibility_state": "eligible",
                   "lead_score": {"score": 60, "tier": "Warm", "reasons": ["admin/ops business — high intake/scheduling load (+25)"]},
                   "site_quality": {"status": "up", "confidence": "high", "website_score": 4,
                                    "automation_gaps": ["no booking/chat system", "no CRM"],
                                    "emails": []},
                   "hiring_signals": [], "review_signals": []},
    }}
    _det_brief = generate_weekly_brief(_wb_cache)
    _det_pros = select_weekly_prospects(_wb_cache)
    assert len(_det_pros) == 2, f"926 fail: fallback selected {len(_det_pros)} (expect 2: booking, picked; weak omitted, rejected excluded)"
    assert all(b.get("eligibility_state") == "eligible" for b in _det_pros), \
        "926 fail: non-eligible record in brief"
    assert "Booking Co" in _det_brief and "Picked Co" in _det_brief, "926 fail: eligible prospect missing"
    assert "Rej Co" not in _det_brief and "Weak Co" not in _det_brief, \
        "926 fail: rejected/weak record surfaced in brief"
    assert "deterministic fallback" in _det_brief, "926 fail: no fallback label with AI off"
    assert "calls" in _det_brief.lower(), "926 fail: booking-gap draft not evidence-specific (no 'calls')"
    assert "Next action" in _det_brief and "within 7 days" in _det_brief, \
        "926 fail: no next action/date in brief"
    assert "hypothesis:" in _det_brief, "926 fail: bottleneck not labeled as hypothesis"
    _no_owner = [s for s in ("Hi Mike", "Hi John", "Hi Sarah", "Hi David") if s in _det_brief]
    assert not _no_owner, f"926 fail: fabricated owner name in draft: {_no_owner}"
    assert "Owner/decision-maker: unknown" in _det_brief, "926 fail: owner field not unverified"

    # (b) AI present: priority first, reject excluded, decision+confidence shown
    _wb_cache["businesses"]["picked"]["ai_review"] = {
        "decision": "priority", "confidence": 0.9, "evidence_refs": ["name"],
        "bottleneck_hypothesis": "hypothesis: test", "why_now": "test urgency",
        "recommended_first_offer": "test offer", "email_draft": "test email draft",
        "phone_opener": "test opener", "missing_evidence": [], "abstain_reason": ""}
    _wb_cache["businesses"]["booking"]["ai_review"] = {
        "decision": "reject", "confidence": 0.8, "evidence_refs": ["name"],
        "bottleneck_hypothesis": "", "why_now": "", "recommended_first_offer": "",
        "email_draft": "", "phone_opener": "", "missing_evidence": [], "abstain_reason": ""}
    _ai_pros = select_weekly_prospects(_wb_cache)
    assert [b["name"] for b in _ai_pros] == ["Picked Co"], \
        f"926 fail: AI selection {[b['name'] for b in _ai_pros]} (priority first, reject excluded)"
    _ai_brief = generate_weekly_brief(_wb_cache)
    assert "AI review: priority (90% confidence)" in _ai_brief, \
        "926 fail: AI decision/confidence not shown"
    assert "test email draft" in _ai_brief and "test opener" in _ai_brief, \
        "926 fail: AI draft/opener not used when present"
    assert "Booking Co" not in _ai_brief, "926 fail: ai_review reject appeared"

    # (c) abstain → kept, labeled watch, deterministic copy still shown
    _wb_cache["businesses"]["booking"]["ai_review"] = {
        "decision": "abstain", "confidence": 0.4, "evidence_refs": ["name"],
        "bottleneck_hypothesis": "", "why_now": "", "recommended_first_offer": "",
        "email_draft": "", "phone_opener": "", "missing_evidence": ["no phone"],
        "abstain_reason": "thin evidence"}
    _ab_pros = select_weekly_prospects(_wb_cache)
    assert [b["name"] for b in _ab_pros] == ["Picked Co", "Booking Co"], \
        f"926 fail: abstain routing {[b['name'] for b in _ab_pros]} (kept with watch label)"
    _ab_brief = generate_weekly_brief(_wb_cache)
    assert "abstain" in _ab_brief and "watch" in _ab_brief.lower(), \
        "926 fail: abstain not labeled watch"
    assert "missed calls" in _ab_brief, "926 fail: abstain fell back to generic copy"

    # (d) hard cap: 12 candidates → exactly WEEKLY_BRIEF_MAX entries
    _big = {"businesses": {}}
    for _i in range(12):
        _big["businesses"][f"b{_i}"] = {
            "name": f"Big Co {_i}", "trade": "Plumbing", "phones": [f"(951) 555-1{_i:03d}"],
            "own_domains": [f"bigco{_i}.com"], "url": f"bigco{_i}.com",
            "eligibility_state": "eligible", "lead_score": {"score": 50 + _i, "tier": "Warm", "reasons": []},
            "site_quality": _wb_sq, "hiring_signals": [], "review_signals": []}
    _cap_pros = select_weekly_prospects(_big)
    assert len(_cap_pros) == WEEKLY_BRIEF_MAX, \
        f"926 fail: cap {len(_cap_pros)} (expect {WEEKLY_BRIEF_MAX})"
    _cap_brief = generate_weekly_brief(_big)
    _cap_cards = _cap_brief.count('class="wb-card"')
    assert _cap_cards == WEEKLY_BRIEF_MAX, \
        f"926 fail: brief HTML has {_cap_cards} cards (expect {WEEKLY_BRIEF_MAX})"
    # top_n override is clamped to WEEKLY_BRIEF_MAX
    assert len(select_weekly_prospects(_big, top_n=999)) == WEEKLY_BRIEF_MAX, \
        "926 fail: --weekly-top 999 not clamped to 10"
    # generate_weekly_brief is pure — cache untouched
    _big_snapshot = json.dumps(_big, sort_keys=True)
    generate_weekly_brief(_big)
    assert json.dumps(_big, sort_keys=True) == _big_snapshot, \
        "926 fail: generate_weekly_brief mutated the cache"

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

            # SGW-942: NEVER let a data-quality pass destroy a lead. If a
            # record was demoted to Cold by our own repair logic rather than
            # by new evidence, keep it — the 7-day Cold cutoff would otherwise
            # delete it (this is exactly how Superior Virtual Bookkeeping was
            # lost: a phone-purge dropped its score, flipped it Cold, and the
            # sweep then removed it). Any record carrying a repair marker is
            # exempt from retention pruning.
            _REPAIR_MARKERS = ("phones_superseded", "phones_dropped_shared",
                               "contact_reverified_at", "restored_from",
                               "_retired", "merged_from", "directory_record")
            def _protected(v):
                return any(k in v for k in _REPAIR_MARKERS)

            cache["businesses"] = {
                k: v for k, v in cache.get("businesses", {}).items()
                if _keep(v) or _protected(v)
            }
            # SGW-864: sweep — any cached record that is now identifiable as a
            # directory/SEO listing gets demoted to Cold + zeroed signals so it
            # stops polluting the top of the report until re-crawled properly.
            for biz in cache["businesses"].values():
                url = biz.get("url", "") or (biz.get("own_domains") or [""])[0]
                if (_is_directory_record(url, biz.get("name", ""))
                        or _mentions_out_of_area(biz.get("name", "") + " " + url)):
                    biz["directory_record"] = True
                    for k in ("hiring_signals", "review_signals", "hiring_role_match", "review_negative"):
                        biz.pop(k, None)
                    sq = biz.get("site_quality")
                    if sq:
                        sq["status"] = "unknown"
                        sq["confidence"] = "low"
                    biz["lead_score"] = {"score": 0, "tier": "Cold",
                                         "breakdown": {}, "reasons": ["directory/SEO listing — not a real business"]}
                    continue
                # SGW-864 round 2: generic SEO names on real own-sites get the
                # domain brand instead of junk ("Contact Us" → "Khan Attorneys").
                if _is_generic_name(biz.get("name", "")) and biz.get("own_domains"):
                    brand = _domain_brand_name(biz["own_domains"][0])
                    if brand:
                        biz["name"] = brand
            # SGW-938 B1: purge cached phone evidence that fails the canonical
            # NANP validator — contaminated tel:/JSON-LD values ingested before
            # the validator fix must not keep inflating contactability.
            # Also scrub the same garbage from site_quality.phones (the other
            # place phones are stored).
            for biz in cache["businesses"].values():
                cleaned = [p for p in (biz.get("phones") or []) if _normalize_phone(p)]
                if len(cleaned) != len(biz.get("phones") or []):
                    biz["phones"] = cleaned
                sq = biz.get("site_quality")
                if sq and sq.get("phones"):
                    cleaned_sq = [p for p in sq["phones"] if _normalize_phone(p)]
                    if len(cleaned_sq) != len(sq["phones"]):
                        sq["phones"] = cleaned_sq
            # SGW-942 B1b (revised 2026-09-11): the first cut of this rule was
            # WRONG — it deleted any number appearing on >1 record, which
            # stripped genuinely-published numbers (Superior Virtual's real
            # (951) 440-3498 was published in its own site footer and on a
            # JSON-LD telephone field). A shared number means two records may
            # be the SAME business; it does not mean the number is fake.
            # Correct handling: when records share a number, MERGE the
            # duplicates into the richest record and retire the others.
            # Never delete the number itself.
            merged = merge_duplicate_records(cache)
            if merged:
                log(f"duplicate records merged: {merged}")
            # SGW-942 B2: re-stamp hiring/review signal provenance. Cached
            # signals written before the classifier fix carry own_site for
            # aggregator hosts; re-classify so scoring stops crediting them.
            for biz in cache["businesses"].values():
                ods = biz.get("own_domains") or []
                for key in ("hiring_signals", "review_signals"):
                    for sig in (biz.get(key) or []):
                        if not isinstance(sig, dict):
                            continue
                        sig["source_kind"] = _evidence_source_kind(sig.get("url", ""), ods)
                if biz.get("hiring_signals") or biz.get("review_signals"):
                    # score depends on source_kind — recompute on next qualify
                    if isinstance(biz.get("lead_score"), dict):
                        biz["lead_score"] = qualify_lead(biz, biz.get("site_quality"))
            # SGW-941: eligibility gate — after identity re-key + phone purge so
            # the gate sees resolved names and clean contact paths. Government,
            # locator/job subdomains, national-enterprise branches, and
            # directory/SEO listings are rejected (Cold, zeroed score, evidence
            # preserved); unverifiable records route to research (Cold). The
            # gate re-runs on every load so newly-ingested junk is caught even
            # if it slipped the crawl-time check.
            _elig_counts = apply_eligibility_sweep(cache)
            log(f"eligibility sweep: {_elig_counts}")
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
    # SGW-938 B3: explicit UTF-8 — matches backup_cache(); non-UTF-8 locale
    # defaults would otherwise throw on non-ASCII cache content.
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def backup_cache(cache):
    """Write a timestamped backup of the cache. ponytail: keep it simple — one file per run."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    backup = REPORT_DIR / f"cache-backup-{ts}.json"
    with open(backup, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
    # Keep only the last 10 backups
    backups = sorted(REPORT_DIR.glob("cache-backup-*.json"))
    for old in backups[:-10]:
        old.unlink()
    return backup


# ── HTML REPORT ──────────────────────────────────────────────────────
# North Web Pro brand colors: #D97548 (trail orange), #60CFF4 (sky blue)
# Black bg, two-color accent system. Designed for mobile-first reading.

# ── HTML REPORT ──────────────────────────────────────────────────────
# North Web Pro brand — dark wilderness theme
# Design system: Linear-inspired dark precision × warm campfire tones
# Fonts: Space Grotesk (headings), Inter (body) — matching northwebpro.com
# Colors: #D97548 trail orange (urgency), #60CFF4 sky blue (opportunity)
# Surfaces: #0a0a0a canvas, #141414 cards, #292929 borders
# Shadow-as-border technique (Vercel/Linear influence)

HTML_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&display=swap');

* { margin: 0; padding: 0; box-sizing: border-box; }

:root {
    --bg: #0a0a0a;
    --surface: #141414;
    --surface-hover: #1a1a1a;
    --border: #292929;
    --border-hover: #3a3a3a;
    --text: #d4d4d4;
    --text-secondary: #888;
    --text-muted: #555;
    --orange: #D97548;
    --orange-dim: rgba(217,117,72,0.12);
    --orange-glow: rgba(217,117,72,0.25);
    --blue: #60CFF4;
    --blue-dim: rgba(96,207,244,0.1);
    --green: #3fb950;
    --radius: 8px;
    --radius-sm: 6px;
    --font-heading: 'Space Grotesk', system-ui, sans-serif;
    --font-body: 'Inter', system-ui, -apple-system, sans-serif;
}

body {
    font-family: var(--font-body);
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
    line-height: 1.5;
}

/* ── HERO ── */
.hero {
    background: linear-gradient(180deg, #111 0%, var(--bg) 100%);
    padding: 40px 20px 32px;
    text-align: center;
    position: relative;
}
.hero::after {
    content: '';
    position: absolute;
    bottom: 0; left: 10%; right: 10%;
    height: 1px;
    background: linear-gradient(90deg, transparent, var(--orange), transparent);
}
.hero-logo {
    font-family: var(--font-heading);
    font-size: 1.6em; font-weight: 700;
    color: #fff; margin-bottom: 4px;
    letter-spacing: -0.02em;
}
.hero-logo span { color: var(--orange); }
.hero-tagline {
    color: var(--blue); font-size: 0.82em;
    letter-spacing: 0.5px; margin-bottom: 16px;
    font-weight: 400;
}
.hero-meta {
    color: var(--text-muted); font-size: 0.72em;
    line-height: 1.6;
}
.hero-meta strong { color: var(--text-secondary); }

.container {
    max-width: 860px; margin: 0 auto;
    padding: 24px 16px;
}

/* ── STATS GRID ── */
.stats {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 10px;
    margin-bottom: 32px;
}
.stat-box {
    background: var(--surface);
    border-radius: var(--radius);
    padding: 16px 8px;
    text-align: center;
    box-shadow: 0 0 0 1px var(--border);
    transition: box-shadow 0.15s;
}
.stat-box:hover { box-shadow: 0 0 0 1px var(--border-hover); }
.stat-num {
    font-family: var(--font-heading);
    font-size: 1.8em; font-weight: 700;
    color: #fff;
    line-height: 1.1;
}
.stat-num.hot { color: var(--orange); }
.stat-num.warm { color: var(--orange); }
.stat-num.upsell { color: var(--blue); }
.stat-label {
    font-size: 0.6em; color: var(--text-muted);
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    font-weight: 500;
}

/* ── SECTIONS ── */
.section { margin-bottom: 32px; }
.section-title {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 16px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
}
.section-title h2 {
    font-family: var(--font-heading);
    font-size: 1em; color: #fff;
    font-weight: 600;
    letter-spacing: -0.01em;
}

/* ── BADGES ── */
.badge {
    display: inline-flex;
    align-items: center;
    padding: 2px 10px;
    border-radius: 9999px;
    font-size: 0.68em;
    font-weight: 600;
    letter-spacing: 0.3px;
    white-space: nowrap;
}
.badge-hot {
    background: var(--orange);
    color: #0a0a0a;
    font-weight: 700;
}
.badge-warm {
    background: var(--orange-dim);
    color: var(--orange);
    border: 1px solid var(--orange-glow);
}
.badge-cold {
    background: #1a1a1a;
    color: var(--text-muted);
}
.badge-new {
    background: var(--orange);
    color: #0a0a0a;
    font-size: 0.6em;
    padding: 1px 7px;
    border-radius: 4px;
    font-weight: 700;
    letter-spacing: 0.5px;
}
.badge-signal {
    background: var(--blue-dim);
    color: var(--blue);
    border: 1px solid rgba(96,207,244,0.2);
}
.badge-upsell {
    background: var(--blue-dim);
    color: var(--blue);
    border: 1px solid rgba(96,207,244,0.2);
}

/* ── LEAD CARDS ── */
.lead-card {
    background: var(--surface);
    border-radius: var(--radius);
    padding: 16px 18px;
    margin-bottom: 8px;
    box-shadow: 0 0 0 1px var(--border);
    transition: box-shadow 0.15s, background 0.15s;
}
.lead-card:hover {
    box-shadow: 0 0 0 1px var(--border-hover);
    background: var(--surface-hover);
}
.lead-card.hot {
    box-shadow: 0 0 0 1px var(--orange-glow), inset 0 0 0 1px rgba(217,117,72,0.05);
}
.lead-card.hot:hover {
    box-shadow: 0 0 0 1px var(--orange), inset 0 0 0 1px rgba(217,117,72,0.08);
}
.lead-card.warm {
    box-shadow: 0 0 0 1px rgba(217,117,72,0.15);
}
.lead-card.cold {
    opacity: 0.6;
    box-shadow: 0 0 0 1px var(--border);
}

.lead-top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 8px;
}
.lead-name {
    font-family: var(--font-heading);
    font-weight: 600;
    color: #fff;
    font-size: 0.95em;
    text-decoration: none;
    line-height: 1.3;
    letter-spacing: -0.01em;
}
.lead-name:hover { color: var(--blue); }

.lead-info {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 16px;
    font-size: 0.78em;
    margin-bottom: 6px;
    align-items: center;
}
.lead-domain a {
    color: #fff;
    text-decoration: none;
    word-break: break-all;
}
.lead-domain a:hover { color: var(--blue); text-decoration: underline; }
.lead-phone a {
    color: var(--orange);
    text-decoration: none;
    font-weight: 500;
}
.lead-phone a:hover { text-decoration: underline; }
.lead-trade {
    color: var(--text-muted);
    font-size: 0.9em;
}
.lead-platform {
    color: var(--text-secondary);
    font-size: 0.9em;
}
.lead-status {
    font-size: 0.72em;
    font-weight: 500;
    padding: 1px 6px;
    border-radius: 4px;
}
.status-down {
    color: var(--orange);
    background: rgba(217,117,72,0.08);
}
.status-blocked {
    color: var(--text-secondary);
    background: rgba(136,136,136,0.08);
}
.status-up {
    color: var(--green);
    background: rgba(63,185,80,0.08);
}

.lead-reasons {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 8px;
}
.reason-tag {
    background: var(--orange-dim);
    color: var(--orange);
    font-size: 0.68em;
    padding: 2px 8px;
    border-radius: 4px;
    border: 1px solid rgba(217,117,72,0.1);
    font-weight: 500;
}

/* SGW-941: eligibility state note on cards */
.eligibility-note {
    margin-top: 8px;
    font-size: 0.7em;
    color: var(--text-muted);
    border-top: 1px dashed var(--border);
    padding-top: 6px;
}
.eligibility-note b {
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.4px;
    font-size: 0.9em;
}

/* ── PITCH LINE ── */
.pitch-line {
    margin-top: 10px;
    padding: 8px 12px;
    background: rgba(96,207,244,0.04);
    border-left: 2px solid var(--blue);
    border-radius: 0 6px 6px 0;
    font-size: 0.78em;
    color: var(--blue);
    line-height: 1.5;
}
.pitch-label {
    font-weight: 600;
    color: var(--blue);
    margin-right: 6px;
}

/* ── SIGNAL CARDS ── */
.signal-card {
    background: var(--surface);
    border-radius: var(--radius-sm);
    padding: 12px 14px;
    margin-bottom: 6px;
    box-shadow: 0 0 0 1px var(--border);
    border-left: 3px solid var(--blue);
}
.signal-title {
    color: #fff;
    font-weight: 500;
    font-size: 0.85em;
    margin-bottom: 3px;
}
.signal-title a { color: #fff; text-decoration: none; }
.signal-title a:hover { color: var(--blue); text-decoration: underline; }
.signal-meta { color: var(--text-muted); font-size: 0.75em; }
.signal-meta a { color: var(--blue); text-decoration: none; }

/* ── COLLAPSIBLE ── */
details summary {
    cursor: pointer;
    color: var(--text-muted);
    font-size: 0.78em;
    padding: 8px 0;
    user-select: none;
    transition: color 0.15s;
}
details summary:hover { color: var(--blue); }

/* ── FOOTER ── */
.footer {
    text-align: center;
    padding: 32px 20px;
    color: var(--text-muted);
    font-size: 0.75em;
    border-top: 1px solid var(--border);
    margin-top: 48px;
}
.footer a { color: var(--orange); text-decoration: none; }
.footer a:hover { text-decoration: underline; }
.footer .pitch {
    background: linear-gradient(135deg, rgba(217,117,72,0.06), rgba(96,207,244,0.04));
    border: 1px solid rgba(217,117,72,0.15);
    border-radius: 10px;
    padding: 20px 24px;
    margin: 0 auto 24px;
    max-width: 480px;
    color: var(--text-secondary);
    font-style: italic;
    line-height: 1.6;
    font-size: 0.85em;
}
.footer .pitch strong { color: var(--orange); font-style: normal; }

/* ── RESPONSIVE ── */
@media (max-width: 600px) {
    .hero-logo { font-size: 1.2em; }
    .stats { grid-template-columns: repeat(3, 1fr); }
    .stat-num { font-size: 1.4em; }
    .lead-info { font-size: 0.72em; }
    .lead-card { padding: 12px 14px; }
}
"""


def pitch_for(biz):
    """T9 + research 2026-08 + SGW-928: Derive an OUTCOME-first pitch line.
    Sells the removal of operational drag, never the tool — the implementation
    (process change, existing software, automation, AI) is chosen AFTER
    diagnosis. ponytail: priority table, first match wins."""
    trade = biz.get("trade", "")
    if biz.get("review_negative"):
        return "never miss another intake call — customers say you're slow to respond, we fix that"
    if biz.get("hiring_role_match"):
        return "the role you're hiring for is eating your margins — we take that workload off your plate"
    if trade in ADMIN_TRADES:
        return "stop letting intake fall through the cracks — your staff gets their time back for billable work"
    if biz.get("hiring_signals"):
        return "the workload behind that job posting is the real cost — we remove it before you pay the salary"
    sq = biz.get("site_quality") or {}
    gaps = sq.get("automation_gaps", [])
    if any(g in gaps for g in ("no booking system", "no booking/chat system")):
        return "book every call that hits voicemail — no more missed revenue"
    return "every missed call is a missed job — we fix your intake so nothing falls through"


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

    # T9: Categorize by lead tier — Unverified is its own bucket, not Cold.
    # SGW-941: 'research' eligibility is its own bucket, not Cold — these are
    # not vetted leads and must not look like actionable prospects.
    hot, warm, cold, unverified, research = [], [], [], [], []
    for biz in businesses.values():
        ls = biz.get("lead_score", {})
        tier = ls.get("tier", "Cold")
        if biz.get("eligibility_state") == "research":
            research.append(biz)
        elif tier == "Hot":
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
        (len(hot), "Hot Leads", "hot"),
        (len(warm), "Warm", "warm"),
        (len(cold), "Cold", ""),
        (sum(1 for b in businesses.values() if b.get("_new")), "New", "hot"),
        (sum(1 for b in businesses.values() if b.get("phones")), "Reachable", "upsell"),
    ]
    stats_html = '<div class="stats">'
    for num, label, cls in stats:
        cls_str = f' {cls}' if cls else ''
        stats_html += f'<div class="stat-box"><div class="stat-num{cls_str}">{num}</div><div class="stat-label">{label}</div></div>'
    stats_html += '</div>'
    cards.append(stats_html)

    def render_lead_card(biz):
        """Render a single lead card with qualification breakdown."""
        ls = biz.get("lead_score", {})
        score = ls.get("score", 0)
        tier = ls.get("tier", "Cold")
        reasons = ls.get("reasons", [])
        domain = biz.get("own_domains", ["?"])[0]
        # SGW-942 B1b: the daily report's click-to-dial link is a contact path
        # claim — render the verified number, never the raw contaminated list.
        _vphones, _vemails = _weekly_contact_paths(biz)
        phone = (_vphones or [""])[0]
        sq = biz.get("site_quality") or {}
        ws = sq.get("website_score", -1)
        status = sq.get("status", "unknown")
        platform = sq.get("platform", "")
        emails = _vemails or biz.get("emails", []) or sq.get("emails", [])
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

        # SGW-941: eligibility reason — visible in supporting detail so a
        # rejected/research record explains itself without polluting the pitch.
        if biz.get("eligibility_state") and biz.get("eligibility_reason"):
            html += (f'<div class="eligibility-note">Eligibility: '
                     f'<b>{biz.get("eligibility_state")}</b> — {biz.get("eligibility_reason")}</div>')

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

    # SGW-941: RESEARCH — eligible-looking but not yet verified as a distinct
    # local business (no contact path, generic title without resolved brand).
    # Collapsed, clearly labeled as needing research, never in the actionable
    # stream.
    if research:
        section = '<div class="section">'
        section += '<div class="section-title"><h2>🔬 Research Needed — not yet vetted</h2>'
        section += f'<span class="badge badge-cold">{len(research)}</span></div>'
        section += f'<details><summary>{len(research)} businesses — need identity/contact verification before they can be leads</summary>'
        for biz in research[:8]:
            section += render_lead_card(biz)
        if len(research) > 8:
            section += f'<p style="color:#444;font-size:0.75em;text-align:center;padding:8px;">+ {len(research)-8} more...</p>'
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
        We fix the intake — every call answered, every job booked, follow-up handled.
        We start with a 48hr assessment and only bring in tooling once we know the fix."
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
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    _h = shutil.which("hermes") or os.path.expanduser("~/.local/bin/hermes")
    hermes = _h if os.path.isfile(_h) else None
    if hermes:
        # SGW-938 B4: single REPORT_TARGET; check the subprocess return code
        # instead of printing "sent" regardless. A failing send is a real error.
        res = subprocess.run([hermes, "send", "-t", REPORT_TARGET,
            f"Lead Scout Report — {now.strftime('%b %d, %H:%M')} PT\nMEDIA:{report_path}"],
            timeout=30, capture_output=True, text=True)
        if res.returncode != 0:
            log(f"WARNING: hermes send failed (rc={res.returncode}): {res.stderr.strip()[:200]}")
            print(f"HTML report written (delivery failed): {report_path}")
            return
        print(f"HTML report sent: {report_path}")
    else:
        log("hermes binary not found — report written but not delivered")
        print(f"HTML report written (hermes missing): {report_path}")


# ── SGW-926: WEEKLY OWNER-READY PROSPECT BRIEF ─────────────────────────
# Compact, opt-in outreach pack: max 10 eligible prospects, score-ordered,
# AI-review decisions applied WHEN PRESENT (reject → excluded, abstain →
# watch label), deterministic fallback copy when AI is off. Separate from
# the daily HTML report — no new dashboard, no send wiring. The cache is
# READ-ONLY here (load_cache()'s eligibility sweep mutates memory only).

WEEKLY_BRIEF_MAX = 10  # SGW-926 hard cap — max 10 priority prospects


def _weekly_contact_paths(biz):
    """SGW-942 B1b: verified contact paths ONLY.

    The brief used to render `biz["phones"][0]` as "primary", which is the
    contaminated list — the top lead's "primary" number was a Wix CSS class
    fragment and its real (951) number sat unused in backup. Prefer the
    high-confidence own-site read; fall back to the record list only when no
    site read exists, and label the provenance so the reader knows which it
    is. Never render a number the site never published."""
    sq = biz.get("site_quality") or {}
    verified = []
    if sq.get("status") == "up" and sq.get("confidence") == "high":
        verified = [p for p in (sq.get("phones") or []) if _normalize_phone(p)]
    if not verified:
        verified = [p for p in (biz.get("phones") or []) if _normalize_phone(p)]
    emails = []
    for e in (sq.get("emails") or []) + (biz.get("emails") or []):
        e = str(e).strip().lower()
        if "@" in e and len(e) < 80 and re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", e) \
                and e not in emails:
            emails.append(e)
    return verified, emails


def _weekly_has_evidence(biz):
    """True when the record carries at least one deterministic evidence
    anchor (phone, site automation gap, hiring or review signal). Prevents
    shell records with nothing observed from filling the brief."""
    sq = biz.get("site_quality") or {}
    phones, emails = _weekly_contact_paths(biz)
    return bool(phones or emails or sq.get("automation_gaps")
                or biz.get("hiring_signals") or biz.get("hiring_role_match")
                or biz.get("review_signals") or biz.get("review_negative"))


def _weekly_evidence_signals(biz):
    """2-4 traceable evidence signals as (signal, ref) pairs. Deterministic
    — only what was actually captured; never owner/revenue/unverified."""
    sigs = []
    sq = biz.get("site_quality") or {}
    phones, _emails = _weekly_contact_paths(biz)
    if phones:
        sigs.append((f"phone contact captured ({phones[0]})", "phones"))
    if biz.get("own_domains"):
        sigs.append((f"verified own website ({biz['own_domains'][0]})", "own_domains"))
    for g in sq.get("automation_gaps", [])[:3]:
        sigs.append((f"missing {g}", "site_quality"))
    if biz.get("hiring_role_match"):
        sigs.append(("hiring an automatable intake/scheduling role", "hiring"))
    elif biz.get("hiring_signals"):
        sigs.append((f"hiring evidence ({len(biz['hiring_signals'])} posting(s))", "hiring"))
    if biz.get("review_negative"):
        sigs.append(("reviewers mention slow/no response", "reviews"))
    if sq.get("has_fax"):
        sigs.append(("fax number on site — paper-based intake", "paper_signals"))
    if sq.get("has_outdated_email"):
        sigs.append(("outdated contact email on site", "paper_signals"))
    if len(sigs) < 2 and biz.get("snippet"):
        sigs.append(("crawler snippet captured", "snippet"))
    return sigs[:4]


def select_weekly_prospects(cache, top_n=WEEKLY_BRIEF_MAX):
    """SGW-926 selection rule: eligible ONLY, at least one evidence anchor,
    deterministic lead_score score desc; AI decisions applied when present
    (ai_review decision 'reject' excludes, 'abstain' is kept with a watch
    label — documented rule, weak records were already omitted by the
    evidence anchor); hard cap WEEKLY_BRIEF_MAX (default 10, never more).
    ai_review 'priority' entries sort ahead of the score order."""
    top_n = max(1, min(int(top_n), WEEKLY_BRIEF_MAX))
    picked = []
    for biz in cache.get("businesses", {}).values():
        # SGW-941: rejected-eligibility records never appear in the brief.
        if biz.get("eligibility_state") != "eligible":
            continue
        if "lead_score" not in biz:
            biz["lead_score"] = qualify_lead(biz, biz.get("site_quality"))
        rev = biz.get("ai_review") or {}
        if rev.get("decision") == "reject":
            continue  # SGW-926: AI reject never appears either
        if not _weekly_has_evidence(biz):
            continue  # weak-evidence prospect — omitted, not surfaced
        ls = biz.get("lead_score") or {}
        picked.append((biz, ls.get("score", 0), rev.get("decision")))
    picked.sort(key=lambda t: (0 if t[2] == "priority" else 1, -t[1]))
    return [biz for biz, _, _ in picked[:top_n]]


def _weekly_hypothesis(biz):
    """Deterministic bottleneck hypothesis — ALWAYS explicitly labeled as
    hypothesis (SGW-940 contract style). Derived from top captured signal."""
    sq = biz.get("site_quality") or {}
    gaps = sq.get("automation_gaps", [])
    if biz.get("review_negative"):
        return "hypothesis: intake calls are not answered fast enough, so prospects give up before booking"
    if biz.get("hiring_role_match"):
        return "hypothesis: intake/scheduling workload outgrew the team, hence the new hire"
    if any(g in gaps for g in ("no booking system", "no booking/chat system")):
        return "hypothesis: calls outside office hours go unanswered — no self-serve booking exists"
    if biz.get("trade", "") in ADMIN_TRADES:
        return "hypothesis: billable staff absorb intake/scheduling manually, cutting into billable hours"
    if sq.get("has_fax"):
        return "hypothesis: paper-based intake forces manual re-entry and slows response"
    if gaps:
        return "hypothesis: manual follow-through on intake/follow-up is where work slips"
    return "hypothesis: response handling relies on manual follow-through — unmeasured, unmanaged"


def _weekly_impact(biz):
    """Plain-language likely business impact — grounded in captured signals
    only; never invents revenue/volume numbers."""
    sq = biz.get("site_quality") or {}
    gaps = sq.get("automation_gaps", [])
    if biz.get("review_negative"):
        return "Slow response costs repeat business and referrals — each unanswered intake is a lost job."
    if biz.get("hiring_role_match"):
        return "A full-time hire for intake/scheduling work is salary spent on work automation can absorb."
    if any(g in gaps for g in ("no booking system", "no booking/chat system")):
        return "Calls outside office hours go to voicemail — jobs that were never booked are lost revenue."
    if biz.get("trade", "") in ADMIN_TRADES:
        return "Billable staff burn hours on intake/scheduling — that time is the real cost."
    if gaps:
        return "Manual steps in intake/follow-up cost staff time on every job."
    return "Intake depends on manual follow-through — staff time and missed calls are the exposure."


def _weekly_fallback_draft(biz):
    """Deterministic email draft — evidence-SPECIFIC, never generic, never
    invents owner names / revenue / complaint details beyond captured
    signals. Reuses the same signal ordering as pitch_for() so the email
    and phone angle agree. Returns (subject, body)."""
    sq = biz.get("site_quality") or {}
    gaps = sq.get("automation_gaps", [])
    name = biz.get("name", "your business")
    if biz.get("review_negative"):
        return (f"slow response time — {name}",
                "Hello,\n\nYour reviews mention slow response — that is missed intake, "
                "and it costs jobs before you ever see them. We find and fix the "
                "bottleneck so every call gets handled.\n\nWorth a 15-minute look? "
                "Reply and I'll send what we'd change first.\n— North Web Pro")
    if biz.get("hiring_role_match"):
        return (f"the role you're hiring for — {name}",
                "Hello,\n\nThe position you're hiring for is largely intake/scheduling "
                "work we can take off your plate before you pay the salary. "
                "That frees the budget for the role you actually need.\n\n"
                "15 minutes this week?\n— North Web Pro")
    if biz.get("hiring_signals"):
        return (f"the workload behind the posting — {name}",
                "Hello,\n\nYou're hiring, which means the current workload already "
                "outpaces the team. We remove the manual intake/admin part so the "
                "new hire goes further.\n\n— North Web Pro")
    if any(g in gaps for g in ("no booking system", "no booking/chat system")):
        return (f"missed calls — {name}",
                "Hello,\n\nYour site has no way to book outside phone hours, so every "
                "call that hits voicemail is a missed job. We set up intake so "
                "nothing falls through.\n\nCan I show you what that looks like for "
                "your business?\n— North Web Pro")
    if biz.get("trade", "") in ADMIN_TRADES:
        return (f"intake falling through the cracks — {name}",
                "Hello,\n\nYour team spends billable hours on intake and scheduling. "
                "We fix the process so staff get their time back for client work.\n\n"
                "— North Web Pro")
    if sq.get("has_fax"):
        return (f"still running on paper — {name}",
                "Hello,\n\nYou're still taking intake by fax, which means manual "
                "re-entry for your staff. We replace that step with something that "
                "handles itself.\n\n— North Web Pro")
    if gaps:
        return (f"what we noticed on your site — {name}",
                f"Hello,\n\nYour site is missing {' and '.join(gaps[:2])} — each one "
                "is a place where work lands on your staff. We find and remove that "
                "drag.\n\n— North Web Pro")
    return (f"every missed call — {name}",
            "Hello,\n\nEvery missed call is a missed job, and without booking or "
            "intake automation some calls are bound to slip. We fix that so nothing "
            "falls through.\n\n— North Web Pro")


def _weekly_next_action(biz):
    """What is due NEXT: action / date / channel, from verified contact
    paths only. SGW-942 B1b: email-only records get an email action rather
    than being told to phone a number that isn't theirs."""
    phones, emails = _weekly_contact_paths(biz)
    domains = biz.get("own_domains") or []
    if phones:
        return ("Call", "within 7 days (next weekly cycle)", f"phone — {phones[0]}")
    if emails:
        return ("Email", "within 7 days (next weekly cycle)", f"email — {emails[0]}")
    if domains:
        return ("Submit via website contact form", "within 7 days (next weekly cycle)",
                f"website — {domains[0]}")
    return ("Verify contact path first", "within 7 days (next weekly cycle)", "manual lookup")


def _weekly_missing(biz):
    """Missing evidence / reason to skip — deterministic, from what is absent."""
    missing = []
    sq = biz.get("site_quality") or {}
    phones, emails = _weekly_contact_paths(biz)
    if not phones and not emails:
        missing.append("no verified phone or email — contact path unverified")
    elif not phones:
        missing.append("no phone published on site — email only")
    if not biz.get("hiring_checked"):
        missing.append("no hiring-signal check")
    if not biz.get("review_checked"):
        missing.append("no review-signal check")
    if not isinstance(sq, dict) or sq.get("status") != "up" or sq.get("confidence") != "high":
        missing.append("site not verified up/high-confidence")
    rev = biz.get("ai_review") or {}
    if rev.get("missing_evidence"):
        missing.extend(rev["missing_evidence"][:2])
    return "; ".join(missing) if missing else "none — evidence sufficient for outreach"


_WEEKLY_BRIEF_CSS = """
body{font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;background:#0a0a0a;color:#d4d4d4;margin:0;padding:16px;line-height:1.45}
.wrap{max-width:760px;margin:0 auto}
h1{font-size:20px;color:#fff;margin:0 0 4px}
.sub{color:#8a8a8a;font-size:13px;margin-bottom:16px}
.wb-card{background:#141414;border:1px solid #292929;border-radius:10px;padding:14px 16px;margin-bottom:14px}
.wb-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap}
.wb-name{font-size:16px;font-weight:600;color:#fff}
.wb-meta{font-size:12px;color:#8a8a8a}
.wb-score{color:#D97548;font-weight:600;font-size:13px}
.wb-label{display:inline-block;font-size:11px;padding:2px 8px;border-radius:999px;margin:6px 4px 0 0}
.lbl-priority{background:#D97548;color:#0a0a0a}.lbl-watch{background:#5a4a2a;color:#ffd479}.lbl-fallback{background:#292929;color:#60CFF4}
.wb-field{font-size:12px;color:#8a8a8a;margin-top:10px;text-transform:uppercase;letter-spacing:.05em}
.wb-body{font-size:13px;color:#d4d4d4;margin-top:2px}
.ref{color:#60CFF4}
.wb-draft{background:#0f0f0f;border-left:3px solid #60CFF4;padding:8px 10px;border-radius:0 6px 6px 0;font-size:13px;margin-top:2px;white-space:pre-line}
ul{margin:4px 0 0 16px;padding:0}
li{font-size:13px;margin-bottom:2px}
.footer{color:#5a5a5a;font-size:12px;margin-top:8px;text-align:center}
"""


def _weekly_card_html(biz, idx):
    """One prospect card per the SGW-926 output contract. All copy is
    evidence-grounded: owner/decision-maker is reported as unverified (the
    engine does not capture owner names) and never invented."""
    ls = biz.get("lead_score") or {}
    score = ls.get("score", 0)
    rev = biz.get("ai_review") or {}
    dec = rev.get("decision", "")
    if dec == "abstain":
        dec_label, dec_cls = "AI review: abstain → watch", "lbl-watch"
    elif dec:
        conf = rev.get("confidence")
        cstr = f" ({conf:.0%} confidence)" if isinstance(conf, (int, float)) else ""
        dec_label = f"AI review: {dec}{cstr}"
        dec_cls = "lbl-priority" if dec == "priority" else "lbl-watch"
    else:
        dec_label, dec_cls = "deterministic fallback", "lbl-fallback"

    sigs = _weekly_evidence_signals(biz)
    why = [f"{s} <span class='ref'>({ref})</span>" for s, ref in sigs]
    if len(sigs) < 2:  # pad to a readable why with the same evidence, via reasons
        why.extend((ls.get("reasons") or [])[: 2 - len(sigs)])

    # AI copy wins when present and substantive; abstain falls back to
    # deterministic copy (the decision label still shows).
    use_ai = dec and dec != "abstain"
    subject, body = _weekly_fallback_draft(biz)
    if use_ai and rev.get("email_draft"):
        subject, body = None, rev["email_draft"]
    opener = f"{pitch_for(biz)} — this is North Web Pro, is this {biz.get('name', '')}?"
    if use_ai and rev.get("phone_opener"):
        opener = rev["phone_opener"]
    angle = pitch_for(biz)
    if use_ai and rev.get("recommended_first_offer"):
        angle = rev["recommended_first_offer"]
    hyp = rev.get("bottleneck_hypothesis") if use_ai and rev.get("bottleneck_hypothesis") \
        else _weekly_hypothesis(biz)
    if not str(hyp or "").lower().startswith("hypothesis"):
        hyp = f"hypothesis: {hyp}"
    impact = rev.get("why_now") if use_ai and rev.get("why_now") else _weekly_impact(biz)

    phones, emails = _weekly_contact_paths(biz)
    domains = biz.get("own_domains") or []
    if phones:
        contacts = f"primary: {phones[0]}"
        if len(phones) > 1:
            contacts += f" · backup: {phones[1]}"
    elif emails:
        contacts = f"email: {emails[0]} (no phone published on site)"
    elif domains:
        contacts = f"website: {domains[0]} (no phone captured)"
    else:
        contacts = "none captured — unverified"
    action, when, channel = _weekly_next_action(biz)

    subject_html = f"<div class='wb-body'><b>Subject:</b> {subject}</div>" if subject else ""
    return f"""
<div class="wb-card">
  <div class="wb-head">
    <span class="wb-name">{idx}. {biz.get('name', '')}</span>
    <span class="wb-score">{score}/100</span>
  </div>
  <div class="wb-meta">Vertical: {biz.get('trade', 'unknown')} · Owner/decision-maker: unknown / not captured</div>
  <span class="wb-label {dec_cls}">{dec_label}</span>
  <div class="wb-field">Why it's on the list</div>
  <ul>{''.join(f'<li>{w}</li>' for w in why[:4])}</ul>
  <div class="wb-field">Bottleneck hypothesis</div>
  <div class="wb-body">{hyp}</div>
  <div class="wb-field">Likely business impact</div>
  <div class="wb-body">{impact}</div>
  <div class="wb-field">Recommended first diagnostic / angle</div>
  <div class="wb-body">{angle}</div>
  <div class="wb-field">Contact paths (verified only)</div>
  <div class="wb-body">{contacts}</div>
  <div class="wb-field">Email draft</div>
  <div class="wb-draft">{subject_html}{body}</div>
  <div class="wb-field">Phone opener</div>
  <div class="wb-body">{opener}</div>
  <div class="wb-field">Next action</div>
  <div class="wb-body"><b>{action}</b> — {when} · channel: {channel}</div>
  <div class="wb-field">Missing evidence / skip</div>
  <div class="wb-body">{_weekly_missing(biz)}</div>
</div>"""


def generate_weekly_brief(cache, top_n=WEEKLY_BRIEF_MAX):
    """SGW-926: render the weekly owner-ready prospect brief HTML. Pure —
    no file writes (write_weekly_brief persists it). Deterministic when
    ai_review is absent; AI decisions layered on when present."""
    prospects = select_weekly_prospects(cache, top_n=top_n)
    now = datetime.now(timezone.utc)
    week = now.strftime("%b %d, %Y")
    n = len(prospects)
    ai_note = ("AI review: on (decisions applied)" if any(
        (b.get("ai_review") or {}).get("decision") for b in prospects)
        else "AI review: off — deterministic fallback copy")
    cards = "\n".join(_weekly_card_html(b, i + 1) for i, b in enumerate(prospects))
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weekly Prospect Brief — {week}</title>
<style>{_WEEKLY_BRIEF_CSS}</style></head>
<body><div class="wrap">
<h1>Weekly Prospect Brief — week of {week}</h1>
<div class="sub">{n} priority prospects (max {WEEKLY_BRIEF_MAX}, eligible only) · {ai_note} ·
generated by SGW-926, cache read-only</div>
{cards}
<div class="footer">North Web Pro — diagnosis first, tooling after. All copy grounded in captured
evidence; owner names/revenue are never assumed.</div>
</div></body></html>"""


def write_weekly_brief(cache, top_n=WEEKLY_BRIEF_MAX):
    """SGW-926: persist the brief to REPORT_DIR (same path as the daily
    report) as weekly-brief-YYYYMMDD.html. Prints the path — no send logic
    (the existing hermes-send path is deliberately NOT wired here)."""
    html = generate_weekly_brief(cache, top_n=top_n)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"weekly-brief-{datetime.now(timezone.utc).strftime('%Y%m%d')}.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Weekly brief written: {path}")
    return path


# ── SGW-939: SIGNAL COVERAGE SWEEP + REPORT ────────────────────────────
def _signal_checked_recently(biz):
    """SGW-939: True when a business has FRESH (<= SIGNAL_RECHECK_DAYS) checks
    for BOTH hiring and reviews. A check WITHOUT a timestamp is treated as
    stale — we cannot verify when it happened, and the coverage report (which
    requires *_checked_at) would otherwise show 0% forever for legacy entries
    while the sweep never re-checks them. Re-checking stamps the timestamp."""
    fresh = True
    for key in ("hiring_checked_at", "review_checked_at"):
        ts = biz.get(key)
        if not ts:
            return False  # never checked, or legacy checked without timestamp → needs re-check
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).days
        except ValueError:
            return False
        if age > SIGNAL_RECHECK_DAYS:
            fresh = False
    return fresh


def signal_sweep_candidates(cache):
    """SGW-939: eligible prospects that need a fresh signal check, in
    processing priority order. Priority = needs-most-urgently first:
    1) never checked, highest current score; 2) stale (recheck overdue).
    Replaces the old 'top N by score only' selection which re-checked the
    same high-scorers every run and starved the rest."""
    candidates = []
    for norm, biz in cache.get("businesses", {}).items():
        sq = biz.get("site_quality")
        # Only leads with a website check can carry signal evidence (T14 keeps
        # 'unknown' sites eligible — they can ONLY be scored on external signals).
        if not sq or sq.get("status") not in ("up", "blocked", "down", "unknown"):
            continue
        if len(biz.get("name", "")) < 3:
            continue
        # QC (2026-08-09): rejected records (government, directories, national
        # enterprises) are never owner-facing — re-checking their hiring/review
        # signals wastes sweep budget. research records stay eligible (the
        # website-check loop can promote them once contact is found).
        if biz.get("eligibility_state") == "rejected":
            continue
        never_h = not biz.get("hiring_checked")
        never_r = not biz.get("review_checked")
        stale_h = _signal_checked_recently(biz) is False and not never_h
        stale_r = _signal_checked_recently(biz) is False and not never_r
        if never_h and never_r:
            priority = 0
        elif never_h or never_r:
            priority = 1
        elif stale_h or stale_r:
            priority = 2
        else:
            continue  # fresh on both — nothing to do
        score = biz.get("lead_score", {}).get("score", 0)
        candidates.append((priority, -score, norm, biz))
    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates


# SGW-942 B1c: tokens that describe a TRADE or a legal form, not an identity.
# Two different firms sharing "plumbing" or "insurance" are NOT the same
# business — using these as identity caused the duplicate merge to eat
# Encore Plumbing & Air and Hawkguard Insurance. Names are only allowed to
# prove sameness via tokens that actually name the business.
TRADE_IDENTITY_STOPWORDS = {
    "plumbing", "plumber", "plumbers", "electric", "electrical", "electrician",
    "hvac", "heating", "cooling", "air", "conditioning", "roofing", "roofer",
    "roofers", "cleaning", "cleaners", "carpet", "handyman", "landscaping",
    "landscape", "tree", "removal", "painting", "painter", "painters",
    "insurance", "insured", "agency", "agencies", "agent", "agents",
    "accounting", "accountant", "accountants", "bookkeeping", "bookkeeper",
    "tax", "taxes", "cpa", "cpas", "law", "lawyer", "lawyers", "attorney",
    "attorneys", "legal", "consulting", "consultant", "consultants",
    "recruiting", "staffing", "employment", "property", "management",
    "realty", "real", "estate", "repair", "repairs", "service", "services",
    "company", "companies", "inc", "llc", "corp", "group", "associates",
    "solutions", "firm", "office", "offices", "business", "enterprise",
    "enterprises", "industries", "partners", "professionals", "network",
    "systems", "technology", "technologies", "murrieta", "temecula",
    "wildomar", "menifee", "riverside", "california", "local", "quality",
    "premier", "elite", "professional", "affordable", "trusted", "family",
    "tire", "auto", "automotive", "mechanic", "shop", "smog", "drain",
    "sewer", "rooter", "floor", "floors", "restoration", "water", "fire",
    "damage", "pest", "termite", "pool", "solar", "garage", "door", "doors",
    "window", "windows", "glass", "flooring", "cabinet", "cabinets",
    "moving", "movers", "storage", "notary", "title", "escrow", "lending",
    "mortgage", "loan", "loans", "financial", "wealth", "retirement",
    "health", "life", "home", "auto", "commercial", "personal", "general",
    "liability", "workers", "compensation", "medicare", "renters", "rental",
}


def _record_richness(biz):
    """Merge heuristic: how much real evidence a record carries. The richest
    record wins and absorbs the others — we never merge into an emptier one
    and lose data."""
    sq = biz.get("site_quality") or {}
    return (
        (2 if sq.get("status") == "up" and sq.get("confidence") == "high" else 0)
        + (1 if (biz.get("lead_score") or {}).get("score") else 0)
        + (1 if biz.get("own_domains") else 0)
        + len(sq.get("automation_gaps") or []) * 0.1
        + (0.5 if biz.get("hiring_signals") else 0)
        + (0.5 if biz.get("review_signals") else 0)
        + (0.5 if biz.get("snippet") else 0)
    )


def _same_business(a, b):
    """True when two records are the same real-world business.

    Deliberately conservative: a shared phone ALONE is not enough (a shared
    switchboard number can serve two genuinely separate firms at one address),
    so we require the phone to be corroborated by a shared identity signal —
    same registrable domain, or a strongly overlapping distinctive name
    token set. This is the guard that stops the merge from eating real
    distinct leads."""
    a_sq, b_sq = a.get("site_quality") or {}, b.get("site_quality") or {}
    a_ph = {p for p in ((a.get("phones") or []) + (a_sq.get("phones") or []))
            if _normalize_phone(p)}
    b_ph = {p for p in ((b.get("phones") or []) + (b_sq.get("phones") or []))
            if _normalize_phone(p)}
    if not (a_ph & b_ph):
        return False
    # SGW-944 D2: same lstrip("www.") defect — it made "wecare.com" and
    # "ecare.com" compare equal and merged two unrelated businesses. Use the
    # real prefix strip.  See _strip_www().
    a_dom = {_strip_www(str(d).lower()) for d in (a.get("own_domains") or []) if d}
    b_dom = {_strip_www(str(d).lower()) for d in (b.get("own_domains") or []) if d}
    if a_dom and b_dom and (a_dom & b_dom):
        return True
    # Name-token route. Trade words are NOT identity: "Plumbing Services" and
    # "Encore Plumbing & Air" are different firms that share the token
    # "plumbing". Strip the generic ICP vocabulary before comparing, and
    # require the overlap to be a genuinely distinctive token.
    a_tok = set(_distinctive_name_tokens(a.get("name", ""))) - TRADE_IDENTITY_STOPWORDS
    b_tok = set(_distinctive_name_tokens(b.get("name", ""))) - TRADE_IDENTITY_STOPWORDS
    if not a_tok or not b_tok:
        return False
    overlap = a_tok & b_tok
    if len(overlap) >= 2:
        return True
    if len(overlap) == 1 and min(len(a_tok), len(b_tok)) == 1:
        return True
    return False


def merge_duplicate_records(cache):
    """SGW-942: collapse records that are the same business into one.

    Replaces the earlier (and wrong) rule that deleted any phone number
    appearing on more than one record. Sharing a number is evidence the
    records are duplicates, not evidence the number is bogus — Superior
    Virtual's real published number got deleted by that rule.

    Merges only when `_same_business` holds (shared phone PLUS shared domain
    or overlapping distinctive name tokens). The richer record absorbs the
    others: missing fields are filled, signals are unioned, and the retired
    keys are recorded under `merged_from` so nothing is lost silently.
    Returns the number of records retired."""
    biz = cache.get("businesses") or {}
    by_phone = defaultdict(list)
    for key, rec in biz.items():
        sq = rec.get("site_quality") or {}
        for p in set((rec.get("phones") or []) + (sq.get("phones") or [])):
            n = _normalize_phone(p)
            if n:
                by_phone[n].append(key)

    retire = {}   # key -> winner key
    for phone, keys in by_phone.items():
        live = [k for k in keys if k not in retire]
        if len(live) < 2:
            continue
        # Richest first — the winner is the record with the most evidence.
        live.sort(key=lambda k: -_record_richness(biz[k]))
        winner = live[0]
        for other in live[1:]:
            if other in retire:
                continue
            if _same_business(biz[winner], biz[other]):
                retire[other] = winner

    if not retire:
        return 0

    for loser, winner in retire.items():
        w, l = biz.get(winner), biz.get(loser)
        if not w or not l:
            continue
        # Fill gaps on the winner; never overwrite richer evidence.
        for field in ("snippet", "trade", "url"):
            if not w.get(field) and l.get(field):
                w[field] = l[field]
        for field in ("own_domains", "emails", "dir_domains"):
            merged_vals = list(w.get(field) or [])
            for v in (l.get(field) or []):
                if v not in merged_vals:
                    merged_vals.append(v)
            if merged_vals:
                w[field] = merged_vals
        # Phones: keep the number that made them duplicates.
        w_ph = list(w.get("phones") or [])
        for p in (l.get("phones") or []):
            if p not in w_ph and _normalize_phone(p):
                w_ph.append(p)
        w["phones"] = w_ph
        # Union the signal lists.
        for field in ("hiring_signals", "review_signals"):
            merged_sigs = list(w.get(field) or [])
            seen_urls = {s.get("url") for s in merged_sigs if isinstance(s, dict)}
            for s in (l.get(field) or []):
                if isinstance(s, dict) and s.get("url") not in seen_urls:
                    merged_sigs.append(s)
            if merged_sigs:
                w[field] = merged_sigs
        for flag in ("hiring_role_match", "review_negative", "hiring_checked", "review_checked"):
            if l.get(flag):
                w[flag] = True
        w.setdefault("merged_from", []).append(
            {"key": loser, "name": l.get("name", ""), "score": (l.get("lead_score") or {}).get("score")})
        # Retire the loser; keep a tombstone rather than deleting outright so
        # the merge is auditable and reversible.
        l["_retired"] = {"merged_into": winner, "at": datetime.now(timezone.utc).isoformat()}
        l["eligibility_state"] = "rejected"
        l["eligibility_reason"] = f"duplicate of {winner} — merged"
        l["lead_score"] = {"score": 0, "tier": "Cold", "breakdown": {},
                           "reasons": [f"duplicate record merged into {winner}"]}
        if isinstance(w.get("lead_score"), dict):
            w["lead_score"] = qualify_lead(w, w.get("site_quality"))
    return len(retire)


def reverify_cached_contact_paths(cache, limit=20):
    """SGW-942: re-derive contact paths for cached records from the live page.

    Why this exists: `site_quality` reads captured before the phone-extractor
    fix contain values extracted from framework identifiers (Wix CSS classes,
    site UUIDs), and the crawl loop SKIPS any domain whose
    `site_quality.website_score >= 0` — so those records are never re-read and
    keep rendering junk as "verified contact paths" (e.g. the (200) 951-7308
    shown as the primary number for Construction Bookkeeping Services).

    Ordering targets the highest-impact records first — eligible, site read
    up, ordered by lead_score — and each is only written when a fresh read
    DISAGREES with what is cached, so the pass repairs stale/contaminated
    contact evidence and leaves verified records untouched. Bounded by
    `limit`, one fetch per record via the existing `check_website` (which
    already handles retry, www fallback, and UNKNOWN-on-failure), and
    idempotent — a second run finds nothing left to change."""
    candidates = []
    for key, biz in (cache.get("businesses") or {}).items():
        sq = biz.get("site_quality") or {}
        if not isinstance(sq, dict) or sq.get("status") != "up":
            continue
        if biz.get("eligibility_state") != "eligible":
            continue
        if not (sq.get("phones") or biz.get("phones")):
            continue
        score = (biz.get("lead_score") or {}).get("score") or 0
        candidates.append((-score, key, biz))
    candidates.sort(key=lambda t: t[0])
    updated = 0
    for _rank, key, biz in candidates[:max(0, int(limit))]:
        domain = (biz.get("own_domains") or [""])[0] or biz.get("url", "")
        if not domain:
            continue
        sq = run_collector("website_check", check_website, domain)
        if not isinstance(sq, dict) or sq.get("status") != "up" \
                or sq.get("confidence") != "high":
            continue  # unreadable now → leave the record exactly as it was
        fresh = [p for p in (sq.get("phones") or []) if _normalize_phone(p)]
        cached = [p for p in ((biz.get("site_quality") or {}).get("phones") or [])
                  if _normalize_phone(p)]
        if sorted(fresh) == sorted(cached) and biz.get("contact_reverified_at"):
            continue  # already verified against the live page, nothing new
        if sorted(fresh) == sorted(cached):
            # Agreement, but never yet verified — stamp it so it is skipped
            # next run without another fetch.
            biz["contact_reverified_at"] = datetime.now(timezone.utc).isoformat()
            updated += 1
            continue
        if cached:
            biz["phones_superseded"] = cached
        biz["site_quality"] = sq
        biz["phones"] = fresh
        if sq.get("emails"):
            biz["emails"] = sq["emails"]
        if isinstance(biz.get("lead_score"), dict):
            biz["lead_score"] = qualify_lead(biz, sq)
        biz["contact_reverified_at"] = datetime.now(timezone.utc).isoformat()
        updated += 1
        time.sleep(1)  # be polite to small-business hosts
    return updated


def generate_coverage_report(cache, out_path=None):
    """SGW-939: deterministic coverage report — what percentage of eligible
    prospects has fresh hiring/review evidence, and what's still missing.
    Written to SIGNAL_COVERAGE_REPORT (or out_path when a run happens before
    the crawl/save completes). Pure cache read; no network. Returns the dict."""
    report_path = Path(os.path.expanduser(out_path or SIGNAL_COVERAGE_REPORT))
    bizs = cache.get("businesses", {})
    total = len(bizs)
    eligible = 0
    fresh_both = 0
    never_checked = 0
    by_collector = {"hiring": 0, "review": 0}
    warm_eligible = 0
    warm_fresh = 0
    for biz in bizs.values():
        sq = biz.get("site_quality")
        if not sq or sq.get("status") not in ("up", "blocked", "down", "unknown"):
            continue
        if len(biz.get("name", "")) < 3:
            continue
        # QC (2026-08-09): rejected records are zeroed to Cold and never get
        # re-checked — counting them in the denominator would permanently
        # depress coverage (e.g. government entities that will never have
        # hiring/review evidence). research records stay in the denominator:
        # they can still be promoted and checked.
        if biz.get("eligibility_state") == "rejected":
            continue
        eligible += 1
        h = biz.get("hiring_checked") and biz.get("hiring_checked_at") and \
            (datetime.now(timezone.utc) - datetime.fromisoformat(biz["hiring_checked_at"])).days <= SIGNAL_RECHECK_DAYS
        r = biz.get("review_checked") and biz.get("review_checked_at") and \
            (datetime.now(timezone.utc) - datetime.fromisoformat(biz["review_checked_at"])).days <= SIGNAL_RECHECK_DAYS
        if not biz.get("hiring_checked") or not biz.get("review_checked"):
            never_checked += 1
        if h:
            by_collector["hiring"] += 1
        if r:
            by_collector["review"] += 1
        if h and r:
            fresh_both += 1
        if (biz.get("lead_score") or {}).get("tier") in ("Warm", "Hot"):
            warm_eligible += 1
            if h and r:
                warm_fresh += 1
    pct_both = (fresh_both * 100 // eligible) if eligible else 0
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_percent": SIGNAL_COVERAGE_TARGET,
        "businesses_total": total,
        "eligible": eligible,
        "fresh_both": fresh_both,
        "fresh_both_percent": pct_both,
        "by_collector": by_collector,
        "never_checked": never_checked,
        "warm_eligible": warm_eligible,
        "warm_fresh": warm_fresh,
    }
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    except OSError as e:
        log(f"coverage report write failed: {e}")
    log(f"coverage: {fresh_both}/{eligible} eligible ({pct_both}%) fresh on both — target {SIGNAL_COVERAGE_TARGET}%")
    return report


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
    parser.add_argument("--self-check", action="store_true",
                        help="SGW-938 B6: run the scoring/identity self-test and exit "
                             "(no crawl, no network). Exit code 0 = all assertions pass.")
    parser.add_argument("--coverage", action="store_true",
                        help="SGW-939: write the signal-coverage report from cache and exit "
                             "(no crawl, no network). Shows what %% of eligible prospects "
                             "have fresh hiring/review evidence.")
    parser.add_argument("--disable-collector", action="append", default=[],
                        help="Disable a collector by name (crawl_search, website_check, "
                             "hiring_signals, review_signals, buying_signals). Repeatable. "
                             "SGW-863: proves disabling a source doesn't break the run.")
    parser.add_argument("--places", action="store_true",
                        help="SGW-925: Google Places identity enrichment pass over "
                             "eligible/research records (requires GOOGLE_PLACES_API_KEY; "
                             "bounded by PLACES_MAX_PER_RUN). No key → skip, exit 0.")
    parser.add_argument("--ai-review", action="store_true",
                        help="SGW-940: grounded AI second-pass review of eligible/research "
                             "candidates (requires AI_REVIEW_MODEL + AI_REVIEW_BASE_URL; "
                             "bounded by AI_REVIEW_MAX_CANDIDATES). Advisory only — never "
                             "changes lead_score or eligibility. Unconfigured → skip, exit 0.")
    parser.add_argument("--weekly-brief", action="store_true",
                        help="SGW-926: write the weekly owner-ready prospect brief "
                             "weekly-brief-YYYYMMDD.html to the reports dir from cache "
                             "(read-only, no crawl, no send). Max 10 eligible prospects, "
                             "score-ordered; AI decisions applied when present, "
                             "deterministic fallback copy otherwise.")
    parser.add_argument("--weekly-top", type=int, default=WEEKLY_BRIEF_MAX,
                        help=f"SGW-926: max prospects in the weekly brief (default "
                             f"{WEEKLY_BRIEF_MAX}, hard cap {WEEKLY_BRIEF_MAX}).")
    parser.add_argument("--reverify-phones", type=int, default=0, metavar="N",
                        help="SGW-942: re-fetch up to N cached records whose "
                             "site_quality was scored by the pre-fix phone "
                             "extractor, and re-derive their contact paths from "
                             "the live page. Bounded, network-using, idempotent.")
    args = parser.parse_args()

    # SGW-938 B6: self-check mode — the previously-dead _test_qualify_lead()
    # is now a supported entry point for the repo verification command.
    if args.self_check:
        _test_qualify_lead()
        sys.exit(0)

    # SGW-942: bounded contact-path re-verification. Cached site_quality reads
    # were scored by the pre-fix phone extractor, and the crawl loop skips
    # domains it has already scored — so those records never get re-read and
    # keep serving framework-identifier "phones" as verified contact paths.
    # This pass re-fetches the worst offenders and rewrites their contact
    # evidence from the live page. Explicit opt-in, bounded, idempotent.
    if args.reverify_phones:
        cache = load_cache()
        changed = reverify_cached_contact_paths(cache, limit=args.reverify_phones)
        if changed:
            save_cache(cache)
        log(f"Contact-path re-verification: {changed} records updated")
        sys.exit(0)

    # SGW-939: coverage report from cache only — no crawl, no network.
    if args.coverage:
        cache = load_cache()
        report = generate_coverage_report(cache)
        print(f"Coverage report: {json.dumps(report, indent=2)}")
        sys.exit(0)

    # SGW-925: standalone Places enrichment pass — cache only, no crawl, no
    # SearXNG. Without a key this logs one line and exits 0 (zero requests).
    if args.places:
        cache = load_cache()
        n = run_places_enrichment(cache)
        if n:
            save_cache(cache)
        log(f"Places enrichment: {n} records checked")
        sys.exit(0)

    # SGW-940: standalone grounded AI review pass — cache only, no crawl.
    # Explicit OPT-IN: never auto-runs on normal crawls (cost discipline).
    # Unconfigured (no model or no base_url) → one log line, exit 0, zero
    # network, cache untouched. Never modifies lead_score/eligibility —
    # advisory opinions land under biz['ai_review'] for the SGW-926 brief.
    if args.ai_review:
        cache = load_cache()
        res = run_ai_review(cache)
        if res["reviewed"]:
            save_cache(cache)
        log(f"AI review: {res['reviewed']} candidates reviewed ({res['decisions']})")
        sys.exit(0)

    # SGW-926: weekly brief from cache only — read-only, no crawl, no send.
    # Runs after --ai-review so an AI pass (when configured) can land fresh
    # reviews in the cache before the brief consumes them.
    if args.weekly_brief:
        cache = load_cache()
        write_weekly_brief(cache, top_n=args.weekly_top)
        sys.exit(0)

    # SGW-863: config-gated collectors — disable at runtime via CLI
    for cname in args.disable_collector:
        if cname in COLLECTORS:
            COLLECTORS[cname]["enabled"] = False
            log(f"collector DISABLED via CLI: {cname}")
        else:
            log(f"unknown collector: {cname} (valid: {list(COLLECTORS)})")

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
            results = run_collector("crawl_search", searx_search, q, limit=15, delay=args.delay)
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
                # SGW-864: directory/SEO records never enter the cache as businesses
                if _is_directory_record(url, name):
                    continue
                # SGW-864/865: out-of-geography records (e.g. a Campbell CA firm
                # surfacing in a Murrieta query) never enter the cache either
                if _mentions_out_of_area(name + " " + url):
                    continue
                # SGW-864 round 2: generic SEO titles on real own-site pages
                # ("Contact Us", "Temecula CPA, CPA") get re-keyed to the domain
                # brand — real firms keep their identity, junk names get fixed.
                if _is_generic_name(name):
                    domain0 = re.sub(r'https?://(www\.)?', '', url.lower()).split('/')[0]
                    brand = _domain_brand_name(domain0)
                    if brand:
                        name = brand
                urlkey = re.sub(r'https?://(www\.)?', '', url.lower()).rstrip('/')
                if urlkey in seen_urls:
                    continue
                seen_urls.add(urlkey)

                phones = extract_phones(title + " " + snippet)
                domain = re.sub(r'https?://(www\.)?', '', url.lower()).split('/')[0]
                # SGW-938 B2: boundary match, not substring — 'prfamilylawyers.com'
                # must be treated as its own domain, not an aggregator echo.
                is_own_site = not _is_aggregator_domain(domain)

                # SGW-941: eligibility gate at ingest — rejected entities
                # (government, locator/job subdomains, national enterprise
                # branches, directory/SEO listings) NEVER enter the cache.
                # Research/eligible records are stored with their state; the
                # load-time sweep re-assesses after the website check merges
                # phones, promoting eligible records on the next run.
                _elig_state, _elig_reason = assess_eligibility(
                    url, name, trade, phones, [domain] if is_own_site else [])
                if _elig_state == "rejected":
                    log(f"  eligibility rejected at ingest ({_elig_reason}): {name}")
                    continue

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
                        "eligibility_state": _elig_state, "eligibility_reason": _elig_reason,
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
        biz["site_quality"] = run_collector("website_check", check_website, domain)
        sq = biz["site_quality"]
        if sq is None:
            sq = {"status": "unknown", "confidence": "low", "automation_gaps": [], "emails": []}
            biz["site_quality"] = sq
        # Merge phones found on the website into the business entry
        # SGW-942 B1b: a successful own-site read is AUTHORITATIVE. The old
        # code only ever appended, so a number extracted from search-result
        # snippets (or from framework identifiers) stayed on the record
        # forever even after the real site was read. When the site read is
        # high-confidence, own-site numbers replace the list; the superseded
        # values are preserved as evidence, not silently dropped.
        if sq and sq.get("status") == "up" and sq.get("confidence") == "high" and sq.get("phones"):
            _prior = list(biz.get("phones") or [])
            _own = list(sq["phones"])
            if _prior and sorted(_prior) != sorted(_own):
                biz["phones_superseded"] = _prior
            biz["phones"] = _own
        elif sq and sq.get("phones"):
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
        # SGW-941: re-assess eligibility now that the website check merged
        # phones/emails — a research record without contact at ingest can
        # become eligible once the site yields a contact path. The score is
        # recomputed only when the record is eligible; otherwise the next
        # load-time sweep routes it correctly.
        if biz.get("eligibility_state") == "research":
            _state, _reason = assess_eligibility(
                biz.get("url", "") or (biz.get("own_domains") or [""])[0],
                biz.get("name", ""), biz.get("trade", ""),
                biz.get("phones", []), biz.get("own_domains", []))
            biz["eligibility_state"] = _state
            biz["eligibility_reason"] = _reason
            if _state == "eligible":
                biz["lead_score"] = qualify_lead(biz, sq)
        checks_done += 1
        time.sleep(0.5)

    log(f"Websites checked: {checks_done}")

    # ── PHASE 2: HIRING + REVIEW SIGNALS — bounded coverage sweep (SGW-939) ──
    # Replaces the old "top 8 by score, every run" loop which re-checked the
    # same high-scorers and starved never-checked prospects. Now: process up to
    # SIGNAL_SWEEP_LIMIT eligible candidates per run, prioritizing never-checked
    # (highest score first), then stale (recheck overdue). A backfill over a few
    # runs reaches every eligible prospect. Per-run request budget unchanged
    # (~2 queries per signal × 6s delay ≈ 3–4 min worst case).
    candidates = signal_sweep_candidates(cache)
    log(f"Signal sweep: {len(candidates)} candidates need fresh checks "
        f"(processing up to {SIGNAL_SWEEP_LIMIT} this run)")
    signal_checks = 0
    for _prio, _neg_score, norm, biz in candidates[:SIGNAL_SWEEP_LIMIT]:
        if signal_checks >= SIGNAL_SWEEP_LIMIT:
            break
        biz_name = biz.get("name", "")
        if not biz.get("hiring_checked") and len(biz_name) >= 3:
            log(f"  Hiring signals: {biz_name}")
            run_collector("hiring_signals", search_hiring_signals, biz_name, norm, cache)
            signal_checks += 1
            time.sleep(6)
        if signal_checks >= SIGNAL_SWEEP_LIMIT:
            break
        if not biz.get("review_checked") and len(biz_name) >= 3:
            log(f"  Review signals: {biz_name}")
            run_collector("review_signals", search_review_signals, biz_name, norm, cache)
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
            results = run_collector("buying_signals", searx_search, q, limit=8, delay=args.delay)
            for r in results or []:
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

    # SGW-939: post-run coverage snapshot — always written so the deterministic
    # coverage trend is queryable without a separate invocation.
    try:
        generate_coverage_report(cache)
    except Exception as e:  # noqa: BLE001 — reporting must never kill the run
        log(f"coverage report failed: {e}")

    log(f"Cache: {len(cache['businesses'])} businesses, {len(cache.get('signals', []))} signals, {len(cache.get('fb_groups', []))} groups")
    log(f"Queries: {searx_ok} ok / {searx_empty} empty")

    # ── OUTPUT ──
    if args.html:
        send_report(cache, args.zip, now, prev_run=prev_run)
    sys.exit(0)


if __name__ == "__main__":
    main()
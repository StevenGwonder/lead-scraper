#!/usr/bin/env python3
"""SGW-945: test the engine's marker tables for SUBSTRING false positives.

_detect_markers() does a bare `marker in html_lower`, so any marker that is a
common substring of ordinary words will fire on pages that have no such tool.
This is the same defect class as the lstrip("www.") bug: a careless string
operation producing confident nonsense.

Each suspicious marker is tested against realistic English words and against the
live top-10 pages, so a false positive is demonstrated, not theorised.
"""
import re
import ssl
import urllib.request
import importlib.util
import sys

spec = importlib.util.spec_from_file_location(
    "p", "/home/steven/lead-scraper-SGW-944/local-biz-92562.py")
m = importlib.util.module_from_spec(spec)
sys.modules["p"] = m
spec.loader.exec_module(m)

MARKERS = {
    "CRM": m.CRM_MARKERS,
    "ANALYTICS": m.ANALYTICS_MARKERS,
    "MARKETING": m.MARKETING_MARKERS,
    "BOOKING": m.BOOKING_MARKERS,
}

# Ordinary words that contain a marker as a substring, with no tool present.
TRAPS = {
    "mbo":     ["jumbo", "combo", "gumbo", "mambo", "bimbo", "crambo", "mumbo"],
    "fresha":  ["refreshable", "refreshablepage"],
    "fbq":     ["fbquery", "afbq"],
    "gtag":    ["gtagline"],
    "zoho":    ["zohover"],
    "acuity":  ["acutely", "acutiy"],
    "close.com": ["enclose.completion"],
    "vcita":   ["evcita"],
    "monday.com": ["someday.com"],
    "book.app": [],
    "mailchimp": ["mailchimps"],
    "hubspot": ["hubspots"],
}

print("=" * 84)
print("MARKER SUBSTRING AUDIT — does a bare `in` test fire on innocent text?")
print("=" * 84)
bad = []
for group, table in MARKERS.items():
    for marker, name in table.items():
        traps = TRAPS.get(marker, [])
        hits = [t for t in traps if marker in t]
        if hits:
            bad.append((group, marker, name, hits))
            print(f"[FALSE POSITIVE RISK] {group:10} marker={marker!r} -> {name!r}")
            print(f"                      fires on ordinary words: {hits}")

if not bad:
    print("no substring traps found in the sampled set")

print()
print("=" * 84)
print("LIVE CHECK — do these markers fire on the real top-10 pages?")
print("=" * 84)
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
SITES = ["hagarinsurance.com", "lock-law.com", "pridestaff.com", "protechjobs.com",
         "temeculavalleyinsuranceagency.com", "titaniumtaxes.com", "rickdouglas.net"]


def get(u, b=400000):
    r = urllib.request.Request(u if "://" in u else "https://" + u, headers=UA)
    with urllib.request.urlopen(r, timeout=25, context=CTX) as x:
        return x.read(b).decode("utf-8", "ignore")


for site in SITES:
    try:
        h = get(site).lower()
    except Exception as e:
        print(f"{site}: unreachable ({type(e).__name__})")
        continue
    flagged = []
    for group, table in MARKERS.items():
        for marker, name in table.items():
            if marker in h:
                # show the surrounding context so a human can judge
                i = h.find(marker)
                ctx = re.sub(r"\s+", " ", h[max(0, i - 45):i + 45])
                flagged.append((group, marker, name, ctx))
    print(f"\n{site}  ({len(flagged)} marker hits)")
    for group, marker, name, ctx in flagged:
        suspicious = any(
            marker in w for w in sum(TRAPS.values(), [])
        )
        tag = "  <<< SUSPICIOUS SUBSTRING" if suspicious else ""
        print(f"   {group:10} {marker!r:20} -> {name:22}{tag}")
        if suspicious:
            print(f"              context: ...{ctx}...")

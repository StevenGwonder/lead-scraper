#!/usr/bin/env python3
"""SGW-945: measure how much the substring markers corrupt real records.

`mbo` -> Mindbody fires on "symbol". `gtag` fires on "tostringtag". `fresha`
fires on "refreshable". A false PRESENT is worse than a false gap: it silently
REMOVES an automation gap, lowering the score and hiding a real lead.

For each record whose cache says a tool is present, re-test the marker with a
proper word-boundary match and report how many "present" claims are bogus.
"""
import json
import re
import ssl
import urllib.request
from pathlib import Path

CACHE = Path("/home/steven/.hermes/scripts/local-biz-cache.json")
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

# Markers that are NOT safe as bare substrings, with a stricter pattern each.
SUBSTRING_RISK = {
    "mbo": r"\bmbo\b",
    "fresha": r"\bfresha\b",
    "gtag": r"gtag\(|/gtag/js|gtag\(",
    "fbq": r"\bfbq\b|_fbq",
    "vcita": r"\bvcita\b",
    "close.com": r"\bclose\.com\b",
    "zoho": r"\bzoho\b",
    "hubspot": r"\bhubspot\b",
    "mailchimp": r"\bmailchimp\b",
    "acuity": r"\bacuity\b",
}


def get(u, b=400000):
    r = urllib.request.Request(u if "://" in u else "https://" + u, headers=UA)
    with urllib.request.urlopen(r, timeout=25, context=CTX) as x:
        return x.read(b).decode("utf-8", "ignore")


cache = json.loads(CACHE.read_text())
recs = [v for v in cache["businesses"].values()
        if v.get("eligibility_state") == "eligible"]
recs.sort(key=lambda v: -((v.get("lead_score") or {}).get("score") or 0))

print("=" * 88)
print("SUBSTRING-MARKER DAMAGE — cache says a tool is PRESENT; is it real?")
print("=" * 88)

checked = 0
bogus = []
for b in recs[:25]:
    sq = b.get("site_quality") or {}
    present = (sq.get("has_crm") or []) + (sq.get("has_analytics") or []) + \
              (sq.get("has_marketing_tools") or []) + (sq.get("has_booking") or [])
    if not present:
        continue
    dom = (b.get("own_domains") or [b.get("url") or ""])[0]
    try:
        h = get(dom).lower()
    except Exception:
        continue
    checked += 1
    for label, pat in SUBSTRING_RISK.items():
        if label in h:  # engine's bare test would fire
            if not re.search(pat, h):  # strict test does not
                i = h.find(label)
                ctx = re.sub(r"\s+", " ", h[max(0, i - 40):i + 40])
                bogus.append((b.get("name"), dom, label, ctx))

print(f"records re-fetched: {checked}")
print(f"bogus PRESENT claims found: {len(bogus)}")
for name, dom, marker, ctx in bogus:
    print(f"\n  {str(name)[:36]:38} {dom}")
    print(f"     marker {marker!r} matched by bare `in` but NOT by a strict pattern")
    print(f"     context: ...{ctx}...")

print()
print("=" * 88)
print("AGGREGATE — how many eligible records could carry a bogus tool claim?")
print("=" * 88)
risk_holders = 0
for b in recs:
    sq = b.get("site_quality") or {}
    names = (sq.get("has_crm") or []) + (sq.get("has_analytics") or []) + \
            (sq.get("has_marketing_tools") or []) + (sq.get("has_booking") or [])
    if any(n in ("Mindbody",) for n in names):
        risk_holders += 1
print(f"eligible records claiming the riskiest tool (Mindbody via 'mbo'): {risk_holders}")

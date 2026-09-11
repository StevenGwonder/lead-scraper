#!/usr/bin/env python3
"""SGW-861 QC — Verify the review worksheet against the fixture + live pipeline.

Checks every rendered field in sgw-861-review-worksheet.html against
benchmark/prospects.json + labels.json and the live qualify_lead ranking.
Prints PASS/FAIL per check. Exit 0 only if all pass.
"""
import html as htmllib
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WS = os.path.join(HERE, "sgw-861-review-worksheet.html")
FIXTURE = os.path.join(HERE, "prospects.json")
LABELS = os.path.join(HERE, "labels.json")

failures = []


def check(cond, msg):
    if cond:
        print(f"  PASS  {msg}")
    else:
        print(f"  FAIL  {msg}")
        failures.append(msg)


def unescape(s):
    return htmllib.unescape(s)


# ---- 1. Load fixture + labels + worksheet ----
fix = json.load(open(FIXTURE))["prospects"]
labels = json.load(open(LABELS))["labels"]
html_src = open(WS, encoding="utf-8").read()

# ---- 2. Recompute the LIVE ranking (same as evaluate.py) ----
spec = importlib.util.spec_from_file_location("pipe", os.path.join(REPO, "local-biz-92562.py"))
pipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipe)


def reconstruct(entry):
    sq = dict(entry.get("site_quality") or {})
    return {
        "name": entry.get("name", ""),
        "trade": entry.get("trade", ""),
        "phones": list(entry.get("phones_anonymized") or []),
        "emails": list(entry.get("emails_anonymized") or []),
        "own_domains": entry.get("own_domains", []),
        "hiring_role_match": entry.get("hiring_role_match", False),
        "hiring_signals": entry.get("hiring_signals", []),
        "review_negative": entry.get("review_negative", False),
        "review_signals": entry.get("review_signals", []),
        "site_quality": sq,
    }


ranked = []
for key, entry in fix.items():
    biz = reconstruct(entry)
    url = entry.get("url", "") or (entry.get("own_domains") or [""])[0]
    if pipe._is_directory_record(url, entry.get("name", "")) or pipe._mentions_out_of_area(entry.get("name", "") + " " + url):
        score = {"score": 0, "tier": "Cold", "reasons": ["directory/out-of-area record"]}
    else:
        try:
            score = pipe.qualify_lead(biz, biz["site_quality"])
        except Exception as e:
            score = {"score": 0, "tier": "Cold", "reasons": [f"EVAL ERROR: {e}"]}
    ranked.append((score.get("score", 0), key, entry, labels[key], score))
ranked.sort(key=lambda x: x[0], reverse=True)

expected_top10 = [(k, e) for _, k, e, _, _ in ranked[:10]]
expected_review_keys = [k for _, k, _, _, _ in ranked[:10]] + [
    "kdainclicensedcpasen", "plumbingservices", "manageditservicestem",
    "handyman", "lawlink", "murrietacaattorneydi", "rioslandscapeandtree",
    "fullserviceaccountin",
]
# dedupe preserving order
seen = set()
expected_review_keys = [k for k in expected_review_keys if not (k in seen or seen.add(k))]

print(f"=== SGW-861 worksheet QC ({len(expected_review_keys)} expected records) ===")

# ---- 3. Structure checks ----
check("<table>" in html_src, "worksheet contains a table")
row_count = html_src.count("<tr>") - 1  # header row
check(row_count == len(expected_review_keys), f"row count {row_count} == expected {len(expected_review_keys)}")
# count actual contenteditable reason boxes (class="reason-box" with data-k), not CSS rules
reason_box_els = len(re.findall(r'class="reason-box" contenteditable="true"', html_src))
check(reason_box_els == len(expected_review_keys), f"reason boxes {reason_box_els} == {len(expected_review_keys)}")
# verdict chips: 4 per row (good_fit/possible_fit/bad_fit/unknown)
vopts = html_src.count('class="vopt"')
check(vopts == len(expected_review_keys) * 4, f"verdict chips {vopts} == {len(expected_review_keys)*4}")
check("<script>" in html_src and "</script>" in html_src, "interactive script present")
check("555-01" in html_src, "anonymized phone placeholders present")

# ---- 4. Anonymization ----
# No real phone numbers: must NOT match real US patterns (only 555-01xx placeholders)
real_phone = re.findall(r"\(\d{3}\) \d{3}-\d{4}", html_src)
leaked = [p for p in real_phone if not re.match(r"\(951\) 555-01\d{2}", p)]
check(len(leaked) == 0, f"no real phone numbers leaked (found {len(real_phone)} patterns, {len(leaked)} non-placeholder)")
# No real emails: only anonymized placeholders (contactN@anonymized.example) allowed
real_email = [e for e in re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", html_src)
              if not e.endswith("@anonymized.example")]
check(len(real_email) == 0, f"no real emails leaked (found {len(real_email)} non-anonymized)")

# ---- 5. Per-record field fidelity ----
print("\n--- per-record evidence fidelity ---")
# Unescape the full HTML once so escaped entities (&amp;, &#8212;) match fixture strings
html_unesc = htmllib.unescape(html_src)
for key in expected_review_keys:
    entry = fix[key]
    name = entry.get("name", "")
    # Name appears in the UNESCAPED HTML
    name_found = name in html_unesc
    check(name_found, f"{name[:40]:42} name rendered")
    # URL
    url = entry.get("url", "")
    check(url and url in html_unesc, f"{name[:40]:42} url rendered")
    # Tier
    tier = entry.get("tier", "")
    # score from live ranking for this key
    live_score = next(sc for _, k, _, _, sc in ranked if k == key)
    check(str(live_score.get("score")) in html_unesc, f"{name[:40]:42} score {live_score.get('score')} rendered")
    check(live_score.get("tier", "") in html_unesc, f"{name[:40]:42} tier {live_score.get('tier')} rendered")
    # Agent label
    lbl = labels[key].get("label", "?")
    check(lbl in html_unesc, f"{name[:40]:42} agent label '{lbl}' rendered")
    # Phones: each placeholder present
    for ph in (entry.get("phones_anonymized") or []):
        check(ph in html_unesc, f"{name[:40]:42} phone {ph} rendered")
    # Site status/confidence
    sq = entry.get("site_quality") or {}
    if sq:
        for field in ("status", "confidence"):
            val = sq.get(field)
            if val:
                check(str(val) in html_unesc, f"{name[:40]:42} site {field}={val} rendered")

# ---- 6. Rank order fidelity (top-10 exact) ----
print("\n--- top-10 order fidelity ---")
# Extract business names in worksheet order
name_order = [htmllib.unescape(n) for n in re.findall(r'class="biz-name">([^<]+)<', html_src)]
expected_top10_names = [e.get("name", "") for _, e in expected_top10]
# The sample is appended in RANKED order (same as build script: ranked[10:]), so
# expected order = top10 names + the 8 sample keys in LIVE ranking order
sample_ranked = [(k, e) for _, k, e, _, _ in ranked if k in set(expected_review_keys[10:])]
expected_names = expected_top10_names + [e.get("name", "") for k, e in sample_ranked]
check(name_order == expected_names, "worksheet row order == live ranking order (top10 + sample)")

# ---- 7. Verify each displayed score == LIVE pipeline score (fixture 'score' is a stale export snapshot) ----
print("\n--- scores vs live pipeline ---")
for key in expected_review_keys:
    entry = fix[key]
    name = entry.get("name", "")
    live_score = next(sc for _, k, _, _, sc in ranked if k == key).get("score")
    check(str(live_score) in html_unesc, f"{name[:40]:42} live score {live_score} rendered")

print("\n" + ("=" * 60))
if failures:
    print(f"QC RESULT: {len(failures)} FAILURES")
    for f_ in failures:
        print("  -", f_)
    sys.exit(1)
else:
    print("QC RESULT: ALL CHECKS PASSED")

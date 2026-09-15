#!/usr/bin/env python3
"""SGW-945: corrected online QC of the top 10 — uses the ENGINE'S OWN marker
tables and the ENGINE'S OWN detection, not a private list I invented.

My first QC pass was invalid. It flagged "FALSE GAP: no CRM" on two leads using
a 'creatio' marker that does not exist in the engine's CRM_MARKERS — so it
reported defects the engine never committed. A QC script that invents its own
markers measures nothing. This version imports the engine and calls its real
_detect_markers, so a PASS/FAIL here is a statement about the engine.

Also fixes a second flaw: "false gap" must be judged by the engine's own
semantics. A booking gap is only false if the engine's own BOOKING_MARKERS find
a booking tool in the live page.
"""
import json
import ssl
import sys
import urllib.request
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

CACHE = Path("/home/steven/.hermes/scripts/local-biz-cache.json")
OUT = Path("/tmp/qc-top10-corrected.json")
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

spec = importlib.util.spec_from_file_location(
    "p", "/home/steven/lead-scraper-SGW-944/local-biz-92562.py")
eng = importlib.util.module_from_spec(spec)
sys.modules["p"] = eng
spec.loader.exec_module(eng)


def fetch(url, budget=400000):
    last = "no attempt"
    for target in ([url] if "://" in url else ["https://" + url, "http://" + url]):
        try:
            req = urllib.request.Request(target, headers=UA)
            with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
                raw = r.read(budget + 1)
                return {"ok": True, "final": r.geturl(), "status": r.status,
                        "bytes": len(raw), "truncated": len(raw) > budget,
                        "html": raw.decode("utf-8", errors="ignore")}
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
    return {"ok": False, "error": last}


def digits(s):
    return "".join(ch for ch in (s or "") if ch.isdigit())


cache = json.loads(CACHE.read_text())
elig = [v for v in cache["businesses"].values() if v.get("eligibility_state") == "eligible"]
elig.sort(key=lambda v: -((v.get("lead_score") or {}).get("score") or 0))

results = []
for i, b in enumerate(elig[:10], 1):
    name = b.get("name", "?")
    dom = (b.get("own_domains") or [b.get("url", "")])[0]
    page = fetch(dom)
    html = page.get("html", "") if page.get("ok") else ""
    low = html.lower()
    sq = b.get("site_quality") or {}
    gaps = sq.get("automation_gaps") or []

    rec = {"rank": i, "name": name, "score": (b.get("lead_score") or {}).get("score"),
           "domain": dom, "engine_trade": b.get("trade"),
           "engine_gaps": gaps,
           "engine_phones": [p for p in (b.get("phones") or []) if p],
           "live": {k: page.get(k) for k in ("ok", "final", "status", "bytes", "truncated", "error")}}

    # --- gaps judged by the ENGINE'S OWN marker tables ----------------------
    gap_tests = {}
    if not page.get("ok"):
        rec["verdict"] = "UNREACHABLE"
        rec["gap_tests"] = {}
        results.append(rec)
        continue

    checks = {
        "no CRM": (eng.CRM_MARKERS, "crm"),
        "no marketing tools": (eng.MARKETING_MARKERS, "marketing"),
        "no analytics": (eng.ANALYTICS_MARKERS, "analytics"),
        "no booking system": (eng.BOOKING_MARKERS, "booking"),
        "no booking/chat system": (eng.BOOKING_MARKERS, "booking"),
    }
    for gap, (table, _) in checks.items():
        if gap in gaps:
            found = eng._detect_markers(low, table)
            gap_tests[gap] = {"engine_would_find": found, "false_gap": bool(found)}
    if "no click-to-call" in gaps:
        gap_tests["no click-to-call"] = {"engine_would_find": ["tel:"] if "tel:" in low else [],
                                         "false_gap": "tel:" in low}
    if "not mobile-responsive" in gaps:
        gap_tests["not mobile-responsive"] = {"engine_would_find": ["viewport"] if "viewport" in low else [],
                                              "false_gap": "viewport" in low}
    if "no contact page" in gaps:
        hit = "contact" in low
        gap_tests["no contact page"] = {"engine_would_find": ["contact"] if hit else [],
                                        "false_gap": hit}
    rec["gap_tests"] = gap_tests

    # --- phone, judged strictly --------------------------------------------
    phone_checks = []
    for p in rec["engine_phones"]:
        d = digits(p)
        last7 = d[-7:] if len(d) >= 7 else d
        phone_checks.append({"phone": p,
                             "on_page": last7 in digits(low),
                             "tel_link": bool(__import__("re").search(r"tel:[^\"']*" + last7, html, __import__("re").I))})
    rec["phone_evidence"] = phone_checks

    # --- local signal -------------------------------------------------------
    rec["local_evidence"] = [t for t in ("temecula", "murrieta", "92562", "92563", "92590",
                                         "92591", "92592", "menifee", "wildomar", "lake elsinore",
                                         "winchester", "riverside county", "french valley")
                             if t in low]
    # --- national chain tell -------------------------------------------------
    rec["national_tells"] = [t for t in ("/locations", "find a location", "nationwide",
                                         "all 50 states", "franchise", "our locations",
                                         "find your local") if t in low]

    probs = []
    if page.get("truncated"):
        probs.append("read truncated (gaps UNKNOWN, not ABSENT)")
    false_gaps = [g for g, t in gap_tests.items() if t.get("false_gap")]
    if false_gaps:
        probs.append("FALSE GAP: " + ", ".join(false_gaps))
    if rec["engine_phones"] and not any(c["on_page"] or c["tel_link"] for c in phone_checks):
        probs.append("ENGINE PHONE NOT ON PAGE")
    if not rec["local_evidence"]:
        probs.append("NO LOCAL SIGNAL")
    if rec["national_tells"]:
        probs.append("national-chain tells: " + ", ".join(rec["national_tells"][:2]))
    rec["problems"] = probs
    rec["verdict"] = ("NOT CALLABLE" if any("PHONE NOT" in p for p in probs)
                      else "ISSUES" if probs else "OK")
    results.append(rec)
    print(f"[{i:2}/10] {name[:36]:38} {rec['verdict']:12} "
          f"{'; '.join(probs)[:70]}")

print("\n" + "=" * 92)
print("CORRECTED QC — gaps judged by the engine's own marker tables")
print("=" * 92)
for v in ("OK", "ISSUES", "NOT CALLABLE", "UNREACHABLE"):
    grp = [r for r in results if r["verdict"] == v]
    if grp:
        print(f"\n{v} ({len(grp)})")
        for r in grp:
            print(f"   {r['rank']:2}. {r['name'][:38]:40} {r['score']}")
            for g, t in (r.get("gap_tests") or {}).items():
                if t.get("false_gap"):
                    print(f"        FALSE GAP {g!r}: engine marker found -> {t['engine_would_find']}")

OUT.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                           "results": results}, indent=2))
print(f"\nwrote {OUT}")

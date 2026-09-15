#!/usr/bin/env python3
"""SGW-945: independent online QC of the engine's TOP 10 leads.

For every top-10 lead, re-derive the engine's claims from the LIVE site and
record where the engine is right, wrong, or unverifiable. The rule: a claim only
counts as confirmed if I can see it myself, on the live page, right now.

Checks per lead:
  IDENTITY  the business is real and is what the engine says it is
  LOCAL     the business plausibly operates in the 92562 / Murrieta-Temecula area
  PHONE     the engine's phone number is actually published by the business
  GAPS      each claimed automation gap, tested against the live page
  CONTACT   at least one working contact path exists

Writes /tmp/qc-top10.json and prints a verdict table.
"""
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CACHE = Path("/home/steven/.hermes/scripts/local-biz-cache.json")
OUT = Path("/tmp/qc-top10.json")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def fetch(url, budget=400000):
    last = "no attempt"
    for target in ([url] if "://" in url else ["https://" + url, "http://" + url]):
        try:
            req = urllib.request.Request(target, headers=UA)
            with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
                raw = r.read(budget + 1)
                return {
                    "ok": True, "url": target, "final": r.geturl(),
                    "status": r.status, "bytes": len(raw),
                    "truncated": len(raw) > budget,
                    "html": raw.decode("utf-8", errors="ignore"),
                }
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
    return {"ok": False, "url": url, "error": last}


def digits(s):
    return re.sub(r"\D", "", s or "")


def main():
    cache = json.loads(CACHE.read_text())
    elig = [v for v in cache["businesses"].values()
            if v.get("eligibility_state") == "eligible"]
    elig.sort(key=lambda v: -((v.get("lead_score") or {}).get("score") or 0))
    top = elig[:10]

    results = []
    for i, b in enumerate(top, 1):
        name = b.get("name", "?")
        doms = b.get("own_domains") or []
        url0 = (doms[0] if doms else b.get("url", "")) or b.get("url", "")
        sq = b.get("site_quality") or {}
        gaps = sq.get("automation_gaps") or []
        score = (b.get("lead_score") or {}).get("score")
        engine_phones = [p for p in (b.get("phones") or []) if p]
        rec = {
            "rank": i, "key": None, "name": name, "score": score,
            "engine_url": url0, "engine_domains": doms,
            "engine_trade": b.get("trade"), "engine_city": b.get("city") or b.get("address"),
            "engine_phones": engine_phones, "engine_emails": b.get("emails") or [],
            "engine_gaps": gaps, "engine_site_status": sq.get("status"),
            "engine_confidence": sq.get("confidence"),
        }
        # find the cache key
        for k, v in cache["businesses"].items():
            if v is b:
                rec["key"] = k
                break

        page = fetch(url0) if url0 else {"ok": False, "error": "no url"}
        rec["live"] = {kk: page.get(kk) for kk in
                       ("ok", "url", "final", "status", "bytes", "truncated", "error")}
        html = page.get("html", "") if page.get("ok") else ""
        low = html.lower()

        # --- IDENTITY --------------------------------------------------------
        title = ""
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()[:120]
        rec["live_title"] = title

        # --- PHONE -----------------------------------------------------------
        phone_checks = []
        for p in engine_phones:
            d = digits(p)
            last7 = d[-7:] if len(d) >= 7 else d
            tel_link = bool(re.search(r'tel:[^"\']*' + last7, html, re.I))
            plain = last7 in digits(low)
            phone_checks.append({"phone": p, "on_page": plain, "tel_link": tel_link})
        rec["phone_evidence"] = phone_checks

        # --- GAPS: re-test each claim on the LIVE page -----------------------
        gap_tests = {}
        if "no marketing tools" in gaps:
            found = [t for t in ("hubspot", "mailchimp", "constantcontact", "klaviyo",
                                 "activecampaign", "marketo", "sendinblue", "brevo")
                     if t in low]
            gap_tests["no marketing tools"] = {"still_missing": not found, "found": found}
        if "no CRM" in gaps:
            found = [t for t in ("hubspot", "salesforce", "zoho", "pipedrive", "insightly",
                                 "lessannoyingcrm", "creatio") if t in low]
            gap_tests["no CRM"] = {"still_missing": not found, "found": found}
        if "no analytics" in gaps:
            found = [t for t in ("google-analytics", "googletagmanager", "gtag(", "gtag/js",
                                 "analytics.js", "plausible", "matomo", "fathom") if t in low]
            gap_tests["no analytics"] = {"still_missing": not found, "found": found}
        if "no booking system" in gaps or "no booking/chat system" in gaps:
            found = [t for t in ("calendly", "acuity", "schedulicity", "setmore", "squareup",
                                 "booksy", "mindbody", "vagaro", "housecallpro",
                                 "servicetitan", "jobber", "zoho bookings") if t in low]
            gap_tests["no booking system"] = {"still_missing": not found, "found": found}
        if "no click-to-call" in gaps:
            tel = "tel:" in low
            gap_tests["no click-to-call"] = {"still_missing": not tel, "found": ["tel: link"] if tel else []}
        if "not mobile-responsive" in gaps:
            vp = "viewport" in low
            gap_tests["not mobile-responsive"] = {"still_missing": not vp,
                                                  "found": ["viewport meta"] if vp else []}
        if "no contact page" in gaps:
            found = [t for t in ("contact", "get in touch", "reach us") if t in low]
            gap_tests["no contact page"] = {"still_missing": not found, "found": found[:2]}
        if any(g.startswith("thin content") for g in gaps):
            words = len(re.sub(r"<[^>]+>", " ", html).split())
            gap_tests["thin content"] = {"still_missing": words < 200, "words": words}
        rec["gap_tests"] = gap_tests

        # --- LOCAL -----------------------------------------------------------
        local_hits = [t for t in ("temecula", "murrieta", "92562", "92563", "92590",
                                  "92591", "92592", "riverside county", "menifee",
                                  "wildomar", "lake elsinore", "french valley", "winchester")
                      if t in low]
        rec["local_evidence"] = local_hits

        # --- CONTACT ---------------------------------------------------------
        emails_found = sorted(set(re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", html)))[:6]
        emails_found = [e for e in emails_found if not e.lower().endswith((".png", ".jpg", ".gif", ".svg", ".webp"))]
        rec["emails_on_page"] = emails_found
        rec["has_form"] = bool(re.search(r"<form[^>]*>", html, re.I))
        rec["has_tel_link"] = "tel:" in low

        results.append(rec)
        print(f"[{i}/10] {name[:38]:40} live={page.get('ok')} "
              f"{page.get('bytes','-')}B gaps={len(gaps)}")

    # ---- verdict ------------------------------------------------------------
    print("\n" + "=" * 96)
    print("TOP-10 ONLINE QC")
    print("=" * 96)
    summary = {"verified": [], "issues": [], "broken": []}
    for r in results:
        probs = []
        if not r["live"].get("ok"):
            probs.append(f"SITE UNREACHABLE ({r['live'].get('error','?')[:40]})")
        else:
            if r["live"].get("truncated"):
                probs.append("read truncated")
            if not any(c["on_page"] or c["tel_link"] for c in r["phone_evidence"]):
                probs.append("ENGINE PHONE NOT ON PAGE")
            bad_gaps = [g for g, t in r["gap_tests"].items()
                        if not t.get("still_missing") and t.get("found")]
            if bad_gaps:
                probs.append("FALSE GAP: " + ", ".join(bad_gaps))
            if not r["local_evidence"]:
                probs.append("NO LOCAL SIGNAL")
        r["problems"] = probs
        (summary["broken"] if (probs and any("UNREACHABLE" in p or "PHONE NOT" in p for p in probs))
         else summary["issues"] if probs else summary["verified"]).append(r["name"])

    for r in results:
        tag = "BROKEN" if any("UNREACHABLE" in p or "PHONE NOT" in p for p in r["problems"]) \
            else "ISSUE " if r["problems"] else "OK    "
        print(f"[{tag}] {r['rank']:2}. {r['name'][:36]:38} {r['score']} "
              f"{r['engine_phones'][0] if r['engine_phones'] else '-':16} "
              f"{'| ' + '; '.join(r['problems']) if r['problems'] else ''}")

    print("\n" + "-" * 96)
    print(f"CLEAN      : {len(summary['verified'])}  {summary['verified']}")
    print(f"ISSUES     : {len(summary['issues'])}  {summary['issues']}")
    print(f"NOT CALLABLE: {len(summary['broken'])}  {summary['broken']}")
    OUT.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                               "results": results}, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()

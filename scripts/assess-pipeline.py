#!/usr/bin/env python3
"""SGW-945: whole-pipeline assessment — where leads are created, lost, and scored.

Walks every stage of the lead machine and reports the real numbers at each
funnel step, so a leak is visible as a number rather than a feeling.

Stages:
  1 SOURCE     what the crawler collected
  2 ELIGIBILITY what survived the gate, and why the rest did not
  3 CONTACT    who is actually callable
  4 EVIDENCE   which signals exist, and whether they were OBSERVED (AGENTS.md 1b)
  5 SCORE      the score distribution and what is unreachable
  6 RANK       the top of the list and what got them there

Read-only. Writes /tmp/pipeline-assessment.json.
"""
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

CACHE = Path("/home/steven/.hermes/scripts/local-biz-cache.json")
ENGINE = Path("/home/steven/lead-scraper-SGW-944/local-biz-92562.py")
OUT = Path("/tmp/pipeline-assessment.json")


def load_engine():
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("p", str(ENGINE))
    m = importlib.util.module_from_spec(spec)
    sys.modules["p"] = m
    spec.loader.exec_module(m)
    return m


def norm(p):
    return re.sub(r"\D", "", p or "")


def main():
    m = load_engine()
    cache = json.loads(CACHE.read_text())
    biz = cache["businesses"]
    a = {}

    print("=" * 90)
    print("LEAD PIPELINE ASSESSMENT")
    print("=" * 90)

    # ---- 1 SOURCE ----------------------------------------------------------
    a["source"] = {
        "records_total": len(biz),
        "with_own_domain": sum(1 for v in biz.values() if v.get("own_domains")),
        "with_url": sum(1 for v in biz.values() if v.get("url")),
        "no_domain_no_url": sum(1 for v in biz.values() if not v.get("own_domains") and not v.get("url")),
    }
    print(f"\n1 SOURCE")
    print(f"   records collected        : {a['source']['records_total']}")
    print(f"   with an own domain       : {a['source']['with_own_domain']}")
    print(f"   with no domain and no url: {a['source']['no_domain_no_url']}")

    # ---- 2 ELIGIBILITY -----------------------------------------------------
    states = Counter(v.get("eligibility_state") for v in biz.values())
    reasons = Counter()
    for v in biz.values():
        if v.get("eligibility_state") in ("rejected", "research"):
            r = v.get("eligibility_reason") or "?"
            reasons[re.sub(r"\(.*?\)", "", r).strip()] += 1
    a["eligibility"] = {"states": dict(states), "reject_reasons": dict(reasons.most_common(12))}
    print(f"\n2 ELIGIBILITY GATE")
    for s, n in states.most_common():
        print(f"   {str(s):12} : {n}")
    print("   top reasons out:")
    for r, n in reasons.most_common(6):
        print(f"     {n:4}  {r[:64]}")

    elig = [v for v in biz.values() if v.get("eligibility_state") == "eligible"]

    # ---- 3 CONTACT ---------------------------------------------------------
    callable_ = [v for v in elig if any(norm(p) and len(norm(p)) == 10 for p in (v.get("phones") or []))]
    emailable = [v for v in elig if v.get("emails")]
    a["contact"] = {"eligible": len(elig), "callable": len(callable_),
                    "emailable": len(emailable),
                    "callable_and_emailable": len([v for v in elig
                                                   if v in callable_ and v.get("emails")])}
    print(f"\n3 CONTACTABILITY")
    print(f"   eligible                 : {len(elig)}")
    print(f"   with a 10-digit phone    : {len(callable_)}")
    print(f"   with an email            : {len(emailable)}")
    print(f"   both paths               : {a['contact']['callable_and_emailable']}")

    # ---- 4 EVIDENCE (AGENTS.md 1b: observed vs unknown) --------------------
    tier1_own = 0
    tier1_any = 0
    tier2 = 0
    tier3 = 0
    unverified_site = 0
    for v in elig:
        sq = v.get("site_quality") or {}
        observed = (sq.get("status") == "up" and sq.get("confidence") == "high")
        if not observed:
            unverified_site += 1
        hs = v.get("hiring_signals") or []
        if any(h.get("source_kind") == "own_site" for h in hs):
            tier1_own += 1
        if hs:
            tier1_any += 1
        if v.get("review_negative"):
            tier2 += 1
        gaps = sq.get("automation_gaps") or []
        if observed and gaps:
            tier3 += 1
    a["evidence"] = {
        "tier1_own_site_hiring": tier1_own, "tier1_any_hiring": tier1_any,
        "tier2_corroborated_pain": tier2, "tier3_manual_tells_observed": tier3,
        "site_read_unverified": unverified_site, "eligible": len(elig),
    }
    print(f"\n4 EVIDENCE (AGENTS.md 1b: PRESENT beats UNKNOWN)")
    print(f"   T1 own-site hiring proof : {tier1_own}   <-- the +25 that reaches Hot")
    print(f"   T1 any hiring signal     : {tier1_any}")
    print(f"   T2 corroborated pain     : {tier2}")
    print(f"   T3 observed manual tells : {tier3}")
    print(f"   site read UNVERIFIED     : {unverified_site} of {len(elig)}")

    # ---- 5 SCORE -----------------------------------------------------------
    scores = sorted([((v.get("lead_score") or {}).get("score") or 0) for v in elig], reverse=True)
    tiers = Counter((v.get("lead_score") or {}).get("tier") for v in elig)
    hot_bar = 65
    a["score"] = {"max": max(scores) if scores else 0, "hot_bar": hot_bar,
                  "at_or_above_hot": sum(1 for s in scores if s >= hot_bar),
                  "tiers": dict(tiers),
                  "top10": scores[:10]}
    print(f"\n5 SCORE")
    print(f"   max score                : {a['score']['max']}")
    print(f"   Hot bar                  : {hot_bar}")
    print(f"   records at/above bar     : {a['score']['at_or_above_hot']}")
    print(f"   tiers                    : {dict(tiers)}")
    if a["score"]["max"] < hot_bar:
        print(f"   >>> Hot is UNREACHABLE: the highest-scoring lead is "
              f"{hot_bar - a['score']['max']} points short, and the signal that")
        print(f"       closes that gap (T1 own-site hiring) is held by {tier1_own} records.")

    # ---- 6 what the top actually earned points for --------------------------
    elig.sort(key=lambda v: -((v.get("lead_score") or {}).get("score") or 0))
    print(f"\n6 TOP 10 — why they scored")
    top = []
    for i, v in enumerate(elig[:10], 1):
        ls = v.get("lead_score") or {}
        reasons = ls.get("reasons") or []
        sq = v.get("site_quality") or {}
        top.append({"rank": i, "name": v.get("name"), "score": ls.get("score"),
                    "trade": v.get("trade"), "reasons": reasons,
                    "gaps": sq.get("automation_gaps") or [],
                    "site_observed": sq.get("status") == "up" and sq.get("confidence") == "high",
                    "phone": (v.get("phones") or [None])[0]})
        print(f"   {i:2}. {str(ls.get('score')):3} {str(v.get('name'))[:34]:36} {str(v.get('trade'))[:18]}")
        for r in reasons[:3]:
            print(f"        - {r[:76]}")
    a["top10"] = top

    OUT.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                               "assessment": a}, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()

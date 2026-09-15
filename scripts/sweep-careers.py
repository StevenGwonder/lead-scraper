#!/usr/bin/env python3
"""NWP-LEAD-15/18 backfill: run the first-party careers crawl over every
eligible record, then re-score.

WHY THIS IS NEEDED: main() skips any record whose site_quality already has a
successful check ("Already successfully checked"), so the new careers crawl
would never reach the ~295 records already in the cache. Without this backfill
the change is invisible on existing data.

Scope discipline:
  - eligible records with a domain only
  - polite delay between fetches (the cron host is a 15-year-old iMac)
  - preserves every existing field; only site_quality is refreshed, because
    check_website is authoritative and the live pipeline replaces it too
  - writes a timestamped backup before touching anything
  - re-scores with qualify_lead so own_site_hiring actually moves the score

Usage: python3 scripts/sweep-careers.py [--limit N] [--delay S]
"""
import argparse
import importlib.util
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CACHE = Path("/home/steven/.hermes/scripts/local-biz-cache.json")
ENGINE = Path("/home/steven/lead-scraper/local-biz-92562.py")


def load_engine():
    spec = importlib.util.spec_from_file_location("p", str(ENGINE))
    m = importlib.util.module_from_spec(spec)
    sys.modules["p"] = m
    spec.loader.exec_module(m)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--delay", type=float, default=0.4)
    args = ap.parse_args()

    m = load_engine()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = CACHE.with_suffix(f".json.pre-careers-sweep-{stamp}")
    shutil.copy2(CACHE, backup)
    print(f"backup: {backup}")

    cache = json.loads(CACHE.read_text())
    biz = cache["businesses"]

    targets = [(k, v) for k, v in biz.items()
               if v.get("eligibility_state") == "eligible" and v.get("own_domains")]
    if args.limit:
        targets = targets[:args.limit]
    print(f"sweeping {len(targets)} eligible records with a domain")
    print("=" * 88)

    found_pages = 0
    found_hiring = 0
    errors = 0
    upgraded = []   # records whose score rose because of own-site hiring

    for i, (key, v) in enumerate(targets, 1):
        dom = v["own_domains"][0]
        before = ((v.get("lead_score") or {}).get("score") or 0)
        try:
            sq = m.check_website(dom)
        except Exception as e:
            errors += 1
            print(f"[{i:3}/{len(targets)}] {str(v.get('name'))[:34]:36} ERROR {type(e).__name__}")
            continue

        if not isinstance(sq, dict):
            errors += 1
            continue
        osh = sq.get("own_site_hiring")
        if osh:
            found_hiring += 1

        # NEVER let a worse read overwrite a good one. A sweep at speed trips
        # WAFs, and the first run at 0.4s delay reclassified 22 live sites as
        # down/blocked (14 of them actually serving HTTP 202 + a SiteGround
        # captcha shell), which destroyed 14 valid scores — Amante 37->3,
        # Elite Tax 39->17. Only adopt the new read when it is at least as
        # trustworthy as the one on disk.
        old_status = (v.get("site_quality") or {}).get("status")
        if old_status == "up" and sq.get("status") != "up":
            print(f"[{i:3}/{len(targets)}] {str(v.get('name'))[:34]:36} "
                  f"read regressed ({old_status}->{sq.get('status')}) -> keeping old")
            continue

        v["site_quality"] = sq
        v["lead_score"] = m.qualify_lead(v, sq)
        after = ((v.get("lead_score") or {}).get("score") or 0)
        if after > before:
            upgraded.append((after - before, before, after, v.get("name"), osh is not None))

        flag = "HIRING" if osh else "      "
        delta = f"{before}->{after}" if after != before else f"{after}"
        print(f"[{i:3}/{len(targets)}] {str(v.get('name'))[:34]:36} {flag} {delta}")
        time.sleep(args.delay)

    cache["careers_sweep_at"] = datetime.now(timezone.utc).isoformat()
    CACHE.write_text(json.dumps(cache, indent=2))

    print("=" * 88)
    print(f"own-site hiring pages found : {found_hiring}")
    print(f"scores changed              : {len(upgraded)}")
    for d, b, a, nm, had in sorted(upgraded, reverse=True)[:15]:
        print(f"   +{d:3}  {b:3} -> {a:3}  {str(nm)[:40]}{'  (own-site hiring)' if had else ''}")
    print(f"errors                      : {errors}")
    print(f"backup: {backup}")

    # who is Hot now?
    hot = [v for v in biz.values()
           if ((v.get("lead_score") or {}).get("score") or 0) >= 65]
    print(f"\nrecords at/above the Hot bar (65): {len(hot)}")
    for v in sorted(hot, key=lambda x: -((x.get("lead_score") or {}).get("score") or 0))[:10]:
        print(f"   {(v.get('lead_score') or {}).get('score')}  {str(v.get('name'))[:44]}")


if __name__ == "__main__":
    main()

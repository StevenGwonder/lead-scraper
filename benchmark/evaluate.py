#!/usr/bin/env python3
"""SGW-861 — Evaluate the lead pipeline against the labeled benchmark set.

Offline evaluation: loads benchmark/prospects.json + benchmark/labels.json,
re-runs the CURRENT scoring pipeline (qualify_lead from local-biz-92562.py)
on the fixture, and reports:
  * precision@5, precision@10  (good_fit = positive; possible_fit reported separately)
  * false-positive count in the ranked top-10
  * unverified count (records whose label is unknown OR tier is Unverified)
  * tier confusion matrix (pipeline tier vs. human label)

No live network calls are made — the fixture is self-contained.

Usage:
    python3 benchmark/evaluate.py
    python3 benchmark/evaluate.py --top 10 --verbose
"""
import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# The pipeline file is named with hyphens (local-biz-92562.py) so it cannot be
# imported as a normal module — load it by path so we always eval the LIVE code.
PIPE_PATH = os.path.join(REPO, "local-biz-92562.py")
_spec = importlib.util.spec_from_file_location("local_biz_pipeline", PIPE_PATH)
pipe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pipe)

FIXTURE = os.path.join(HERE, "prospects.json")
LABELS = os.path.join(HERE, "labels.json")

# good_fit is the positive class for precision; possible_fit is "don't discard yet"
POSITIVE = {"good_fit"}
LOOKS = {"good_fit", "possible_fit"}


def load():
    with open(FIXTURE) as f:
        fix = json.load(f)["prospects"]
    with open(LABELS) as f:
        labels = json.load(f)["labels"]
    return fix, labels


def reconstruct_biz(entry):
    """Rebuild the exact dict shape qualify_lead() reads from the cache."""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10, help="precision window (default 10; 5 also reported)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    fix, labels = load()
    ranked = []
    missing = 0
    for key, entry in fix.items():
        if key not in labels:
            missing += 1
            continue
        biz = reconstruct_biz(entry)
        # Mirror the SGW-864 cache sweep from load_cache(): records now
        # identifiable as directory/SEO listings are demoted before scoring,
        # exactly as the live pipeline would treat them on next load.
        url = entry.get("url", "") or (entry.get("own_domains") or [""])[0]
        if pipe._is_directory_record(url, entry.get("name", "")):
            biz["site_quality"] = {"status": "unknown", "confidence": "low"}
            score = {"score": 0, "tier": "Cold", "breakdown": {},
                     "reasons": ["directory/SEO listing — not a real business"]}
            ranked.append((0, key, entry, labels[key], score))
            continue
        try:
            score = pipe.qualify_lead(biz, biz["site_quality"])
        except Exception as e:  # noqa: BLE001 — a broken fixture row must not kill the eval
            score = {"score": 0, "tier": "Cold", "reasons": [f"EVAL ERROR: {e}"]}
        ranked.append((score.get("score", 0), key, entry, labels[key], score))

    ranked.sort(key=lambda x: x[0], reverse=True)

    def precision_at(n, positive_set=POSITIVE):
        top = ranked[:n]
        return sum(1 for _, _, _, lbl, _ in top if lbl.get("label") in positive_set) / max(1, n)

    top5 = ranked[:5]
    top10 = ranked[:args.top]
    fp_top10 = [k for _, k, _, lbl, _ in top10 if lbl.get("label") == "bad_fit"]
    unverified = [k for _, k, _, lbl, _ in ranked if lbl.get("label") == "unknown"]

    print(f"Fixture: {len(fix)} prospects, {len(labels)} labels, {missing} missing labels")
    print(f"precision@5  = {precision_at(5):.2f}  ({sum(1 for *_ , lbl, _ in top5 if lbl.get('label') in POSITIVE)}/5)")
    print(f"precision@{args.top} = {precision_at(args.top):.2f}  ({sum(1 for *_ , lbl, _ in top10 if lbl.get('label') in POSITIVE)}/{args.top})")
    print(f"top-{args.top} false positives: {len(fp_top10)}  {fp_top10}")
    print(f"unverified (label=unknown): {len(unverified)}  {unverified}")

    # Tier confusion matrix: re-scored pipeline tier vs human label
    print("\nTier × label confusion (rows=pipeline tier, cols=human label):")
    tiers = ["Warm", "Cold", "Unverified"]
    labels_order = ["good_fit", "possible_fit", "bad_fit", "unknown"]
    header = "         " + "".join(f"{l[:10]:>12}" for l in labels_order) + "    total"
    print(header)
    for t in tiers:
        row = {l: 0 for l in labels_order}
        for _, _, entry, lbl, score in ranked:
            if score.get("tier") == t:
                row[lbl.get("label")] = row.get(lbl.get("label"), 0) + 1
        total = sum(row.values())
        print(f"{t:10}" + "".join(f"{row[l]:>12}" for l in labels_order) + f"    {total}")

    # Where do the good_fit records sit? Are they actually on top?
    print("\ngood_fit rank positions:", [i + 1 for i, (_, k, _, lbl, _) in enumerate(ranked) if lbl.get("label") == "good_fit"])

    if args.verbose:
        print("\nRanked (top 20):")
        for i, (sc, k, entry, lbl, scobj) in enumerate(ranked[:20], 1):
            reasons = "; ".join(scobj.get("reasons", [])[:2])[:70]
            print(f"{i:2}. {sc:3} {entry.get('tier',''):10} {lbl.get('label'):12} {entry.get('name','')[:35]:35} | {reasons}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

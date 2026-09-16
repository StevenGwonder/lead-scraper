#!/usr/bin/env python3
"""SGW-861 — Evaluate the lead pipeline against the labeled benchmark set.

Offline evaluation: loads benchmark/prospects.json + benchmark/labels.json,
re-runs the CURRENT scoring pipeline (qualify_lead from local-biz-92562.py)
on the fixture, and reports:
  * precision@5, precision@10  (good_fit = positive; possible_fit reported separately)
  * recall@10 — of ALL good_fit records, how many reach the top 10 (NWP-LEAD-17)
  * `good_fit below Warm` — qualified leads discarded into Cold (NWP-LEAD-17)
  * good_fit rank distribution (NWP-LEAD-17, promoted to a first-class metric)
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
        # SGW-944 D6: the benchmark mirrored only the SGW-864 sweep and never
        # ran the eligibility gate, so it scored records the live pipeline
        # would have routed to research/rejected. precision@10 was therefore
        # measuring a pipeline that does not exist. Apply the same gate.
        # NOTE: the fixture stores contact paths as `phones_anonymized` /
        # `emails_anonymized`, NOT `phones`. Passing the wrong key made every
        # record look contact-less, so the gate rejected all 53 and precision@10
        # collapsed to 0.30. Use the same fields reconstruct_biz() uses.
        _elig_state, _elig_reason = pipe.assess_eligibility(
            url, entry.get("name", ""), entry.get("trade", ""),
            list(entry.get("phones_anonymized") or []),
            entry.get("own_domains", []) or [])
        if _elig_state in ("rejected", "research"):
            biz["site_quality"] = {"status": "unknown", "confidence": "low"}
            score = {"score": 0, "tier": "Cold", "breakdown": {},
                     "reasons": [f"eligibility: {_elig_reason}"]}
            ranked.append((0, key, entry, labels[key], score))
            continue
        if (pipe._is_directory_record(url, entry.get("name", ""))
                or pipe._mentions_out_of_area(entry.get("name", "") + " " + url)):
            biz["site_quality"] = {"status": "unknown", "confidence": "low"}
            score = {"score": 0, "tier": "Cold", "breakdown": {},
                     "reasons": ["directory/out-of-area record — not a local business"]}
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

    # ── NWP-LEAD-17: measurement only. NO scoring code is touched by this
    # change. Precision alone hides where the engine loses commercial value:
    # a top-10 of 7 good_fit looks fine while qualified leads rot in Cold.
    def recall_at(n, positive_set=POSITIVE):
        """Of every <positive_set> record in the fixture, how many reach top-n."""
        total = sum(1 for *_, lbl, _ in ranked if lbl.get("label") in positive_set)
        if total == 0:
            return 0.0, 0, 0
        hit = sum(1 for *_, lbl, _ in ranked[:n] if lbl.get("label") in positive_set)
        return hit / total, hit, total

    good_fit_ranks = [i + 1 for i, (_, _, _, lbl, _) in enumerate(ranked)
                      if lbl.get("label") == "good_fit"]
    # A good_fit is "discarded" when the pipeline's own tier has dropped below
    # Warm — i.e. it is not even in the list a human would work from.
    below_warm = [(sc, k, entry.get("name", ""))
                  for sc, k, entry, lbl, scobj in ranked
                  if lbl.get("label") == "good_fit"
                  and (scobj.get("tier") or "Cold") not in ("Hot", "Warm")]
    # Unscored good_fit: demoted by the eligibility/directory gate to score 0.
    unscored_good = [(k, entry.get("name", ""))
                     for _, k, entry, lbl, scobj in ranked
                     if lbl.get("label") == "good_fit"
                     and not (scobj.get("breakdown") or {})]

    rec10, rec_hit, rec_tot = recall_at(args.top)

    print(f"Fixture: {len(fix)} prospects, {len(labels)} labels, {missing} missing labels")
    print(f"precision@5  = {precision_at(5):.2f}  ({sum(1 for *_ , lbl, _ in top5 if lbl.get('label') in POSITIVE)}/5)")
    print(f"precision@{args.top} = {precision_at(args.top):.2f}  ({sum(1 for *_ , lbl, _ in top10 if lbl.get('label') in POSITIVE)}/{args.top})")
    print(f"recall@{args.top}   = {rec10:.2f}  ({rec_hit}/{rec_tot} good_fit records reached the top {args.top})")
    print(f"good_fit below Warm: {len(below_warm)}  "
          f"{[(n, s) for s, _, n in below_warm]}")
    if unscored_good:
        print(f"good_fit demoted by a gate (score 0, no breakdown): {len(unscored_good)}  "
              f"{[n for _, n in unscored_good]}")
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

    # NWP-LEAD-17: good_fit rank distribution as a first-class metric — a
    # qualified lead at rank 32 is invisible to anyone working the top 10.
    print("\ngood_fit rank positions:", good_fit_ranks)
    if good_fit_ranks:
        import statistics
        print(f"good_fit rank: median {statistics.median(good_fit_ranks):.0f}, "
              f"worst {max(good_fit_ranks)}, in top-10 {sum(1 for r in good_fit_ranks if r <= 10)}/"
              f"{len(good_fit_ranks)}")

    if args.verbose:
        print("\nRanked (top 20):")
        for i, (sc, k, entry, lbl, scobj) in enumerate(ranked[:20], 1):
            reasons = "; ".join(scobj.get("reasons", [])[:2])[:70]
            print(f"{i:2}. {sc:3} {entry.get('tier',''):10} {lbl.get('label'):12} {entry.get('name','')[:35]:35} | {reasons}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

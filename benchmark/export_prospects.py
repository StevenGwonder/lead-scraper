#!/usr/bin/env python3
"""SGW-861 — Export a labeled prospect benchmark set from the live cache.

Reads ~/.hermes/scripts/local-biz-cache.json, samples a deterministic
cross-section of prospects (Warm / Cold / Unverified), and writes a versioned,
non-secret fixture to benchmark/prospects.json.

Design rules (see SGW-861 + AGENTS.md):
  * Deterministic: fixed seed + stable sort, so re-runs yield the same sample.
  * Non-secret: real phone numbers and emails are ANONYMIZED (placeholder
    formats preserving count + area-code validity) so the fixture can live
    in a public repo without exposing customer contact data.
  * Scoring-faithful: every field qualify_lead() reads is preserved, so
    evaluate.py can run the current pipeline against the fixture offline.
  * Balanced: Warm, Cold, Unverified + forced inclusion of known
    false-positive archetypes and contact-worthy archetypes.

Usage:
    python3 benchmark/export_prospects.py [--count 50] [--force]
"""
import argparse
import hashlib
import json
import os
import random
import re
import sys

CACHE_PATH = os.path.expanduser("~/.hermes/scripts/local-biz-cache.json")
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prospects.json")
SEED = 92562

# ── Known false-positive archetypes (crawler artifacts, not businesses) ──
GARBAGE_NAMES = {"home page", "home", "untitled", "page not found", "index", "care"}
AGGREGATOR_SUBSTRINGS = (
    "superlawyers", "justia", "yelp", "bbb", "yellowpages", "manta",
    "thumbtack", "angies", "angi", "homeadvisor", "porch", "nextdoor",
    "city-data", "linkedin", "facebook.com",
)

# ── Anonymization ──
_PHONE_SEQ = iter(range(101, 199))


def _valid_area_code(p: str) -> bool:
    m = re.search(r"\((\d{3})\)", p or "")
    if not m:
        return False
    ac = int(m.group(1))
    return 200 <= ac <= 989


def anonymize_phones(phones):
    """Keep count + validity shape, drop real numbers (555-01xx is reserved)."""
    out = []
    for _ in (phones or []):
        n = next(_PHONE_SEQ, 101)
        out.append(f"(951) 555-01{n}")
    return out


def anonymize_emails(emails):
    return [f"contact{idx}@anonymized.example" for idx, _ in enumerate(emails or [])]


def is_false_positive_archetype(biz):
    """Heuristic flags for crawler artifacts — used for sampling, not scoring."""
    name = (biz.get("name") or "").strip().lower()
    if name in GARBAGE_NAMES or len(name) < 5:
        return True
    domain = biz.get("url", "") or (biz.get("own_domains") or [""])[0]
    if any(s in domain.lower() for s in AGGREGATOR_SUBSTRINGS):
        return True
    if biz.get("phones") and not any(_valid_area_code(p) for p in biz["phones"]):
        return True
    return False


def is_contact_worthy_archetype(biz):
    """Heuristic flags for businesses Steven would actually contact."""
    sq = biz.get("site_quality") or {}
    if sq.get("status") != "up" or sq.get("confidence") != "high":
        return False
    if not (biz.get("phones") or sq.get("emails") or biz.get("emails")):
        return False
    # Real phone (valid area code) or a real email + a manual-drag signal
    valid_phone = biz.get("phones") and any(_valid_area_code(p) for p in biz["phones"])
    if not valid_phone and not (biz.get("emails") or sq.get("emails")):
        return False
    has_drag = bool(
        biz.get("hiring_role_match")
        or biz.get("review_negative")
        or (biz.get("trade") in ("Accounting", "Law Office", "Insurance", "Property Management"))
    )
    return has_drag


def pick(candidates, n, rng):
    return sorted(candidates, key=lambda x: (x[0],))[:n] if False else rng.sample(candidates, min(n, len(candidates)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=50)
    ap.add_argument("--force", action="store_true", help="overwrite existing fixture")
    args = ap.parse_args()

    if os.path.exists(OUT_PATH) and not args.force:
        print(f"Fixture exists at {OUT_PATH} (use --force to regenerate)")
        return 0

    if not os.path.exists(CACHE_PATH):
        print(f"Cache not found: {CACHE_PATH}")
        return 1
    with open(CACHE_PATH) as f:
        cache = json.load(f)
    businesses = cache["businesses"]

    rng = random.Random(SEED)
    warm = [(k, v) for k, v in businesses.items() if (v.get("lead_score") or {}).get("tier") == "Warm"]
    cold = [(k, v) for k, v in businesses.items() if (v.get("lead_score") or {}).get("tier") == "Cold"]
    unv = [(k, v) for k, v in businesses.items() if (v.get("lead_score") or {}).get("tier") == "Unverified"]

    fp_pool = [(k, v) for k, v in businesses.items() if is_false_positive_archetype(v)]
    cw_pool = [(k, v) for k, v in businesses.items() if is_contact_worthy_archetype(v)]
    print(f"pools: warm={len(warm)} cold={len(cold)} unverified={len(unv)} "
          f"fp_archetypes={len(fp_pool)} contact_worthy={len(cw_pool)}")

    # Forced: known false positives and contact-worthy prospects (task requires
    # ≥10 of each). Deterministic hash-order pick so it's stable.
    def hash_pick(pool, n):
        pool_sorted = sorted(pool, key=lambda kv: hashlib.sha1(kv[0].encode()).hexdigest())
        return pool_sorted[:n]

    forced_fp = hash_pick(fp_pool, 12)
    forced_cw = hash_pick(cw_pool, 14)
    forced_keys = {k for k, _ in forced_fp + forced_cw}

    # Random cross-section from the rest, target mix: ~10 Warm, ~10 Cold, ~8 Unverified
    remaining = [(k, v) for k, v in businesses.items() if k not in forced_keys]
    remaining_warm = [(k, v) for k, v in remaining if (v.get("lead_score") or {}).get("tier") == "Warm"]
    remaining_cold = [(k, v) for k, v in remaining if (v.get("lead_score") or {}).get("tier") == "Cold"]
    remaining_unv = [(k, v) for k, v in remaining if (v.get("lead_score") or {}).get("tier") == "Unverified"]

    budget = args.count - len(forced_keys)
    n_warm = min(10, len(remaining_warm))
    n_cold = min(10, len(remaining_cold))
    n_unv = min(8, len(remaining_unv))
    extra = budget - n_warm - n_cold - n_unv
    # top up from highest-scoring remaining Warm/Cold
    topup = sorted(remaining_warm + remaining_cold, key=lambda kv: (kv[1].get("lead_score") or {}).get("score", 0), reverse=True)[:max(0, extra)]

    sample_keys = forced_keys | {k for k, _ in pick(remaining_warm, n_warm, rng)}
    sample_keys |= {k for k, _ in pick(remaining_cold, n_cold, rng)}
    sample_keys |= {k for k, _ in pick(remaining_unv, n_unv, rng)}
    sample_keys |= {k for k, _ in topup}

    # Build anonymized, scoring-faithful fixture
    fixture = {}
    for key in sorted(sample_keys):
        b = businesses[key]
        sq = dict(b.get("site_quality") or {})
        if sq:
            # phones live at biz level; emails anonymized like biz emails
            sq = {k: v for k, v in sq.items() if k not in ("phones", "emails")}
        entry = {
            "id": key,
            "name": b.get("name", ""),
            "trade": b.get("trade", ""),
            "tier": (b.get("lead_score") or {}).get("tier", "Cold"),
            "score": (b.get("lead_score") or {}).get("score", 0),
            "reasons": (b.get("lead_score") or {}).get("reasons", []),
            "url": b.get("url", ""),
            "first_seen": b.get("first_seen", ""),
            "last_seen": b.get("last_seen", ""),
            "phones_anonymized": anonymize_phones(b.get("phones")),
            "emails_anonymized": anonymize_emails(b.get("emails")),
            "own_domains": b.get("own_domains", []),
            "hiring_role_match": b.get("hiring_role_match", False),
            "hiring_signals": b.get("hiring_signals", []),
            "review_negative": b.get("review_negative", False),
            "review_signals": b.get("review_signals", []),
            "site_quality": sq,
            "archetype": "false_positive" if key in {k for k, _ in forced_fp} else
                        "contact_worthy" if key in {k for k, _ in forced_cw} else "cross_section",
        }
        fixture[key] = entry

    payload = {
        "_meta": {
            "task": "SGW-861",
            "description": "Labeled prospect benchmark set for NWP Precision Lead Engine",
            "exported_from": CACHE_PATH,
            "cache_business_count": len(businesses),
            "count": len(fixture),
            "anonymization": "real phones/emails replaced with placeholders",
            "seed": SEED,
            "exported_at": __import__("datetime").datetime.now().isoformat(),
        },
        "prospects": fixture,
        "labels": {},  # filled by human/agent review in labels.json
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {len(fixture)} prospects to {OUT_PATH}")
    tiers = {}
    archetypes = {}
    for e in fixture.values():
        tiers[e["tier"]] = tiers.get(e["tier"], 0) + 1
        archetypes[e["archetype"]] = archetypes.get(e["archetype"], 0) + 1
    print("tiers:", tiers)
    print("archetypes:", archetypes)
    return 0


if __name__ == "__main__":
    sys.exit(main())

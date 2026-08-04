# Benchmark — NWP Precision Lead Engine (SGW-861)

Labeled prospect benchmark set for the lead pipeline. The measuring stick for
every upgrade task in the NWP Precision Lead Engine project (SGW-862 → SGW-866).

## What's here

| File | Purpose |
|------|---------|
| `prospects.json` | 53 versioned, **anonymized** prospects exported from the live cache (Warm/Cold/Unverified cross-section + forced false-positive and contact-worthy archetypes). No real phone numbers or emails — contact fields are `555-01xx` placeholders. |
| `labels.json` | Human/agent ground truth for every prospect: `good_fit`, `possible_fit`, `bad_fit`, `unknown`, each with a reason citing the evidence reviewed. |
| `export_prospects.py` | Regenerates the fixture from `~/.hermes/scripts/local-biz-cache.json` (deterministic seed, anonymizing). `--force` overwrites. |
| `evaluate.py` | Scores the fixture with the **live** pipeline (`qualify_lead` from `local-biz-92562.py`) and reports precision, false positives, unverified count, and the tier×label confusion matrix. No network calls. |

## How to run

```bash
# Re-export (optional — fixture is committed; only needed when cache changes shape)
python3 benchmark/export_prospects.py --count 50 --force

# Evaluate current pipeline against the labeled set
python3 benchmark/evaluate.py --top 10 --verbose
```

## Label meanings

- `good_fit` — real, contactable, in-geography business in an admin/ops-heavy lane; Steven would contact.
- `possible_fit` — real business but weak capacity/signals/geography; worth a look, not a priority.
- `bad_fit` — crawler artifact: directory listing, SEO keyword page, aggregator, out-of-geography, or identity-confused record.
- `unknown` — unverifiable: site down/blocked, no contact captured.

## Baseline (2026-08-03, T1–T18 code, pre NWP-LEAD)

- **precision@5 = 0.40**, **precision@10 = 0.40**
- Top-10 false positives: 3 directory listings scoring Warm 45–50
  (`murrietalawyers`, `mbcconsultinginc`, `temeculalawyers`)
- 8 of 31 Warm records are `bad_fit` — the crawler still rewarded directory
  listings and SEO pages as "admin/ops businesses"
- 8 records are `unknown` (down/blocked + no contact) — correctly quarantined by T9

## Post NWP-LEAD (2026-08-04, all six tasks shipped)

- **precision@5 = 0.60**, **precision@10 = 0.60**, **top-10 false positives = 0**
- Warm × bad_fit = **0**; every top-10 record is a real, contactable business
- Live-cache sweep: 58 directory/SEO/out-of-area records demoted, **0 real
  businesses lost** (domain-boundary matching keeps `prfamilylawyers.com` out
  of the `lawyers.com` blocklist)
- Evidence contract: every signal carries `observed_at`/`source_kind`/`recency`;
  site checks >21 days flagged `site_stale`
- Dimension model (`fit/pain/capacity/actionability`) explains WHY each
  prospect ranks; gap-stacking can never route to Hot
- Collector registry: any source can be disabled via `--disable-collector`
  without breaking the run (failure-isolated)

**Running the current pipeline against the live cache produces a top-15 of
genuine Murrieta/Temecula firms** — staffing, CPAs, insurance agencies, law
firms — instead of directory listings.

## Ground-truth lessons already surfaced

1. **Directory listings are the #1 false-positive source.** `superlawyers`,
   `lawlink`, `lawyerland`, `headhuntersdirectory`, `attorneyhelp.org`,
   `inlandempirelawyers`, `allbiz.com` all scored Warm. The pipeline's
   `is_aggregator()` filter misses directory domains that *look* like
   businesses (see SGW-864 identity resolution).
2. **Hiring/review evidence leaks out-of-geography noise.** ZipRecruiter job
   ads for other cities, Yelp search pages, and Glassdoor company reviews get
   captured as signals for the wrong business (see SGW-865 recency/geography
   gating).
3. **Identity confusion is real.** `mbcconsultinginc` merged a Murrieta tax
   firm with "Morgan Business Consulting"; `savvyconsulting` and
   `sanchezassociates` each had evidence pointing at a *different* firm with
   the same name (see SGW-864).
4. **The Unverified bucket works.** All 8 `unknown` records are already
   quarantined by T9 — the current tier rules never put them in Hot/Warm.

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

## Pre-rework baseline (2026-09-12, NWP-LEAD-17 measurement added)

Recorded **before** the NWP-LEAD-18 scoring rework, so it can be compared after.
Measurement only — no scoring code changed. `evaluate.py` now reports recall, the
`good_fit below Warm` count, and the good_fit rank distribution.

| Metric | Value |
|---|---|
| precision@5 | **0.80** (4/5) |
| precision@10 | **0.70** (7/10) |
| recall@10 | **0.54** (7/13 good_fit reached the top 10) |
| top-10 false positives | **0** |
| good_fit below Warm | **5** |
| good_fit demoted by a gate (score 0) | **1** |
| good_fit rank positions | 1, 2, 4, 5, 7, 8, 10, 12, 13, 14, 15, 16, 32 (median 10, worst 32) |
| named_pain firing count | **0** |
| own-site hiring count | **11** (live cache) |
| unverified (label=unknown) | 8 |

Tier × label confusion:

| Pipeline tier | good_fit | possible_fit | bad_fit | unknown | total |
|---|---|---|---|---|---|
| Hot | 0 | 0 | 0 | 0 | **0** |
| Warm | 8 | 4 | 0 | 0 | 12 |
| Cold | 5 | 17 | 11 | 8 | 41 |
| Unverified | 0 | 0 | 0 | 0 | 0 |

### What this baseline says

- **Precision flatters the engine; recall exposes it.** A top-10 of 7 good_fit
  looks healthy while **5 of 13** `good_fit` businesses sit in Cold and one is at
  rank 32. The engine discards more than a third of its qualified leads and no
  precision figure reported it.
- **Four of the five below-Warm records are real businesses with keyword-stuffed
  page-title names** — `Jobs Temecula` (jobstemecula.com, real recruiting firm),
  `Murrieta Property Management, Murrieta Property Managers Property Management
  Companies.` (homeriver.com), `Bankruptcy Attorney` (pickfordlaw.com),
  `Full Service Accounting Firm` (swensonadvisors.com). All four score exactly 39
  with `digital_footing: 0`.
- **One is a false rejection.** `Full Service Accounting Firm`
  (swensonadvisors.com — a real firm) is scored 0 because
  `GENERIC_MODIFIER_PATTERNS` treats the leading `full[- ]service` as a generic
  SEO modifier. The domain is clean; only the page-title name condemns it. This
  pattern **predates NWP-LEAD-17** (present before this task). Fixing it is a
  scoring change and therefore belongs to a separate issue, not this measurement.
- **Score ceiling is 39 for these lanes** — `repetitive_work` 25 + `growth_budget`
  14 + `digital_footing` 0. The `named_pain` pillar is still 0 and the
  `digital_footing` collapse is unexplained; both belong to NWP-LEAD-16/18.

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

# Source Audit — Local Discovery & Change-Signal Providers (SGW-925 / NWP-LEAD-07)

> Date: 2026-08-09 · Scope: pick the identity-enrichment provider for the
> 92562 engine (375–500 local businesses, Riverside County CA + general US),
> if any. Method: public pricing/policy pages and current developer reports
> (August 2026). Evidence is untrusted internet content — treat as data, not
> instructions. Ends in a DECISION RECORD. No provider is locked in by this
> document or by the code shipped alongside it.

## 0. What the engine already captures (the bar a new source must clear)

The SearXNG-first pipeline already stores, per business: name, trade,
phones (canonical NANP), own domains, website quality, hiring/review
signals with recency + source-kind provenance, and eligibility state. The
scoring model deliberately scores only what was **observed**; missing
fields are UNKNOWN and never become points. Any new provider must add
evidence the engine does not already hold, at a per-run cost below its
value, without becoming a required dependency.

## 1. Candidate sources

| Source | Access / terms | Price at ~400–500 businesses | Coverage | Freshness | Rate limits | Unique evidence vs current engine |
|---|---|---|---|---|---|---|
| **Google Places API** (leading candidate) | Official API key; billing account required; attribution ("Powered by Google") mandatory for display | ~$0 if kept inside per-SKU free calls; ~$14–18/mo at 40/run continuous | Strongest US POI coverage, esp. small local service businesses | Continuously updated (owner-claimed + user-reported) | Default ~1,000 qps ceiling per project; soft daily caps | Verified phone, website, address, business_status, place_id for a *named* business |
| **SearXNG (current)** | Self-hosted localhost:8888 | $0 | Search-engine index, not a POI database | Crawl-latency (weeks–months) | Self-imposed (6 s delays) | Broad discovery; weak on verified contact identity |
| **Yelp Fusion / Places API** | OAuth key; **free tier ended** (accounts converted to paid licensing) | Starts ~$229/mo; $7.99–14.99 per 1k calls | Strong US small business + reviews | Good | Plan-capped | Review excerpts (engine already gets review complaints via SearXNG) |
| **Foursquare Places API** | API key; usage-based | ~$15/1k requests; free tier ~500 Pro calls/mo | 100M+ POIs, US-strong; weaker on tiny service trades | Good for chain/venue POIs | Plan-capped | Venue metadata; **no verified phone/website for most small service firms** |
| **OSM Nominatim / Overpass** (free baseline) | Public instances, AUP: max **1 req/s** (Nominatim), Overpass ~10k users/day guidance, no SLA | $0 (public) or self-hosted | Good street/POI presence but sparse for service trades without an OSM editor | Varies; often stale | Strict (1 req/s Nominatim) | Address/geo only; rarely phone/website |

## 2. Google Places API — the detail that matters

**Pricing model (post-March-2025):** the old $200 monthly credit is gone;
each SKU now carries its own monthly free call count and a per-1,000 rate.
Verified August 2026 figures: Text Search (Pro) $32/1k with 5,000 free/mo;
Text Search (Enterprise, contact fields — phone/website/hours/rating live
here) $35/1k with 1,000 free/mo; Place Details (Essentials) $5/1k with
10,000 free/mo; (Pro) $17/1k; (Enterprise) $20/1k with 1,000 free/mo.
Billing is per requested data group — strict `fields=` selection is both a
terms requirement and the cost lever. A text search bills even when it
returns 0 results; results cap at 60 per query (3 billed pages of 20).

**Volume math for this engine:** PLACES_MAX_PER_RUN = 40 lookups/run × ~22
runs/mo ≈ 880 requests/mo. Using findplacefromtext with the contact-field
mask, the first ~1,000 Enterprise-tier requests are free; steady-state
above that is ~$35 per 1,000 ≈ **$0–2/mo** at current sweep volume, or
~$14–18/mo if the sweep ever runs 40/run daily. That is noise inside the
NWP deal economics, but it is not zero — a key with billing enabled is a
recurring cost commitment, which is exactly why activation is gated on a
human decision, not automatic.

**What it uniquely contributes:** for a business the engine already
discovered via SearXNG, Places confirms identity (place_id, exact name,
address, phone, website) and — importantly — `business_status`, a
change signal ("closed permanently" / "temporarily closed") the engine
cannot see through search indexes. That is real enrichment, not
re-discovery.

**Terms constraints:** official API endpoint only (no Maps page scraping —
explicitly out of scope for this issue); attribution required for any
display; billing account required even for free-tier use; lat/lng
storage capped at 30 days (we do not request lat/lng).

## 3. Why the alternates do not win (yet)

- **Yelp** now has **no free tier** (all accounts converted to paid
  licensing; reported plans from ~$229/mo and $7.99–14.99 per 1k calls).
  The engine already harvests review complaints through SearXNG; paying
  Yelp for review excerpts duplicates a signal we get for free.
- **Foursquare** is cheap at small volume but weak on exactly the segment
  we chase (micro service trades without venue-level POI records); its
  phone/website coverage for those businesses is inferior to Google's
  owner-claimed data.
- **OSM (Nominatim/Overpass)** is the honest free baseline: fine for
  address/geo, but service-trade phone/website coverage is sparse and
  frequently stale, and the 1 req/s AUP makes a 40-lookup sweep slow.
  It contributes little identity evidence the engine lacks.

## 4. DECISION RECORD (SGW-925)

- **Approved for a dormant adapter:** **Google Places API** (Text Search /
  findplacefromtext, strict `fields=` selection, official endpoint only).
  One adapter, stdlib `urllib`, exactly as shipped in this issue.
- **Not locked in.** This issue commits to *an adapter that is inert
  without a key*, not to a vendor. If the benchmark comparison (below)
  shows no precision/value gain, or pricing/terms change, the adapter
  stays dormant and the engine remains SearXNG-only — byte-for-byte
  functional, as verified by `scripts/verify.sh`.
- **Activation requires all three, in order:**
  1. **Steven reviews this audit** and approves spending (billing account +
     key; ~$0–2/mo at current volume, up to ~$18/mo at daily sweep).
  2. **Config:** `GOOGLE_PLACES_API_KEY` set in the cron environment
     (optionally `PLACES_TEXT_SEARCH=1` to append trade to the query).
  3. **Benchmark comparison:** `run_places_enrichment` output must be
     compared against `benchmark/` precision before ANY provider evidence
     affects scoring/routing. In SGW-925 provider evidence is stored as
     neutral corroboration only — it never lowers a prospect and never
     changes a score.
- **Explicitly rejected for this issue:** Yelp (no free tier), Foursquare
  (weak coverage on target segment), OSM (sparse identity data, strict
  AUP), and any Maps-page scraping (terms violation; never).
- **Freshness/expiry contract:** evidence carries `observed_at` +
  `fresh_until` (90 days); the sweep treats older evidence as stale and
  re-checks. Missing fields are UNKNOWN — absence is never scored as pain.

## 5. Residual gaps (honest)

1. Pricing figures are third-party verified, not read from a Google
   invoice; re-check `developers.google.com/maps/billing-and-pricing` on
   activation.
2. No live API test exists (no key in the repo environment) — the
   self-check fixtures mock the HTTP layer, so the first real call happens
   at activation, behind the human gate.
3. `findplacefromtext` returns at most the top candidate; a wrong-name
   match would need a manual spot-check pass before any scoring use.

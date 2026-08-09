# Lead Scraper

Local business lead generation pipeline for North Web Pro. Scrapes SearXNG for businesses in a target ZIP code, audits their websites, and scores **buying readiness** — not website quality.

North Web Pro **diagnoses and removes operational drag** — the manual work that
slows a business down (phone answering, scheduling, intake, follow-up, data
entry). The fix may be a process change, better use of existing software, an
integration, automation, custom software, or AI — the implementation tool is
chosen **after** the diagnosis. The pipeline ranks businesses by how much
operational drag they carry and how likely they are to act, not by how easy
they were to crawl.

## What It Does

1. **Crawls** SearXNG for local businesses across trades and admin/ops verticals (plumbing, HVAC, accounting, law, insurance, property management, recruiting, and more)
2. **Audits** each business website with deep fetching (150KB, 20s timeout, /contact + /about, JSON-LD parsing, JS-shell detection)
3. **Scores** buying readiness on a 5-pillar model (see below) — only scoring what was actually observed
4. **Reports** via styled HTML (North Web Pro branding) with pitch lines, Unverified bucket, and actionable sort order

## Buying-Readiness Score (0-100)

Weights live in `SCORING` at the top of `local-biz-92562.py`. Edit there; see `PRD.md §2` for the full philosophy.

| Pillar | Max | What It Measures |
|--------|-----|-----------------|
| **Repetitive-work load** | 35 | Admin/ops trade (+25); appointment trade with no booking system (+10) |
| **Named pain** | 25 | Responsiveness complaints in reviews — corroborated across ≥2 results |
| **Growth & budget** | 25 | Hiring an automatable role (+25); generic hiring (+12); multi-location/phone (+8); trade prior (+5-8) |
| **Digital footing** | 15 | Website score gap (verified only); down site capped at +3; blocked at +5 |
| **Contactability** | gate | No phone AND no email → tier capped at Cold |

### Tier rules

- **Hot** = contactable AND (named pain OR automatable-role hiring OR verified admin/ops) AND total ≥ 65
- **Warm** = contactable AND total ≥ 40
- **Cold** = everything else, including any non-contactable business
- **Unverified** = site unreadable + no external signal + no contact info — collapsed in the report

Every Hot/Warm card shows a **"Pitch this:"** line derived from the top signal, not raw scoring internals.

## Architecture

- **Incremental crawler**: 6 query groups (A-F) rotate across runs. Each run does 3-4 queries with 6-second delays to respect SearXNG rate limits.
- **Zero LLM tokens at runtime**: All Python, deterministic. Runs via Hermes cron with `no_agent: true`. The optional AI reviewer (SGW-940) is opt-in, advisory, and never required for a report.
- **JSON cache**: `~/.hermes/scripts/local-biz-cache.json` — Hot/Warm leads kept 30 days, Cold 7 days; signals pruned after 14 days
- **HTML reports**: Dark North Web Pro branded, written to `~/.hermes/scripts/reports/`
- **pip allowed**: Install on Hermes before use; justify any new dep against what stdlib already does

## AI review (SGW-940)

Grounding rules: the deterministic pipeline and report are fully functional with AI **disabled** — the engine has zero LLM runtime dependence by default. `--ai-review` is an explicit opt-in second-pass pass over the top N eligible/research candidates (bounded, score-ordered).

- Env: `AI_REVIEW_BASE_URL` (default `https://ollama.com/v1`, OpenAI-compatible `/chat/completions`), `AI_REVIEW_MODEL`, optional `AI_REVIEW_API_KEY`, `AI_REVIEW_MAX_CANDIDATES` (default 15), `AI_REVIEW_MAX_TOKENS`, `AI_REVIEW_TIMEOUT`. Unconfigured → log one line, skip, exit 0.
- Output contract per prospect: `decision` (priority/research/watch/reject/abstain), `confidence`, `evidence_refs[]`, `bottleneck_hypothesis` (labeled), `why_now`, `recommended_first_offer`, `email_draft`, `phone_opener`, `missing_evidence[]`, `abstain_reason` — stored under `biz["ai_review"]` with model/provider/latency/tokens metadata.
- Anti-fabrication: `evidence_refs` must map to evidence actually captured on the record; a `priority` verdict without substantive evidence (verified site read, automation gaps, hiring/review evidence, provider corroboration) is downgraded to abstain. The reviewer NEVER changes `lead_score` or `eligibility_state` — it is advisory only. No outbound writes.
- Deployment host is not decided here (Tahoe pending its runtime audit); the module is host-agnostic stdlib.

## Eligibility gate (SGW-941)

Before any business can be owner-facing, it must pass a deterministic gate:
- **rejected** — government/public agencies, corporate locator/job subdomains
  (`agents.*`, `jobs.*`, `careers.*`), national-enterprise branches (parent
  platform is not the prospect), and directory/SEO listings. These never enter
  the cache at crawl time and are swept to Cold (score 0, evidence preserved)
  on every load.
- **research** — no verified contact path yet, or no domain/identity to
  verify. Routed to the collapsed "Research Needed" report section, never Warm.
- **eligible** — distinct local operating business with a contact path.

The gate re-runs on every cache load, so junk that slips crawl-time checks is
caught on the next run. `eligibility_state` / `eligibility_reason` are
persisted per record for debugging.

## Sources (SGW-925)

- **SearXNG (localhost:8888)** is the default and only **required** source —
  discovery, website audit, hiring/review signals, buying signals.
- **Google Places identity enrichment is dormant.** It activates only when
  `GOOGLE_PLACES_API_KEY` is set **and** `--places` is passed (bounded by
  `PLACES_MAX_PER_RUN`); without a key it logs one line and exits 0 with zero
  requests. Provider evidence is stored under `provider_evidence` as neutral
  corroboration only — it is **not** enabled for scoring or routing yet,
  pending the benchmark comparison required by
  `docs/source-audit-2026-08.md`. **No provider is locked in.**

## Website site check (sub-input, not the headline metric)

The 0-5 site check is a sub-input to Digital Footing, not the primary ranking. A great website doesn't disqualify a lead — a business can have a beautiful site and still drown in manual intake.

| Points | Check |
|--------|-------|
| +1 | Mobile viewport |
| +1 | Click-to-call (tel:) |
| +1 | Contact page |
| +1 | 200+ words of content |
| +1 | Booking or live chat |

## Usage

```bash
# Crawl + generate HTML report
python3 local-biz-92562.py --html --backup

# Generate HTML from cache only (no crawl)
python3 local-biz-92562.py --briefing --html

# Generate text briefing from cache
python3 local-biz-92562.py --briefing

# Force a specific query group (0-5)
python3 local-biz-92562.py --group 0 --html
```

## Cron Configuration

```yaml
# Hermes cron job (already configured)
job_id: 4b49f990a0cf
name: 92562-local-biz-briefing
schedule: "0 6,14,22 * * 1-5"  # 6AM, 2PM, 10PM PT weekdays
deliver: telegram:-1003913783231:11
no_agent: true
script: local-biz-92562.py
```

Delivery: the cron job's stdout is delivered as a text line (the script
deliberately emits no MEDIA: tag on stdout); the HTML file attachment is sent
by the script itself via `hermes send` to the same target (`REPORT_TARGET` in
`local-biz-92562.py`). One target, one file-delivery owner.

## Tech Stack

- Python 3 (pip allowed; install deps on Hermes before use)
- SearXNG (localhost:8888) for search
- JSON cache with tier-aware TTL
- HTML/CSS inline for reports (no external assets)

## Legacy

The `legacy/` folder contains the older construction lead scout and AI employee prospect finder pipelines. Paused and replaced by the unified local-biz scout.

## License

© North Web Pro

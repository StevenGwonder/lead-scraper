# Lead Scraper

Local business lead generation pipeline for North Web Pro. Scrapes SearXNG for businesses in a target ZIP code, audits their websites, and scores **buying readiness** — not website quality.

North Web Pro sells **custom AI agents on retainer** that remove manual operational drag (phone answering, scheduling, intake, follow-up, data entry). The pipeline ranks businesses by how likely they are to buy that, not by how easy they were to crawl.

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
- **Zero LLM tokens at runtime**: All Python, no AI. Runs via Hermes cron with `no_agent: true`. LLMs are only used by developers editing this code.
- **JSON cache**: `~/.hermes/scripts/local-biz-cache.json` — Hot/Warm leads kept 30 days, Cold 7 days; signals pruned after 14 days
- **HTML reports**: Dark North Web Pro branded, written to `~/.hermes/scripts/reports/`
- **pip allowed**: Install on Hermes before use; justify any new dep against what stdlib already does

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
deliver: telegram:-5131689526
no_agent: true
script: local-biz-92562.py
```

## Tech Stack

- Python 3 (pip allowed; install deps on Hermes before use)
- SearXNG (localhost:8888) for search
- JSON cache with tier-aware TTL
- HTML/CSS inline for reports (no external assets)

## Legacy

The `legacy/` folder contains the older construction lead scout and AI employee prospect finder pipelines. Paused and replaced by the unified local-biz scout.

## License

© North Web Pro

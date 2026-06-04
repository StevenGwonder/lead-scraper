# Lead Scraper

Local business lead generation pipeline for North Web Pro. Scrapes SearXNG for businesses in a target ZIP code, audits their websites, and delivers styled HTML reports.

## What It Does

1. **Crawls** SearXNG for local businesses across 10 trades (plumbing, HVAC, electrical, landscaping, roofing, auto, tree, painting, carpet, handyman)
2. **Audits** each business website on 5 criteria (mobile, click-to-call, contact, content depth, booking/chat)
3. **Caches** results in a JSON file with 7-day expiry
4. **Reports** via styled HTML (North Web Pro branding) or text briefing

## Architecture

- **Incremental crawler**: 4 query groups rotate across 3 daily runs (6AM, 2PM, 10PM PT). Each run does 3-4 queries with 6-second delays to avoid SearXNG rate limiting.
- **Zero LLM tokens**: All Python, no AI. Runs via Hermes cron with `no_agent: true`.
- **JSON cache**: `~/.hermes/scripts/local-biz-cache.json` — 7-day TTL, timestamped backups in `~/.hermes/scripts/reports/`
- **HTML reports**: Dark wilderness-themed, North Web Pro branded, written to `~/.hermes/scripts/reports/`

## Usage

```bash
# Crawl + generate HTML report
python3 local-biz-92562.py --html --backup

# Generate HTML from cache only (no crawl)
python3 local-biz-92562.py --briefing --html

# Generate text briefing from cache
python3 local-biz-92562.py --briefing

# Force a specific query group (0-3)
python3 local-biz-92562.py --group 0 --html

# Custom area
python3 local-biz-92562.py --zip 92563 --html
```

## Website Quality Score (0-5)

| Points | Check | Why It Matters |
|--------|-------|----------------|
| +1 | Mobile viewport | Google penalizes non-mobile sites |
| +1 | Click-to-call (tel:) | Contractors get calls — missing = lost leads |
| +1 | Contact page | Can't reach them = lost business |
| +1 | 200+ words of content | Thin sites rank poorly |
| +1 | Booking or live chat | Automation-ready = upsell target |

**Score tiers:** -1 = SITE DOWN | 0-2 = BAD | 3 = OK (upsell) | 4-5 = GOOD

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

- Python 3 stdlib only (no pip dependencies)
- SearXNG (localhost:8888) for search
- JSON for cache (structured backups kept, last 10)
- HTML/CSS inline for reports (no external assets)

## Legacy

The `legacy/` folder contains the older construction lead scout and AI employee prospect finder pipelines. These were paused and replaced by the unified local-biz scout.

## License

© North Web Pro
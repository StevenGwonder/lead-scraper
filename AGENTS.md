# AGENTS.md — Working rules for agents on this repo

> Conventional filename. If you prefer `agent.md`, this is the same document — keep
> only one to avoid drift.

This repo is a **cron-driven lead-generation pipeline for North Web Pro**, an
**AI consultancy**. The seller's offer is **custom software and AI agents on
retainer** that remove repetitive, manual, human-bottlenecked work — phone
answering, scheduling, intake, follow-up, data entry, document handling — so a
client can move onto the new digital frontier instead of being slowed by old
ways and bad habits. **North Web Pro does not sell websites.** A site audit is
only useful here as evidence of *manual operational drag*, never as the product.

Read this before touching code. Then pick a task from `PRD.md`.

---

## 1. The one rule that governs every change

**Score and rank businesses by how likely they are to BUY AND RETAIN a
digital-worker retainer — not by how easy it was for the crawler to find a gap.**

The current code inverts this: a website that is **down** or **bot-blocked**
auto-scores into the Hot tier because "no site = big opportunity." That is a
crawler-success artifact, not a buying signal. A business with no working site is
usually defunct or a one-person phone-and-word-of-mouth shop — *no budget, no
systems to integrate with, worst retainer prospect.*

Before you add or reweight any signal, ask: **"Does this measure repetitive work
volume, a named pain, or ability to pay?"** If not, it does not belong in the
score.

## 1b. The second rule: never score what you couldn't observe

Most "Hot" leads today are **the crawler's own failures scored as opportunity** —
a timeout, a TLS error, a 400/403, or a JavaScript-rendered page the scraper
can't read all collapse into `status="down"/"blocked"` and earn +40 points. The
script is mining its own error log.

Every signal has **three** states, and they must never be merged:

| State | Meaning | Scores |
|-------|---------|--------|
| **PRESENT** | We *observed* it (saw the phone, the job posting, the complaint) | The only thing that earns buying points |
| **ABSENT** | We fully read the page and it genuinely isn't there | Neutral / weak at most |
| **UNKNOWN** | Error, blocked, timeout, or JS shell — we couldn't tell | **Zero points. Quarantined. Never Hot.** |

Rules that follow from this:
- **Confidence-gate every positive point.** A point may only be awarded when the
  source was actually read (`confidence == "high"`, `status == "up"`, or a
  structured/external source like a job posting). Down/blocked/error/JS-shell →
  zero, routed to the **Unverified** bucket.
- **Absence is not evidence.** "We didn't find a phone in the first 5 KB of the
  homepage" is not "no phone." Read more, read the contact page, parse JSON-LD —
  or report UNKNOWN, never a confident negative.
- **Corroborate.** A soft signal counts only if seen in ≥2 independent places, or
  it comes from a structured source (job posting, schema.org JSON-LD).

---

## 2. The Ideal Customer Profile (ICP)

A great retainer client carries **manual operational drag that software/agents
remove**, and shows it through signals ranked by how *verifiable* they are (so we
score facts, not guesses):

**Tier 1 — structured / externally verifiable (trust these):**
- **Job postings for automatable roles** — receptionist, scheduler, intake
  coordinator, dispatcher, data-entry, AR/AP clerk, office/appointment
  coordinator. A real posting is hard evidence they are about to pay a human
  $40–60k/yr for work an agent does. Strongest signal; corroborated by a source,
  not inferred from a failed fetch.
- **Multiple locations / multiple phone lines / a careers page** → operational
  complexity + budget for a retainer.

**Tier 2 — named pain (trust when corroborated about the right business, recent):**
- Review complaints about responsiveness — "no callback," "slow," "couldn't reach
  them," "waited days." Exactly the pain our agents remove, stated by the customer.

**Tier 3 — manual-process tells (supporting evidence only, and only when the page
was actually read — confidence high):**
- "Call to book" with no online scheduling; "download this PDF and email it back";
  "allow 24–48 hours for a reply"; fax; a contact form that clearly routes to a
  person. These signal "old ways," but never count them from a blocked/UNKNOWN fetch.

Note: a beautiful website does **not** disqualify a lead — a business can have a
great site and still drown in manual intake. We are not grading websites.

A lead you **cannot contact** (no phone, no email) is not a lead. Contactability
is a gate, not a bonus.

---

## 3. Hard constraints (do not break these)

- **Python 3 standard library only.** No pip installs. The script runs unattended
  inside a VirtualBox on an old iMac (host "Hermes") via cron. Keep it light and
  synchronous; assume modest CPU and that long runs are fine but heavy deps are not.
- **Zero LLM tokens at runtime.** The cron job runs with `no_agent: true`. All
  scoring is deterministic Python. (LLMs may be used by *developers* to edit this
  code — that is what `PRD.md` task prompts are for — but never called from the
  script itself.)
- **Single file for the pipeline:** `local-biz-92562.py`. Keep it self-contained.
- **Cache schema is append-compatible.** `~/.hermes/scripts/local-biz-cache.json`
  persists across runs. When you add a field, default it safely
  (`biz.get("field", default)`) so old cache entries don't crash new code.
- **SearXNG is rate-limited.** It lives at `http://localhost:8888`. Respect the
  6-second delays and per-run query caps. Don't add query volume without removing
  some.
- **Read-only crawling only.** No posting, no form submission, no contacting the
  businesses. This tool finds and ranks leads; a human does outreach.

---

## 4. Layout

| Path | What it is |
|------|------------|
| `local-biz-92562.py` | The live pipeline (crawl → audit → score → HTML report → Telegram). The only file cron runs. |
| `PRD.md` | The rebalance plan + numbered, self-contained task prompts. **Start here.** |
| `AGENTS.md` | This file. |
| `README.md` | User-facing overview + cron config. Update it when behavior changes. |
| `legacy/` | Paused predecessors (construction scout, prospect finder, old `qualify.py`). Reference only — do not wire back in. |

Key functions in `local-biz-92562.py`:
- `qualify_lead()` (~line 517) — **the scoring brain. Most rebalance work is here.**
- `check_website()` (~line 345) — site audit / status / tool detection.
- `search_hiring_signals()` / `search_review_signals()` (~line 432 / 476) — the
  signal searches that should drive the score but currently barely do.
- `generate_html_report()` (~line 886) — the "narrative" the salesperson reads.
- `main()` (~line 1126) — crawl loop, website-check loop, signal loop, save.

---

## 5. How to execute a PRD task (for a small/cheap LLM)

1. Open `PRD.md`, find the lowest-numbered **unchecked** task (`- [ ]`).
2. Read **only** the files and line ranges that task names. Do not refactor outside
   its scope.
3. Make the change. Obey every item in "Constraints" (§3) and "Acceptance criteria"
   in the task.
4. Self-check against the task's acceptance criteria. Run
   `python3 -c "import ast; ast.parse(open('local-biz-92562.py').read())"` to
   confirm it still parses.
5. Mark the task `- [x]` in `PRD.md` and write a one-line note under it describing
   what changed.
6. Commit with message `task(Tn): <short summary>`. One task per commit.

If a task is ambiguous or seems to conflict with §1 (the one rule), **stop and ask
a human** instead of guessing. Do not invent new signals that aren't in the PRD.

---

## 6. Style notes

- The codebase calls its cleanup passes "ponytail" — terse, dependency-free,
  collapse-the-duplicates. Match it: prefer a small constant/table over scattered
  `if`s.
- Put tunable weights in **declarative tables at the top of the file**, never as
  magic numbers buried in logic. The whole point of the rebalance is that the ICP
  philosophy should be editable in one place.
- Keep `log()` messages to stderr; stdout is for the report path only.
- Branch for all work: `claude/cron-script-lead-capture-yeqpn4`. Never push to
  `main` without explicit permission. Never open a PR unless asked.

# PRD — Rebalancing Lead Scout for *actual* leads, not crawler artifacts

**Owner:** North Web Pro (AI consultancy — custom software + AI agents on retainer
that remove manual operational drag; **not a website seller**)
**Pipeline:** `local-biz-92562.py`
**Branch:** `claude/cron-script-lead-capture-yeqpn4`
**Status:** Proposed — tasks below are unstarted.

---

## 1. Problem

The scout finds local businesses, audits their sites, and scores "buying
readiness" 0–100. In practice the score measures **how easy it was for the
crawler to find a gap**, not how likely a business is to buy a digital-worker
retainer. Two failure modes dominate the Hot tier:

- **"Down / blocked site" → instant Hot.** A site that won't load scores
  `+25 automation + +15 digital gap = 40 points before any real signal`
  (`qualify_lead`, lines ~539 and ~613). But a dead or invisible site usually
  means a defunct or one-person business — *no budget, nothing to integrate
  with.* The crawler's *failure to fetch* is being read as opportunity.
- **"Missing generic tooling" → Hot.** `no CRM +15`, `no marketing +10`,
  `no analytics +5` (lines ~551–556). This is web-agency logic ("you lack a
  tool, buy one"). For an **automation retainer** it's mostly noise — especially
  `no analytics`, which says nothing about repetitive-work volume.

Meanwhile the signals that actually predict an automation buyer —
**admin/ops workload, responsiveness complaints, and hiring for automatable
roles** — are collected but underweighted, double-counted, or detected so
loosely they're nearly random.

**The deeper defect — hallucination from errors.** The "no page / no phone / site
down" results are frequently false, and the cause is systematic, not random: the
crawler treats **"we couldn't read it"** the same as **"it isn't there,"** and
then scores that absence as opportunity. A timeout, a TLS failure, a 400/403, or a
JavaScript-rendered page all become `status="down"/"blocked"` → +40 points. The
script collects a `confidence` field and then **never reads it.** This is why a
site that looks fine in your browser comes back as "no phone, site down" — it was
read 5 KB deep, homepage-only, in 12 seconds, un-rendered. See the new **Signal
Reliability** section (§2b) and findings F13–F18.

**Positioning correction.** North Web Pro is an **AI consultancy**, not a website
seller. The "Website Quality 0–5" rubric is demoted from headline metric to a
minor *"is the page even real / is it a manual operation"* check. Buyer signals
are reframed around manual operational drag (job postings for automatable roles,
responsiveness complaints, manual-process tells).

**Goal:** Re-point the *navigation* (scoring + sort order), the *narrative* (the
HTML report), **and the *reliability*** (only score what we actually observed) at
real buying readiness — so the operator's time goes to businesses that genuinely
carry automatable work and can pay monthly.

---

## 2. The rebalanced model (target state)

Replace the current 4-pillar score with a 5-pillar model whose every point maps
to the ICP (see `AGENTS.md` §2). All weights live in **one declarative table**
at the top of the file.

| Pillar | Max | Measures | Key inputs |
|--------|-----|----------|------------|
| **Repetitive-work load** | 35 | Volume of boring, automatable work | Admin/ops trade (+25); appointment-heavy trade with no booking system (+10) |
| **Named pain** | 25 | Customer-stated pain a digital worker fixes | Responsiveness complaints in reviews (+25); other negative reviews (+10) |
| **Growth & budget** | 25 | Can they pay a retainer; are they straining | Hiring an automatable role (+25); hiring generally (+12); multi-location / multi-phone / team page (+8) |
| **Digital footing** | 15 | Enough maturity to integrate with, real gaps | UP site with tooling but clear gaps (up to +15); DOWN site **capped at +3**; bot-blocked +5 |
| **Contactability** | gate | Can a human reach them at all | Phone or email required for Warm+; absent → max tier = Cold |

### Tier rules (the "navigation")
- **Hot** = contactable **AND** (named pain **OR** automatable-role hiring **OR**
  admin/ops) **AND** total ≥ 65. Gap-stacking alone can no longer reach Hot.
- **Warm** = contactable AND total ≥ 40.
- **Cold** = everything else, including any non-contactable business and any
  business whose only points came from a down/blocked site.
- **Unverified** (new bucket) = site down/blocked AND no independent signal
  (no phone/email, no reviews, no hiring). These leave the Hot/Warm stream
  entirely and go to a collapsed "couldn't verify — low confidence" section.

### Narrative rebalance (the report)
- Sort by **actionability**, not raw score: contactable + named-pain leads first.
- Each card shows a **"Pitch this:"** line — the concrete boring task to offer to
  automate — derived from the top signal, *not* scoring internals like
  `no CRM (+15)`.
- Move down/blocked sites out of Hot into the **Unverified** section.
- Keep the existing North Web Pro styling; only the ordering, bucketing, and the
  per-card pitch line change.

---

## 2b. Signal Reliability & Confidence Gating (the anti-hallucination layer)

Every signal has **three** states; the code currently has only two ("found" /
"not found") and scores the merge as opportunity.

| State | Meaning | Scores |
|-------|---------|--------|
| **PRESENT** | We observed it (saw phone, job posting, complaint) | Earns buying points |
| **ABSENT** | We fully read the source and it isn't there | Neutral / weak |
| **UNKNOWN** | Error / blocked / timeout / JS shell | **Zero. Quarantined. Never Hot.** |

Target behavior:

1. **Confidence gate.** `qualify_lead` awards positive points only when the
   evidence was actually observed: `status == "up"` and `confidence == "high"`,
   or the signal came from a structured/external source (job posting, JSON-LD).
   Down/blocked/error/JS-shell → 0 points → **Unverified** bucket.
2. **Stop confidently mislabeling errors.** A fetch that times out, fails TLS, or
   returns 400/500 must be reported as **UNKNOWN/low confidence**, not
   `("down", "high")`. Down ("we connected and the site is genuinely dead/parked")
   must be distinguished from unreachable ("we couldn't connect").
3. **Read enough to be honest.** Raise the read budget (12 KB → ~150 KB), raise
   timeout (12 s → ~20 s) with one retry, fetch `/contact` and `/about`, and try
   www↔non-www before concluding anything is "missing."
4. **Parse structured data.** Extract `<script type="application/ld+json">`
   (`LocalBusiness` → `telephone`, `address`, `openingHours`, `sameAs`). This is
   far more reliable than regex on rendered text and kills most false "no phone."
5. **Detect JS shells** (tiny text + `id="root"` / `__NEXT_DATA__` / wix /
   squarespace markers) → mark UNKNOWN, never "thin content."
6. **Exhaust all UAs/schemes** before declaring "blocked."
7. **Corroborate soft signals** — count a Tier-2/3 signal only if seen in ≥2
   independent places or from a structured source.
8. **Drop `no analytics` / `no marketing` from scoring** — unmeasurable reliably
   and irrelevant to AI consulting (keep detection for display only).

---

## 3. Findings (catalogue — each maps to a task)

| # | Finding | Severity | Location |
|---|---------|----------|----------|
| F1 | Down/blocked site auto-scores into Hot (crawler artifact read as opportunity) | High | `qualify_lead` ~539, ~613 |
| F2 | Generic tooling gaps (esp. `no analytics`) inflate score with agency-logic noise | High | ~551–556 |
| F3 | Hiring detection is near-random: searches `"{name} hiring"` then matches "hiring"/"jobs" — echoes the query back; ignores role type and recency | High | `search_hiring_signals` ~432 |
| F4 | Hiring branch double-counts: "checked, none found" and "not checked" run identical trade-prior code, so real hiring barely changes the score | High | ~577–600 |
| F5 | "New" count/badges always render 0 on crawl runs: `last_run` is overwritten to `now` before the report reads it, so `first_seen > cutoff` is never true | Med | ~896, ~902, ~1344 |
| F6 | Hot/Warm leads silently expire: flat 7-day TTL by `last_seen` deletes qualified, expensively-researched leads when SearXNG stops surfacing them | Med | `load_cache` ~676–680 |
| F7 | `signals` and `fb_groups` never pruned — cache grows unbounded | Low | `load_cache` ~677 |
| F8 | Name-only dedup key collides: distinct businesses without domains merge | Low | ~1197 |
| F9 | Contactability is a +bonus, not a gate — uncontactable businesses can rank Hot | Med | `qualify_lead` ~639–654 |
| F10 | Job boards excluded as aggregators — discards the richest automatable-role signal for this ICP | Med | `AGGREGATOR_DOMAINS`, signal filter |
| F11 | Report reasons are scoring internals, not pitch angles | Med | `render_lead_card` ~1000 |
| F12 | Dead code/comment drift: `sq.get(...,"issues")` legacy key (~536); phone comment claims 800/888 skip but code skips 000/999 (~277) | Low | as noted |
| F13 | `confidence` field is collected then **never read** by scoring — low-confidence guesses score like verified facts | High | `qualify_lead` vs `check_website` |
| F14 | Unreachable sites are confidently labeled `("down","high")` — timeout/TLS/exception all become "site down, high confidence" → +40 | High | `check_website` ~427–429 |
| F15 | 400/500 (often the server rejecting our own request) → `"blocked"` → scored as opportunity | High | `check_website` ~426 |
| F16 | Truncated/partial reads cause false negatives: 12 KB page cap (~355), 5 KB phone scan (~404), homepage-only (no /contact, /about), 12 s timeout | High | `check_website` 354–404 |
| F17 | JS-rendered sites read as empty → false "thin content / not mobile / no tools"; first 403 returns "blocked" without trying other UAs/schemes | High | `check_website` ~361, ~421–423 |
| F18 | No structured-data parsing (JSON-LD `LocalBusiness`) — most reliable phone/address/hours source is ignored | Med | `check_website` |

---

## 4. Tasks — self-contained prompts for a small LLM

Rules for every task: **stdlib only; don't break the cache schema (default new
fields with `.get`); one task per commit; verify the file still parses with
`python3 -c "import ast; ast.parse(open('local-biz-92562.py').read())"`; mark the
task `- [x]` and add a one-line note when done.** Do tasks in order — later ones
assume earlier ones landed.

---

### - [x] T1 — Extract all scoring weights into one declarative table
> Done: added module-level `SCORING` dict; `qualify_lead` now reads every weight
> from it. Pure refactor — verified identical scores (collapsed the two duplicate
> hiring-fallback branches).

**Prompt:** In `local-biz-92562.py`, add a single module-level dict named
`SCORING` near the other top-level constants (after `REVIEW_COMPLAINT_KEYWORDS`,
~line 142). Move every magic number currently inside `qualify_lead` (~line 517)
into it, grouped by pillar: `repetitive_work`, `named_pain`, `growth_budget`,
`digital_footing`, plus a `tiers` sub-dict with `hot`, `warm` thresholds. Then
rewrite `qualify_lead` to read weights from `SCORING` instead of literals. **Do
not change any numeric value yet** — this is a pure refactor; the score output
must be identical to before. Add a short comment above `SCORING` saying "Edit the
ICP philosophy here — see PRD.md §2."
**Acceptance:** No literal point values remain inside `qualify_lead`; running the
script on an existing cache (`--briefing --html` won't crawl) produces the same
tiers as before this change.

---

### - [ ] T2 — Cap the down/blocked-site bonus (fixes F1)
**Prompt:** In `qualify_lead`, change the status handling so a **down** site adds
at most `SCORING["digital_footing"]["site_down"]` = **3** (not 25), and a
**blocked** site adds **5**. Remove the `+25` automation award for down sites and
the `+15` digital-gap award for down sites. A down/blocked site must no longer be
able to reach Hot on its own. Keep the honest status string for display.
**Acceptance:** A business whose only data is `status="down"` and no phone/email/
reviews/hiring scores ≤ 10 total and lands in Cold or Unverified, never Hot.

---

### - [ ] T3 — Replace tooling-gap scoring with ICP pillars (fixes F2)
**Prompt:** Rewrite the automation-readiness section of `qualify_lead` into the
**Repetitive-work load** pillar (max 35): admin/ops trade (`trade in ADMIN_TRADES`)
→ +25; an appointment-heavy trade (`HVAC`, `Plumbing`, `Auto Repair`, `Carpet
Cleaning`, `Handyman`) with `no booking system`/`no booking/chat system` in gaps
→ +10. **Delete `no analytics` and `no marketing tools` from scoring entirely**
(keep detecting them for display only). `no CRM` is no longer scored on its own.
Pull all values from `SCORING["repetitive_work"]`.
**Acceptance:** `no analytics` and `no marketing tools` contribute 0 points; an
admin/ops business with a working site scores ≥ 25 on this pillar.

---

### - [ ] T4 — Make contactability a gate, not a bonus (fixes F9)
**Prompt:** In `qualify_lead`, after computing the total, enforce: if the business
has **no phone and no email** (`biz.get("phones")` empty and
`biz.get("emails")`/`sq.get("emails")` empty), cap its tier at **Cold**
regardless of score, and append the reason "no contact info — can't reach". Keep
the numeric score for reference but never assign Hot/Warm without a phone or email.
**Acceptance:** A high-scoring business with no phone and no email returns
`tier == "Cold"`.

---

### - [ ] T5 — Add the Named-pain and Growth pillars with new tier rules (fixes F4)
**Prompt:** Rewrite the growth/review section of `qualify_lead` into two pillars
reading from `SCORING`:
- **Named pain (max 25):** `biz.get("review_negative")` → +25 (this is the lead
  pitch); else any review results present without complaints → 0. Collapse the
  duplicated "checked vs not-checked" trade-prior blocks (~577–600) into ONE
  helper so the trade prior is applied once.
- **Growth & budget (max 25):** automatable-role hiring (see T6 output flag
  `biz.get("hiring_role_match")`) → +25; generic hiring → +12; else trade prior
  (admin/ops +8, appointment trades +5); plus multi-signal budget proxy: +8 if
  `len(biz.get("phones", [])) > 1` OR `len(biz.get("own_domains", [])) > 1`.
Then implement the new tier logic from PRD §2: **Hot** requires contactable AND
(named pain OR `hiring_role_match` OR admin/ops) AND total ≥ `SCORING["tiers"]
["hot"]` (65); **Warm** contactable AND ≥ 40; else **Cold**.
**Acceptance:** Gap-stacking alone cannot produce Hot; an admin/ops business with
a responsiveness complaint and a phone reaches Hot.

---

### - [ ] T6 — Make hiring detection role-aware (fixes F3)
**Prompt:** In `search_hiring_signals` (~line 432), add a module-level list
`AUTOMATABLE_ROLES = ["receptionist", "front desk", "scheduler", "scheduling",
"intake", "dispatcher", "dispatch", "administrative assistant", "admin assistant",
"data entry", "office assistant", "customer service rep", "appointment
coordinator", "office manager"]`. When scanning results, only set
`hiring_found = True` if the combined title+snippet contains a real hiring verb
(`"now hiring"`, `"we're hiring"`, `"join our team"`, `"apply now"`, `"careers"`)
**and** the business name appears in the title/url (to avoid aggregator pages
about unrelated firms). If any `AUTOMATABLE_ROLES` term also appears, set
`cache["businesses"][cache_key]["hiring_role_match"] = True`. Store
`hiring_role_match` (default False) alongside the existing `hiring_signals`.
Tighten the generic keyword list so a bare "jobs"/"hiring" echo from the query is
not sufficient on its own.
**Acceptance:** A business whose only hiring hit is the query word echoed back is
no longer flagged; a posting for "scheduler" or "receptionist" sets
`hiring_role_match=True`.

---

### - [ ] T7 — Fix the always-zero "New" count (fixes F5)
**Prompt:** In `main()` (~line 1126), capture the **previous** last-run value
before overwriting it: `prev_run = cache.get("last_run")` near the top of
`main()`, before the crawl. Pass it into `generate_html_report` (add a
`prev_run` parameter, default `None`) and use it as `new_cutoff` instead of the
freshly-written `cache["last_run"]`. A business is "new" if
`first_seen > prev_run` (or, if `prev_run` is None, first_seen within the last 24h).
**Acceptance:** On a crawl run that adds businesses, the report's "New" stat and
NEW badges are > 0 for the just-added businesses.

---

### - [ ] T8 — Qualification-aware cache TTL (fixes F6, F7)
**Prompt:** In `load_cache` (~line 669), replace the flat 7-day expiry with
tier-aware TTL: keep Hot/Warm leads for 30 days, Cold for 7 days, based on
`v.get("lead_score", {}).get("tier")` and `v.get("last_seen")`. Also prune
`signals` and `fb_groups` older than 14 days (by their `date`/absence-safe).
Default missing fields safely so old caches don't crash.
**Acceptance:** A Warm lead last seen 10 days ago survives reload; a Cold lead
last seen 10 days ago is dropped; `signals`/`fb_groups` older than 14 days are
gone.

---

### - [ ] T9 — Add the Unverified bucket + pitch lines to the report (narrative; F1, F11)
**Prompt:** In `generate_html_report` (~line 886) and `render_lead_card`
(~line 939): (a) route businesses with `status in ("down","blocked")` and no
phone/email/reviews/hiring into a new collapsed **"🛰️ Unverified — couldn't
confirm, low confidence"** section instead of Hot/Warm; (b) add a **"Pitch this:"**
line to each card derived from the top signal — map: responsiveness complaint →
"24/7 digital receptionist that answers + books every call"; admin/ops →
"digital worker for intake, scheduling & follow-up"; automatable-role hiring →
"replace the role you're hiring for with a digital worker"; no booking system →
"automated booking + reminders." Build a small `pitch_for(biz)` helper. Stop
rendering raw `+N` scoring internals as the primary reason tags (keep them only
inside the collapsible detail, if anywhere).
**Acceptance:** Down/blocked-only businesses no longer appear in Hot; every Hot/
Warm card shows a plain-English "Pitch this:" line.

---

### - [ ] T10 — Stop discarding job-board role signals (F10)
**Prompt:** This is the one philosophy-expanding task — keep it conservative.
Do **not** remove job boards from `AGGREGATOR_DOMAINS` (they're still bad
*business* results). Instead, in `search_hiring_signals`, allow results from
`indeed.com`, `ziprecruiter.com`, `linkedin.com/jobs` to count toward
`hiring_role_match` (only) when an `AUTOMATABLE_ROLES` term and the business name
both appear. Add a one-line comment explaining why these domains are allowed here
but blocked elsewhere.
**Acceptance:** An Indeed posting for a "receptionist" at the named business sets
`hiring_role_match=True`; the same domains still never become a `business` entry
in the crawl loop.

---

### - [ ] T11 — Cleanup: dead code + comment drift (F8, F12)
**Prompt:** (a) Remove the legacy `sq.get("automation_gaps", sq.get("issues", []))`
fallback (~536) — use `sq.get("automation_gaps", [])`. (b) Fix the `extract_phones`
comment (~277) to match behavior (it skips 000/999, not toll-free), OR if you and
the owner prefer, also skip leading `900`. (c) In the crawl dedup (~1197), when a
business has no domain, append a short hash of the cleaned name to the key to
reduce collisions: `norm = existing_norm or (re.sub(r'[^a-z0-9]','',name.lower())
[:20] + "-" + str(abs(hash(name)) % 1000))`. Keep domain-based dedup unchanged.
**Acceptance:** File parses; two distinct no-domain businesses with similar names
get distinct cache keys.

---

### - [ ] T12 — Update README + sync the scoring table doc
**Prompt:** Update `README.md` to describe the new 5-pillar buying-readiness model
and the Unverified bucket, and remove the stale "Website Quality Score (0–5)"
framing as the headline metric (keep it as a sub-input). Reference `PRD.md` §2 as
the source of truth for weights.
**Acceptance:** README no longer claims website score is the primary ranking;
pillars and tiers match `SCORING` in code.

---

### Reliability tasks (T13–T18) — the anti-hallucination layer

> **Sequencing:** T13 is the single highest-leverage change in this whole PRD —
> do it immediately after T1 (the weight-table refactor), before/around T2.
> T14–T18 reduce the false negatives that make the confidence gate over-quarantine.

### - [ ] T13 — Confidence-gate all positive scoring (fixes F1, F13, F14, F15)
**Prompt:** In `qualify_lead` (~line 517), award positive points **only** when the
evidence was actually observed. At the top, compute
`verified = sq.get("status") == "up" and sq.get("confidence") == "high"`. Gate the
site-derived pillars (digital footing, any gap-based points) behind `verified`.
Signals from structured/external sources — `biz.get("hiring_role_match")`,
`biz.get("review_negative")`, JSON-LD-sourced phone/email — are exempt (they don't
depend on a clean homepage fetch). If not `verified`, the site contributes **0**,
and the lead may only reach Hot/Warm on exempt signals + contactability. Append a
reason "site unverified — scored on external signals only" when applicable.
**Acceptance:** A business with `status in ("down","blocked")` and no external
signal scores 0 and lands in Unverified; the `confidence` field now affects output.

### - [ ] T14 — Distinguish "unreachable" from "down"; stop confident mislabeling (fixes F14, F15)
**Prompt:** In `check_website` (~line 345), change the failure paths so a timeout,
TLS error, or generic exception returns `_base_result("unknown", "low",
["unreachable — couldn't connect"])` — NOT `("down","high")`. Reserve
`status="down"` for cases where we *connected* and got a clearly dead/parked page.
Change the `400/500` branch (~426) to `("unknown","low", [f"HTTP {e.code} — can't
verify"])`. Add `"unknown"` as a recognized status everywhere it's checked
(`qualify_lead`, the report buckets). Keep `_base_result` defaulting safely.
**Acceptance:** A host that times out reports `status="unknown"`, confidence
`"low"`; no error path returns `"high"` confidence.

### - [ ] T15 — Read enough to be honest: deeper fetch + contact/about + www fallback (fixes F16)
**Prompt:** In `check_website`, raise the page read budget from 12 KB to 150 KB
(`[:150000]`) and the phone scan from 5 KB to the full fetched text; raise
`timeout` to 20 s and add one retry with a 3 s backoff. After fetching the
homepage, if no phone/contact found, fetch up to two internal links whose href
contains `contact` or `about` (same domain only, absolutize relative URLs) and
merge their findings. Try both `domain` and `www.{domain}` (and strip a leading
`www.` if present) before concluding. Keep total requests per business bounded
(≤4) to respect runtime.
**Acceptance:** A site with its phone only in the footer or on `/contact` is now
found; "no contact page" no longer fires when a `/contact` link exists.

### - [ ] T16 — Parse JSON-LD structured data (fixes F18)
**Prompt:** Add a helper `parse_jsonld(html)` that finds every
`<script type="application/ld+json">…</script>` block, `json.loads` each (wrap in
try/except; some are arrays or `@graph`), and extracts from any `LocalBusiness`/
`Organization` node: `telephone`, `email`, `address`, `openingHours`, and
`sameAs` (social URLs). In `check_website`, call it and merge results into
`phones`/`emails` with high confidence (structured data is authoritative). Surface
`sameAs` socials in the result dict for display. Pure stdlib (`json`, `re`).
**Acceptance:** A page whose phone appears only in JSON-LD returns that phone;
malformed JSON-LD is skipped without crashing.

### - [ ] T17 — Detect JS shells + exhaust UAs before "blocked" (fixes F17)
**Prompt:** In `check_website`: (a) after reading HTML, if the visible text is tiny
(<200 words) **and** the page contains a shell marker (`id="root"`, `__NEXT_DATA__`,
`data-reactroot`, `ng-version`, or a Wix/Squarespace app bootstrap), return
`("unknown","low", ["JS-rendered — couldn't read content"])` instead of "thin
content"/"down". (b) On a `403/401/429`, do **not** return immediately — `continue`
to the next UA and scheme, and only return `("blocked","low", …)` after all
UA×scheme attempts are exhausted.
**Acceptance:** A React/Next shell reports `status="unknown"`, not "thin content";
a site that 403s the first UA but serves a later one is read successfully.

### - [ ] T18 — Corroboration gate for soft signals (supports F3, reliability)
**Prompt:** Add a small helper `corroborated(observations)` that returns True only
when a soft signal (manual-process tells, generic negative reviews) is supported by
≥2 independent sources (e.g., complaint seen in 2+ review results) OR comes from a
structured source. Apply it in `qualify_lead` so Tier-3 manual-process points and
generic negative-review points require corroboration; Tier-1 job postings and
JSON-LD facts are exempt (already authoritative). Add a one-line comment citing
PRD §2b rule 7.
**Acceptance:** A one-off complaint mention no longer earns points; a complaint
echoed across 2+ review results does.

---

## 5. Out of scope (explicitly)
- Calling any LLM from the runtime script (cron runs `no_agent: true`).
- Outbound contact/automation of outreach — this tool ranks; humans pitch.
- New third-party dependencies.
- Geographic expansion beyond the current Murrieta/Temecula/Wildomar set.

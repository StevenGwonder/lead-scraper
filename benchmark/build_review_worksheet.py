#!/usr/bin/env python3
"""SGW-861 — Build the human review worksheet (HTML) from the benchmark fixture.

Top-10 ranked prospects + representative sample of possible/bad/unknown records,
each with the pipeline's captured evidence so a human can verify against live
evidence and assign good_fit | possible_fit | bad_fit | unknown.
"""
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

fix = json.load(open(os.path.join(HERE, "prospects.json")))["prospects"]
labels = json.load(open(os.path.join(HERE, "labels.json")))["labels"]

spec = importlib.util.spec_from_file_location("pipe", os.path.join(REPO, "local-biz-92562.py"))
pipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipe)


def reconstruct(entry):
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


ranked = []
for key, entry in fix.items():
    biz = reconstruct(entry)
    url = entry.get("url", "") or (entry.get("own_domains") or [""])[0]
    if pipe._is_directory_record(url, entry.get("name", "")) or pipe._mentions_out_of_area(entry.get("name", "") + " " + url):
        score = {"score": 0, "tier": "Cold", "reasons": ["directory/out-of-area record — not a local business"]}
    else:
        try:
            score = pipe.qualify_lead(biz, biz["site_quality"])
        except Exception as e:  # noqa: BLE001
            score = {"score": 0, "tier": "Cold", "reasons": [f"EVAL ERROR: {e}"]}
    ranked.append((score.get("score", 0), key, entry, labels[key], score))
ranked.sort(key=lambda x: x[0], reverse=True)

# Review set: top 10 + representative sample across label classes.
# Keys MUST be real fixture keys (verified 2026-08-09): kdainclicensedcpasen,
# murrietacaattorneydi, fullserviceaccountin.
top10 = ranked[:10]
sample_keys = {
    "kdainclicensedcpasen": "KDA Inc (good_fit, high score 41)",
    "plumbingservices": "Plumbing Services (possible, Cold)",
    "manageditservicestem": "Managed IT Services (unknown)",
    "handyman": "Handyman (bad_fit)",
    "lawlink": "LawLink (bad_fit)",
    "murrietacaattorneydi": "Murrieta Attorney Directory (bad_fit)",
    "rioslandscapeandtree": "Rios Landscape (unknown)",
    "fullserviceaccountin": "Full Service Accounting Firm (good_fit, score 0)",
}
sample = [r for r in ranked if r[1] in sample_keys]
review = top10 + sample
# dedupe by key, keep order
seen = set()
review = [r for r in review if not (r[1] in seen or seen.add(r[1]))]

def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def sig_list(sigs, kind):
    if not sigs:
        return '<span class="none">none captured</span>'
    out = []
    for s in sigs[:3]:
        title = esc(s.get("title", ""))[:90]
        src = esc(s.get("source_kind", ""))
        rec = esc(s.get("recency", "unknown"))
        out.append(f'<li><span class="sig-title">{title}</span> <span class="pill">{src}</span> <span class="pill dim">recency:{rec}</span></li>')
    if len(sigs) > 3:
        out.append(f'<li class="dim">+{len(sigs)-3} more</li>')
    return "<ul>" + "".join(out) + "</ul>"

def sq_block(sq):
    if not sq:
        return '<span class="none">no site check</span>'
    status = esc(sq.get("status", "?"))
    conf = esc(sq.get("confidence", "?"))
    gaps = sq.get("automation_gaps", [])
    gap_txt = ", ".join(esc(g) for g in gaps[:4]) if gaps else "none"
    return f'status <b>{status}</b> · confidence <b>{conf}</b> · gaps: {gap_txt}'

rows = []
for i, (sc, k, entry, lbl, scobj) in enumerate(review, 1):
    phones = ", ".join(esc(p) for p in (entry.get("phones_anonymized") or [])) or '<span class="none">none</span>'
    emails = ", ".join(esc(e) for e in (entry.get("emails_anonymized") or [])) or '<span class="none">none</span>'
    reasons = "; ".join(esc(r) for r in (scobj.get("reasons") or [])[:3])
    agent_lbl = lbl.get("label", "?")
    lbl_color = {"good_fit": "ok", "possible_fit": "warn", "bad_fit": "bad", "unknown": "dim"}.get(agent_lbl, "dim")
    rows.append(f"""
<tr>
  <td class="rank">{i}</td>
  <td>
    <div class="biz-name">{esc(entry.get('name',''))}</div>
    <div class="dim">{esc(entry.get('trade',''))} · <a href="https://{esc(entry.get('url',''))}" target="_blank">{esc(entry.get('url',''))}</a></div>
    <div class="dim">phones: {phones} · emails: {emails}</div>
  </td>
  <td>
    <div><span class="pill info">score {sc}</span> <span class="pill info">tier {esc(scobj.get('tier',''))}</span> <span class="pill {lbl_color}">agent: {agent_lbl}</span></div>
    <div class="dim small">{reasons}</div>
  </td>
  <td class="ev">
    <div class="ev-label">Site</div><div class="small">{sq_block(entry.get('site_quality'))}</div>
    <div class="ev-label">Hiring</div><div class="small">{sig_list(entry.get('hiring_signals'), 'hiring')}</div>
    <div class="ev-label">Reviews</div><div class="small">{sig_list(entry.get('review_signals'), 'review')}</div>
  </td>
  <td class="verdict">
    <div class="verdict-box">
      <div class="dim small">Your label:</div>
      <div class="verdict-options">
        <span class="vopt" data-k="{k}">good_fit</span>
        <span class="vopt" data-k="{k}">possible_fit</span>
        <span class="vopt" data-k="{k}">bad_fit</span>
        <span class="vopt" data-k="{k}">unknown</span>
      </div>
      <div class="dim small">Reason (evidence-based):</div>
      <div class="reason-box" contenteditable="true" data-k="{k}" placeholder="e.g. real CPA firm, phone verified, hiring for bookkeeper — good_fit"></div>
    </div>
  </td>
</tr>""")

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SGW-861 — Human Review Worksheet — NWP Lead Engine</title>
<style>
:root{{--bg:#0a0a0a;--card:#141414;--border:#292929;--fg:#d4d4d4;--dim:#8a8a8a;--accent:#D97548;--ok:#3ddc84;--warn:#ffd479;--bad:#ff6b6b;--info:#60CFF4}}
*{{box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:24px;line-height:1.5}}
.wrap{{max-width:1100px;margin:0 auto}}
h1{{font-size:22px;color:#fff;margin:0 0 4px}}
.sub{{color:var(--dim);font-size:13px;margin-bottom:18px}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 18px;margin-bottom:14px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid var(--border);vertical-align:top}}
th{{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.05em}}
.rank{{color:var(--dim);font-weight:700;width:30px}}
.biz-name{{font-weight:700;color:#fff;font-size:14px}}
.dim{{color:var(--dim)}}
.small{{font-size:12px}}
.none{{color:#5a5a5a;font-style:italic}}
.pill{{font-size:11px;padding:2px 8px;border-radius:999px;background:#1f1f1f;border:1px solid var(--border);color:var(--dim);display:inline-block;margin:1px 2px 1px 0}}
.pill.ok{{background:#10241a;border-color:#1f4d33;color:var(--ok)}}
.pill.warn{{background:#2a2112;border-color:#5a4a2a;color:var(--warn)}}
.pill.bad{{background:#2a1212;border-color:#5a2a2a;color:var(--bad)}}
.pill.info{{background:#0f2230;border-color:#1f4a63;color:var(--info)}}
.ev{{width:34%}}
.ev-label{{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);margin-top:6px}}
.ev-label:first-child{{margin-top:0}}
.sig-title{{color:var(--fg)}}
ul{{margin:2px 0 4px;padding-left:16px}}
li{{margin:1px 0}}
.verdict{{width:22%}}
.verdict-box{{background:#1b1b1b;border:1px solid var(--border);border-radius:8px;padding:8px}}
.verdict-options{{display:flex;flex-wrap:wrap;gap:4px;margin:4px 0}}
.vopt{{font-size:11px;padding:3px 8px;border-radius:999px;background:#222;border:1px solid var(--border);color:var(--dim);cursor:pointer;user-select:none}}
.vopt:hover{{border-color:var(--accent);color:#fff}}
.vopt.sel{{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:700}}
.reason-box{{min-height:44px;background:#141414;border:1px solid var(--border);border-radius:6px;padding:6px;font-size:12px;color:var(--fg);outline:none}}
.reason-box:focus{{border-color:var(--accent)}}
.footer{{color:#5a5a5a;font-size:12px;margin-top:24px;text-align:center}}
a{{color:var(--info)}}
</style>
</head>
<body><div class="wrap">
<h1>SGW-861 — Human Review Worksheet</h1>
<div class="sub">NWP Precision Lead Engine · benchmark fixture (53 anonymized records) · generated 2026-08-09 · agent labels shown for reference — <b>your labels are the ground truth</b></div>

<div class="card">
<b>How to review:</b> For each record, open the URL and check the business is real, in-geography (Murrieta/Temecula CA area), contactable, and in an admin/ops-heavy lane (law, accounting, insurance, property management, staffing, etc.). Then pick a label:
<ul>
<li><b>good_fit</b> — real, contactable, in-geography, admin/ops-heavy; you would contact.</li>
<li><b>possible_fit</b> — real business but weak capacity/signals/geography; worth a look, not priority.</li>
<li><b>bad_fit</b> — crawler artifact: directory listing, SEO keyword page, aggregator, out-of-geography, identity-confused. Would NOT contact.</li>
<li><b>unknown</b> — unverifiable: site down/blocked, no contact captured.</li>
</ul>
<div class="small dim">Flag anything notable: false contact data, directory/aggregator records, insufficient evidence, or commercially promising despite a low score. Click a label chip to select it; type a reason in the box. Then reply to Sierra with your verdicts (or just the ones that differ from the agent labels).</div>
</div>

<table>
<tr><th>#</th><th>Business</th><th>Pipeline</th><th>Captured evidence</th><th>Your verdict</th></tr>
{''.join(rows)}
</table>

<div class="footer">SGW-861 · human-verified benchmark · NWP Precision Lead Engine · Sierra</div>
</div>
<script>
document.querySelectorAll('.vopt').forEach(el => {{
  el.addEventListener('click', () => {{
    const k = el.dataset.k;
    document.querySelectorAll(`.vopt[data-k="${{k}}"]`).forEach(o => o.classList.remove('sel'));
    el.classList.add('sel');
  }});
}});
</script>
</body></html>
"""

out = os.path.join(HERE, "sgw-861-review-worksheet.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print(f"Wrote {out} — {len(review)} records in review set")
print("Review set:")
for i, (sc, k, entry, lbl, scobj) in enumerate(review, 1):
    print(f"  {i:2}. {sc:3} {entry.get('tier',''):10} {lbl.get('label'):12} {entry.get('name','')[:45]}")

#!/usr/bin/env python3
"""Render the pre-call visual check JSON into a call-ready HTML sheet.

The point: put what a VISITOR sees next to what the audit tool CLAIMS, and
mark the disagreements. You read this before dialing so you never open a call
with a gap the business already solved.

Usage:
    python3 render-pre-call-sheet.py                    # newest JSON
    python3 render-pre-call-sheet.py --json path.json
"""
import argparse
import glob
import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

REPORT_DIR = Path(os.path.expanduser("~/.hermes/scripts/reports"))


def disagreements(vision_text, engine_gaps):
    """Pull the model's DISAGREEMENTS line, and independently check whether the
    vision text contradicts a specific engine gap (so we don't depend on the
    model remembering to fill in that line)."""
    out = set()
    m = re.search(r"DISAGREEMENTS:\s*(.+)", vision_text, re.I | re.S)
    if m:
        frag = m.group(1).strip().split("\n")[0]
        if frag and "none" not in frag.lower():
            out.add(frag.strip())
    low = vision_text.lower()
    # Direct contradiction checks per engine claim.
    if any("booking" in g for g in engine_gaps):
        if re.search(r"\bschedule[ds]?\b.{0,40}\bbutton\b|\bbutton\b.{0,40}\bschedul", low) \
           or "booking button visible" in low or "there is a scheduling button" in low:
            out.add("Engine says NO booking system — a scheduling control IS visible on the page.")
    if "no click-to-call" in engine_gaps and re.search(r"phone number.{0,60}(top|header|corner|visible)", low):
        out.add("Engine says no click-to-call — a phone number IS prominent in the header.")
    if "no CRM" in engine_gaps and "cannot-tell" not in low:
        out.add("Engine says no CRM — a CRM is not visually confirmable either way; do not lead with this.")
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    jf = Path(args.json) if args.json else \
        Path(sorted(glob.glob(str(REPORT_DIR / "pre-call-visual-*.json")))[-1])
    data = json.loads(jf.read_text(encoding="utf-8"))
    leads = data["leads"]
    gen = data.get("generated_at", "")

    cards = []
    for i, L in enumerate(leads, 1):
        v = L.get("vision", "") or ""
        gaps = L.get("engine_gaps", [])
        dis = disagreements(v, gaps)
        dis_html = "".join(
            f'<li>{html.escape(d)}</li>' for d in dis) or "<li class='ok'>No contradictions detected.</li>"
        # Trim the model's reasoning to the answer block for readability.
        body = v
        if len(body) > 2600:
            body = body[:2600] + " …"
        err = f'<div class="err">Vision error: {html.escape(L.get("error",""))}</div>' if L.get("error") else ""
        tel = L.get("contact") or ""
        tel_href = "tel:+1" + re.sub(r"\D", "", tel) if re.sub(r"\D", "", tel) else ""
        cards.append(f"""
<div class="card">
  <div class="head">
    <span class="n">{i}. {html.escape(L.get('name',''))}</span>
    <span class="sc">{L.get('score')}</span>
  </div>
  <div class="meta">
    <a href="{html.escape(L.get('url',''))}" target="_blank">{html.escape(L.get('url',''))}</a>
    {f' · <a href="{tel_href}">{html.escape(tel)}</a>' if tel_href else ''}
    · visual source: {html.escape(L.get('vision_source',''))}
  </div>
  {err}
  <div class="cols">
    <div>
      <div class="lbl">Audit tool claims MISSING</div>
      <ul class="gaps">{''.join(f'<li>{html.escape(g)}</li>' for g in gaps) or '<li class="ok">none</li>'}</ul>
    </div>
    <div>
      <div class="lbl">What a visitor actually SEES — contradictions</div>
      <ul class="dis">{dis_html}</ul>
    </div>
  </div>
  <div class="lbl">Vision read of the live page</div>
  <div class="vs">{html.escape(body)}</div>
</div>""")

    H = f"""<!doctype html><html><head><meta charset="utf-8">
<title>NWP Pre-Call Visual Check</title><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Inter',system-ui,-apple-system,sans-serif;background:#0a0a0a;color:#d4d4d4;line-height:1.5;padding:0 0 60px}}
.hero{{background:linear-gradient(180deg,#111,var(--bg));padding:38px 20px 26px;text-align:center;border-bottom:1px solid #292929}}
h1{{font-size:1.5em;color:#fff}}h1 span{{color:#D97548}}
.sub{{color:#60CFF4;font-size:.8em;margin-top:6px}}
.meta2{{color:#777;font-size:.72em;margin-top:12px}}
.wrap{{max-width:1000px;margin:0 auto;padding:0 18px}}
.note{{background:#141414;border-left:3px solid #D97548;border-radius:6px;padding:12px 14px;margin:22px 0;font-size:.86em}}
.card{{background:#141414;border:1px solid #292929;border-radius:10px;padding:16px;margin:16px 0}}
.head{{display:flex;justify-content:space-between;align-items:baseline;gap:8px}}
.n{{color:#fff;font-weight:600;font-size:1.05em}}
.sc{{color:#D97548;font-weight:700}}
.meta{{color:#888;font-size:.78em;margin-top:2px}}
.meta a{{color:#60CFF4;text-decoration:none}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:12px 0}}
@media(max-width:700px){{.cols{{grid-template-columns:1fr}}}}
.lbl{{color:#777;font-size:.68em;text-transform:uppercase;letter-spacing:.08em;margin:10px 0 4px}}
ul{{list-style:none;font-size:.84em}}li{{padding:2px 0}}
.gaps li::before{{content:"— ";color:#D97548}}
.dis li{{color:#ffd479}}.dis li::before{{content:"! ";color:#e5534b;font-weight:700}}
.ok{{color:#3fb950}}
.vs{{background:#0f0f0f;border-left:3px solid #60CFF4;padding:10px 12px;border-radius:6px;font-size:.8em;white-space:pre-wrap;color:#bbb;max-height:340px;overflow:auto}}
.err{{background:rgba(229,83,75,.12);color:#e5534b;border-radius:4px;padding:6px 8px;font-size:.8em;margin:8px 0}}
</style></head><body>
<div class="hero"><h1>NWP <span>Pre-Call Visual Check</span></h1>
<div class="sub">A second opinion on the live site, before you dial</div>
<div class="meta2">Generated {html.escape(gen[:19])} UTC · model {html.escape(data.get('model',''))} · {len(leads)} lead(s)</div></div>
<div class="wrap">
<div class="note"><b>How to use this.</b> Left column is what the audit tool <i>claims</i> is missing.
Right column is what a visitor can <i>actually see</i> on the live page. Where they disagree — marked with !
— <b>do not open the call with that claim.</b> The engine reads HTML markers; a page can ship the tracking
script and still have no working booking flow, and vice versa. This sheet is the difference.</div>
{''.join(cards)}
</div></body></html>"""
    out = REPORT_DIR / f"pre-call-visual-{datetime.now(timezone.utc).strftime('%Y%m%d')}.html"
    out.write_text(H, encoding="utf-8")
    print(f"Pre-call sheet written: {out}")
    # also print a terse text summary
    print()
    for i, L in enumerate(leads, 1):
        dis = disagreements(L.get("vision", ""), L.get("engine_gaps", []))
        print(f"{i}. {L.get('name')} ({L.get('score')})")
        for d in dis:
            print(f"     ! {d}")
    return out


if __name__ == "__main__":
    main()

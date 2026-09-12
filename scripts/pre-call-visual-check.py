#!/usr/bin/env python3
"""NWP Pre-Call Visual Check — a second opinion on the top leads before you dial.

Why this exists: the engine scores automation gaps from HTML markers. That is
necessary but not sufficient — a page can ship the HubSpot script and still
have no working booking flow a customer can actually use. This tool renders the
business's own site and asks a vision model what a VISITOR would experience,
then puts that next to what the engine claims. Where the two disagree, the
engine is probably wrong and you should not open the call with that claim.

Cost discipline: this is a PRE-CALL check on a handful of leads, not bulk
scoring. Vision is called once per lead (one screenshot each).

Usage:
    python3 pre-call-visual-check.py --top 10
    python3 pre-call-visual-check.py --top 10 --out ~/.hermes/reports
    python3 pre-call-visual-check.py --keys k1,k2      # specific records

Model: OpenRouter free vision (see auxiliary.vision in ~/.hermes/config.yaml).
Stdlib only — no playwright, no requests. Screenshots come from a render
service that returns an image for a URL; if that is unavailable the tool
degrades to a text-only visual proxy and SAYS SO rather than pretending.
"""
import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CACHE_FILE = Path(os.path.expanduser("~/.hermes/scripts/local-biz-cache.json"))
REPORT_DIR = Path(os.path.expanduser("~/.hermes/scripts/reports"))
VISION_MODEL = "inclusionai/ling-3.0-flash-vl:free"
VISION_URL = "https://openrouter.ai/api/v1/chat/completions"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# What we ask the model to look for. Each maps to a claim the engine makes, so
# a disagreement is directly actionable on the call.
QUESTIONS = [
    ("booking", "Is there a way for a visitor to BOOK or SCHEDULE an "
                "appointment online, without phoning? Look for a booking "
                "button, calendar widget, or 'schedule' control."),
    ("contact", "Is there a clear way to CONTACT them — a visible phone number, "
                "a contact form, or an email address?"),
    ("chat",    "Is there a live chat or messaging widget visible on the page?"),
    ("hours",   "Are business hours or 'call us during' style instructions "
                "shown, implying you must phone during set hours?"),
    ("form",    "Does intake look like a plain form/pdf/email-back process "
                "rather than a scheduling system?"),
]


def _env(key):
    v = os.environ.get(key, "")
    if v:
        return v
    p = Path.home() / ".hermes" / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            s = line.strip()
            if s.startswith(key + "="):
                raw = s.split("=", 1)[1].strip()
                if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
                    raw = raw[1:-1]
                return raw
    return ""


def log(msg):
    print(f"[pre-call] {msg}", file=sys.stderr)


def screenshot(url, timeout=90):
    """Fetch a rendered screenshot of `url` as raw image bytes, or None.

    Uses wordpress.com's public mShots service (no key, no signup): it renders
    a real browser view and returns an image. NOTE: mShots returns JPEG even
    though it is often described as PNG — accepting only the PNG magic
    rejected every valid screenshot (verified 2026-09-12). Falls back to None
    so the caller degrades honestly instead of guessing."""
    q = urllib.parse.quote(url, safe="")
    for host in ("https://s0.wp.com/mshots/v1", "https://s.wordpress.com/mshots/v1"):
        endpoint = f"{host}/{q}?w=1280&h=1600"
        try:
            req = urllib.request.Request(endpoint, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if len(data) > 5000 and (data[:8] == b"\x89PNG\r\n\x1a\n"
                                     or data[:3] == b"\xff\xd8\xff"):
                return data
        except Exception as e:
            log(f"screenshot attempt failed for {url}: {type(e).__name__}")
            continue
    return None


def fetch_text(url, budget=400000):
    """Text proxy when no screenshot is available. Clearly labelled as such."""
    for scheme in ("", ):
        target = url if "://" in url else "https://" + url
        try:
            req = urllib.request.Request(target, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read(budget).decode("utf-8", errors="ignore")
        except Exception:
            continue
    return ""


def ask_vision(b64_png, business, engine_gaps, text_proxy=None):
    """One vision call per lead. Returns dict of findings or an error note."""
    key = _env("OPENROUTER_API_KEY")
    if not key:
        return {"error": "no OPENROUTER_API_KEY"}

    qs = "\n".join(f"- {k}: {q}" for k, q in QUESTIONS)
    prompt = (
        f"You are helping a consultant prepare a phone call to a local "
        f"business, \"{business}\". Look at the screenshot of their website and "
        f"answer ONLY from what is visible to a normal visitor.\n\n"
        f"A website audit tool has claimed these things are MISSING: "
        f"{', '.join(engine_gaps) if engine_gaps else 'nothing'}.\n\n"
        f"For each question answer YES, NO, or CANNOT-TELL, then one short "
        f"sentence of evidence. Do not guess. If the image is unclear or you "
        f"cannot see the relevant part of the page, answer CANNOT-TELL.\n\n"
        f"{qs}\n\n"
        f"Finish with a line starting 'DISAGREEMENTS:' listing which of the "
        f"audit tool's MISSING claims you believe are WRONG based on what you "
        f"can see, or 'none'."
    )

    if b64_png:
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64," + b64_png}},
        ]
    else:
        snippet = re.sub(r"<[^>]+>", " ", text_proxy or "")[:6000]
        content = [{"type": "text", "text": prompt +
                    "\n\nNO SCREENSHOT AVAILABLE. Here is the raw page text as a "
                    "weak proxy — treat visual questions as CANNOT-TELL:\n" + snippet}]

    body = json.dumps({
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 700,
    }).encode()
    req = urllib.request.Request(
        VISION_URL, data=body,
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            j = json.loads(r.read())
        # Defensive: a provider may return a null content with the answer in
        # the reasoning channel. Never crash on that — surface it.
        choices = j.get("choices") or []
        if not choices:
            return {"error": f"no choices in response: {json.dumps(j)[:180]}"}
        msg = choices[0].get("message") or {}
        text = (msg.get("content") or "").strip()
        if not text:
            text = (msg.get("reasoning") or "").strip()
        if not text:
            return {"error": f"empty content: {json.dumps(msg)[:180]}"}
        return {"text": text}
    except Exception as e:
        detail = ""
        try:
            detail = e.read().decode()[:200]
        except Exception:
            detail = str(e)[:200]
        return {"error": f"{type(e).__name__}: {detail}"}


def load_top(n, keys=None):
    cache = json.loads(CACHE_FILE.read_text())
    biz = cache.get("businesses", {})
    if keys:
        chosen = [(k, biz[k]) for k in keys if k in biz]
    else:
        pool = [(k, v) for k, v in biz.items()
                if v.get("eligibility_state") == "eligible"
                and (v.get("own_domains") or v.get("url"))]
        pool.sort(key=lambda kv: -((kv[1].get("lead_score") or {}).get("score") or 0))
        chosen = pool[:n]
    return chosen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--keys", default="")
    ap.add_argument("--out", default=str(REPORT_DIR))
    args = ap.parse_args()

    keys = [k.strip() for k in args.keys.split(",") if k.strip()]
    leads = load_top(args.top, keys or None)
    if not leads:
        log("no leads selected")
        sys.exit(1)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for key, biz in leads:
        name = biz.get("name", key)
        domain = (biz.get("own_domains") or [biz.get("url", "")])[0]
        url = domain if "://" in domain else "https://" + domain
        gaps = (biz.get("site_quality") or {}).get("automation_gaps") or []
        score = (biz.get("lead_score") or {}).get("score")
        log(f"checking {name} ({score}) — {url}")

        png = screenshot(url)
        b64 = base64.b64encode(png).decode() if png else None
        text = None if png else fetch_text(url)
        res = ask_vision(b64, name, gaps, text)
        results.append({
            "key": key, "name": name, "url": url, "score": score,
            "contact": (biz.get("phones") or [""])[0],
            "engine_gaps": gaps,
            "vision_source": "screenshot" if png else ("text-proxy" if text else "none"),
            "vision": res.get("text", ""),
            "error": res.get("error", ""),
        })
        log(f"  -> {results[-1]['vision_source']}"
            + (f" ERROR {results[-1]['error'][:60]}" if results[-1]["error"] else ""))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "model": VISION_MODEL, "leads": results}
    jf = out / f"pre-call-visual-{stamp}.json"
    jf.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Pre-call visual check written: {jf}")
    return jf


if __name__ == "__main__":
    main()

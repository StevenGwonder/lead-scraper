#!/usr/bin/env python3
"""
Website Quality Checker for Local Business Prospecting.
Curls a domain, checks HTML for quality indicators, scores 1-5.

Usage:
  python3 website-quality-check.py site1.com site2.com site3.com
  cat leads.txt | xargs python3 website-quality-check.py

Scoring:
  5 = GREAT (mobile + phone + contact + content + booking/chat)
  3-4 = OK (basics covered, missing automation)
  2 = WEAK (missing mobile or content)
  0-1 = BAD (barely exists — walk-in target)
"""
import subprocess, re, sys


def check_site(domain):
    """Check a single domain and return a quality report."""
    result = {
        "domain": domain,
        "score": 0,
        "label": "BAD",
        "flags": [],
        "platform": [],
        "title": "",
        "words": 0,
        "error": None,
    }

    # Try HTTPS first, fall back to HTTP (many small biz sites only work on HTTP)
    html = ""
    for scheme in ["https", "http"]:
        try:
            proc = subprocess.run(
                ["curl", "-sL", "--max-time", "10", f"{scheme}://{domain}"],
                capture_output=True, text=True, timeout=15
            )
            html = proc.stdout[:10000]
            if html and len(html) >= 100:
                break
        except subprocess.TimeoutExpired:
            continue

    if not html or len(html) < 100:
        result["error"] = "NO RESPONSE (tried https + http)"
        return result

    html_lower = html.lower()

    # Platform detection
    platforms = []
    if "wp-content" in html_lower or "wordpress" in html_lower:
        platforms.append("WordPress")
    if "wix" in html_lower:
        platforms.append("Wix")
    if "weebly" in html_lower:
        platforms.append("Weebly")
    if "squarespace" in html_lower:
        platforms.append("Squarespace")
    if "godaddy" in html_lower:
        platforms.append("GoDaddy")
    if "elementor" in html_lower:
        platforms.append("Elementor")
    if "et_builder" in html_lower or "divi" in html_lower:
        platforms.append("Divi")
    result["platform"] = platforms

    flags = []

    # Scoring checks
    has_viewport = "viewport" in html_lower
    if has_viewport:
        result["score"] += 1
    else:
        flags.append("NO-MOBILE")

    has_tel = "tel:" in html_lower
    if has_tel:
        result["score"] += 1
    else:
        flags.append("NO-CLICK-TO-CALL")

    has_contact = "contact" in html_lower
    if has_contact:
        result["score"] += 1
    else:
        flags.append("NO-CONTACT")

    text_only = re.sub(r'<[^>]+>', ' ', html)
    words = len(text_only.split())
    result["words"] = words
    if words > 200:
        result["score"] += 1
    else:
        flags.append("THIN-CONTENT")

    has_booking = any(x in html_lower for x in ["book", "schedule", "appointment", "calendly", "acuity"])
    has_chat = any(x in html_lower for x in ["chat", "intercom", "drift", "tawk", "zendesk"])
    if has_booking or has_chat:
        result["score"] += 1
        if has_booking:
            flags.append("HAS-BOOKING")
        if has_chat:
            flags.append("HAS-CHAT")

    result["flags"] = flags

    # Title
    title_match = re.search(r'<title>(.*?)</title>', html, re.I | re.S)
    result["title"] = title_match.group(1).strip()[:60] if title_match else "NO TITLE"

    # Label
    score = result["score"]
    if score >= 4:
        result["label"] = "GREAT"
    elif score >= 3:
        result["label"] = "OK"
    elif score >= 2:
        result["label"] = "WEAK"
    else:
        result["label"] = "BAD"

    return result


def print_report(result):
    """Print a single site report."""
    if result["error"]:
        print(f"\n{result['domain']}: ERROR - {result['error']}")
        return

    platform_str = ", ".join(result["platform"]) if result["platform"] else "Unknown/Custom"
    flags_str = ", ".join(result["flags"]) if result["flags"] else "None"

    print(f"\n{result['domain']} [{result['label']}]")
    print(f"  Title: {result['title']}")
    print(f"  Platform: {platform_str}")
    print(f"  Content: ~{result['words']} words")
    print(f"  Flags: {flags_str}")
    print(f"  Score: {result['score']}/5")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 website-quality-check.py domain1.com domain2.com ...")
        print("       cat leads.txt | xargs python3 website-quality-check.py")
        sys.exit(1)

    domains = sys.argv[1:]
    results = []

    for domain in domains:
        r = check_site(domain.strip())
        results.append(r)
        print_report(r)

    # Summary
    bad = sum(1 for r in results if r["label"] in ("BAD", "WEAK") and not r["error"])
    ok = sum(1 for r in results if r["label"] == "OK" and not r["error"])
    great = sum(1 for r in results if r["label"] == "GREAT" and not r["error"])
    errors = sum(1 for r in results if r["error"])

    print(f"\n--- SUMMARY ---")
    print(f"Checked: {len(results)} sites")
    print(f"  BAD/WEAK (walk-in targets): {bad}")
    print(f"  OK (automation upsell): {ok}")
    print(f"  GREAT (low priority): {great}")
    print(f"  Errors: {errors}")


if __name__ == "__main__":
    main()
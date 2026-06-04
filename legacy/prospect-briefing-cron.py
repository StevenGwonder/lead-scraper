#!/usr/bin/env python3
"""
AI Employee Prospect Morning Briefing — Cron wrapper.

Runs the prospect finder across all target industries, formats a briefing,
and delivers it to Telegram. Zero LLM tokens — all Python.

This finds STEVEN's potential $5k/mo clients across:
  marketing agencies, law firms, insurance, manufacturing, wholesale, real estate
"""

import subprocess
import sys
import os

PROSPECT_SCRIPT = os.path.expanduser(
    "~/.hermes/scripts/prospect-finder.py"
)


def main():
    cmd = [
        sys.executable,
        PROSPECT_SCRIPT,
        "--all",
        "--cities", "Riverside, CA", "Orange County, CA", "San Diego, CA",
        "--state", "California",
        "--region", "SoCal",
        "--limit", "10",
        "--delay", "2",
        "--format", "briefing",
    ]
    result = subprocess.run(cmd, timeout=600)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
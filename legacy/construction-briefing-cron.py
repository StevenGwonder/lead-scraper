#!/usr/bin/env python3
"""
Construction Morning Briefing — Cron wrapper.

Runs the full briefing pipeline (scout → qualify → format) using the
construction-demo profile. Defaults to multi-city SoCal coverage.

This script is called by the 'construction-morning-briefing' cron job
with no_agent=True, so stdout is delivered verbatim to Telegram.
Zero LLM tokens consumed — all processing is Python script-based.
"""

import subprocess
import sys
import os

BRIEFING_SCRIPT = os.path.expanduser(
    "~/.hermes/scripts/construction-briefing.py"
)
PROFILE = os.path.expanduser(
    "~/.hermes/profiles/construction-demo/config.yaml"
)


def main():
    cmd = [
        sys.executable,
        BRIEFING_SCRIPT,
        "--profile", PROFILE,
    ]
    result = subprocess.run(cmd, timeout=300)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
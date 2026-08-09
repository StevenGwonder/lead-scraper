#!/usr/bin/env bash
# SGW-938 B6 — repository verification command.
# Runs: syntax check → scoring self-check → offline benchmark.
# Exit non-zero on any failure. No crawl, no network, no cache writes.
set -uo pipefail
cd "$(dirname "$0")/.."

echo "== [1/4] Syntax check =="
python3 -c "import ast; ast.parse(open('local-biz-92562.py').read()); print('  OK: local-biz-92562.py parses')" || exit 1

echo "== [2/4] Scoring + identity self-check =="
python3 local-biz-92562.py --self-check || exit 1

echo "== [3/4] Signal-coverage report (cache-only, no network) =="
python3 local-biz-92562.py --coverage || exit 1

echo "== [4/4] Offline benchmark (live pipeline vs labeled fixture) =="
python3 benchmark/evaluate.py --top 10 || exit 1

echo
echo "ALL CHECKS PASSED"

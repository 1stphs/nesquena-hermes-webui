#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://172.234.237.195:8787}"
OUT="${1:-loadtests/results/manual_health.jsonl}"
INTERVAL="${HERMES_LOADTEST_MONITOR_INTERVAL:-5}"
TIMEOUT="${HERMES_LOADTEST_MONITOR_TIMEOUT:-10}"
CONTAINER="${HERMES_LOADTEST_CONTAINER:-hermes-webui}"

python3 "$(dirname "$0")/monitor.py" \
  --base-url "$BASE_URL" \
  --output "$OUT" \
  --interval "$INTERVAL" \
  --timeout "$TIMEOUT" \
  --container "$CONTAINER"

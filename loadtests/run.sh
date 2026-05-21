#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://172.234.237.195:8787}"
USERS="${USERS:-20}"
SPAWN_RATE="${SPAWN_RATE:-2}"
RUN_TIME="${RUN_TIME:-10m}"
RESULTS_DIR="${RESULTS_DIR:-loadtests/results}"
MONITOR_INTERVAL="${HERMES_LOADTEST_MONITOR_INTERVAL:-5}"
CONTAINER="${HERMES_LOADTEST_CONTAINER:-hermes-webui}"

mkdir -p "$RESULTS_DIR"

if [ -z "${RUN_ID:-}" ]; then
  RUN_ID="$(printf 'run_%03d_%s' "$USERS" "$(date +%Y%m%d_%H%M%S)")"
fi

CSV_PREFIX="$RESULTS_DIR/$RUN_ID"
HEALTH_FILE="${CSV_PREFIX}_health.jsonl"

if [ -z "${HERMES_LOADTEST_API_TOKEN:-}" ] && [ -z "${HERMES_LOADTEST_PASSWORD:-}" ]; then
  echo "warning: no HERMES_LOADTEST_API_TOKEN or HERMES_LOADTEST_PASSWORD is set; this only works if auth is disabled." >&2
fi

python3 "$(dirname "$0")/monitor.py" \
  --base-url "$BASE_URL" \
  --output "$HEALTH_FILE" \
  --interval "$MONITOR_INTERVAL" \
  --container "$CONTAINER" &
MONITOR_PID="$!"

cleanup() {
  if kill -0 "$MONITOR_PID" >/dev/null 2>&1; then
    kill "$MONITOR_PID" >/dev/null 2>&1 || true
    wait "$MONITOR_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

locust \
  -f "$(dirname "$0")/locustfile.py" \
  --host "$BASE_URL" \
  --headless \
  -u "$USERS" \
  -r "$SPAWN_RATE" \
  --run-time "$RUN_TIME" \
  --csv "$CSV_PREFIX" \
  --csv-full-history

cleanup
python3 "$(dirname "$0")/report.py" --results-dir "$RESULTS_DIR"

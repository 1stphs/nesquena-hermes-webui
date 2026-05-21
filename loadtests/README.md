# Hermes WebUI Load Test

This first version uses Locust to exercise the real WebUI chat path:

1. log in with a token or password,
2. create an isolated session,
3. call `/api/chat/start`,
4. keep `/api/chat/stream` open until `stream_end`,
5. summarize the largest passing concurrency as `MaxStableConcurrentRuns`.

## Install

```bash
python3 -m venv .venv-loadtest
source .venv-loadtest/bin/activate
pip install -r loadtests/requirements.txt
```

## Secrets

Do not commit tokens, cookies, passwords, or provider keys. Pass one credential
at runtime:

```bash
export HERMES_LOADTEST_API_TOKEN="paste-token-here"
# or:
export HERMES_LOADTEST_PASSWORD="paste-password-here"
```

## Run One Step

```bash
BASE_URL="http://172.234.237.195:8787" \
USERS=20 \
SPAWN_RATE=2 \
RUN_TIME=10m \
./loadtests/run.sh
```

Useful optional environment variables:

```bash
export HERMES_LOADTEST_PROFILE="default"
export HERMES_LOADTEST_MODEL=""
export HERMES_LOADTEST_MODEL_PROVIDER=""
export HERMES_LOADTEST_WORKSPACE=""
export HERMES_LOADTEST_PROMPT="Reply with OK only."
export HERMES_LOADTEST_STREAM_TIMEOUT_SECONDS=180
export HERMES_LOADTEST_CONTAINER="hermes-webui"
```

## Step Search

Run several steps and let the report pick the largest passing value:

```bash
for users in 1 5 10 20 30 50; do
  USERS="$users" RUN_TIME=10m ./loadtests/run.sh
done
```

Then regenerate the summary:

```bash
python3 loadtests/report.py --results-dir loadtests/results
```

Outputs:

```text
loadtests/results/summary.json
loadtests/results/summary.md
```

Default pass thresholds:

| Metric | Threshold |
|---|---:|
| `/api/chat/start` success rate | >= 0.99 |
| SSE completion rate | >= 0.95 |
| first token P95 | <= 15000 ms |
| stream total P95 | <= 60000 ms |
| health samples | all `status=ok` |
| CPU max | <= 85% when local Docker stats are available |
| memory max | <= 80% when local Docker stats are available |

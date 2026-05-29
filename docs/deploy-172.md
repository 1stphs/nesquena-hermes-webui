# Deploy 172.234.237.195

This runbook is for deploying this Hermes API service to the existing server:

```text
host: 172.234.237.195
ssh user: root
public API URL: http://172.234.237.195:8787
```

Do not write the SSH password, service password, `hermes_session`, long-lived API token, or provider API keys into this repository. Enter secrets interactively or load them from a local password manager.

If you make a private local deployment script, keep the host fixed and leave the password blank in committed files:

```bash
SSH_HOST="172.234.237.195"
SSH_USER="root"
SSH_PASSWORD=""
```

Fill `SSH_PASSWORD` only in a local uncommitted copy, or pass it through your shell/password manager at runtime.

## Service Map

Current production relationship on `172.234.237.195`:

| Port | Container | Image | Purpose |
|---:|---|---|---|
| `8787` | `hermes-webui` | `hermes-webui-token-login:latest` | Hermes API service. The Vue frontend should call this layer through `/api/*`. |
| `8642` | `hermes` | `nousresearch/hermes-agent:latest` | Hermes OpenAI-compatible `/v1` API Server. Publicly reachable, requires API key. |
| `8643` | `hermes-foxu` | `nousresearch/hermes-agent:latest` | Another Hermes OpenAI-compatible `/v1` API Server. Publicly reachable, requires API key. |

The `8787` API chat flow is not a direct proxy to `8642` or `8643`. It uses this service's own profile/session/chat/stream API and the mounted Hermes home plus Hermes agent source:

```text
Browser or Vue -> http://172.234.237.195:8787/api/* -> Hermes API service -> shared Hermes home / agent runtime
```

The API Server ports are separate:

```text
OpenAI-compatible client -> http://172.234.237.195:8642/v1/*
OpenAI-compatible client -> http://172.234.237.195:8643/v1/*
```

## Server Paths

Current server layout:

```text
active compose dir: /var/www/nesquena-hermes-webui
active compose file: /var/www/nesquena-hermes-webui/docker-compose.yml
source dir:         /var/www/nesquena-hermes-webui
Hermes home:        /root/.hermes
API service state:  /root/.hermes/webui-mvp
workspace:          /root/.hermes/workspace
agent src:          /var/www/hermes-agent-src
```

The API service container sees those paths as:

```text
/home/hermeswebui/.hermes
/home/hermeswebui/.hermes/webui-mvp
/home/hermeswebui/.hermes/workspace
/home/hermeswebui/.hermes/hermes-agent
```

Current important mounts:

```text
/root/.hermes:/home/hermeswebui/.hermes
/var/www/hermes-agent-src:/home/hermeswebui/.hermes/hermes-agent
/var/www/hermes-community-skills:/var/www/hermes-community-skills:ro
/var/www/hermes-built-in-skills:/var/www/hermes-built-in-skills:ro
/var/www/hermes-optional-skills:/var/www/hermes-optional-skills:ro
/var/www/hermes-bioclaw-skills:/var/www/hermes-bioclaw-skills:ro
```

Current important service environment:

```text
HERMES_WEBUI_HOST=0.0.0.0
HERMES_WEBUI_PORT=8787
HERMES_WEBUI_STATE_DIR=/home/hermeswebui/.hermes/webui-mvp
HERMES_WEBUI_DEFAULT_WORKSPACE=/home/hermeswebui/.hermes/workspace
HERMES_HOME=/home/hermeswebui/.hermes
HERMES_WEBUI_CORS_ALLOW_ALL=1
```

The older `/var/www/hermes-agent-webui` directory may still exist on the server,
but it is not the current compose owner of the running `hermes-webui` container.
Before rebuilding, always trust the compose labels from `docker inspect`; using a
different compose directory can create a separate compose project and hit a
`container name "/hermes-webui" is already in use` conflict.

## Token Login State

The deployed API service includes the long-lived token login patch:

```http
POST /api/auth/token-login
```

Token config is stored on the server at:

```text
/root/.hermes/webui-mvp/api_tokens.json
```

The current test token id is:

```text
digital-employee-local-test
```

The plaintext test token is stored only on the server in a root-only file:

```text
/root/.hermes/webui-mvp/api_token.digital-employee-local-test.secret
```

Do not print that token in logs or paste it into docs. The JSON config stores only `sha256:<hex>` token hashes.

## Deploy From Local Changes

From the local development machine, first commit and push the API service changes:

```bash
cd '/Users/mac/Documents/ljl-project/nesquena:hermes-webui'
git status --short
git push origin master
```

Then SSH directly into the server:

```bash
ssh root@172.234.237.195
```

After login, run a read-only status check before changing anything:

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}'
docker inspect hermes-webui --format '{{json .Mounts}}'
docker inspect hermes-webui --format 'project={{ index .Config.Labels "com.docker.compose.project" }} service={{ index .Config.Labels "com.docker.compose.service" }} config_files={{ index .Config.Labels "com.docker.compose.project.config_files" }} working_dir={{ index .Config.Labels "com.docker.compose.project.working_dir" }}'
```

Do not copy secrets from `docker inspect` into tickets, commits, docs, or chat logs.
Avoid dumping `.Config.Env` unless you are specifically debugging environment
inheritance; it may contain deployment secrets.

Update the server source:

```bash
cd /var/www/nesquena-hermes-webui
git fetch origin master
git status --short
git log --oneline -1 HEAD
git log --oneline -1 origin/master
git pull --ff-only origin master
```

Build and recreate only the WebUI service, preserving existing volumes and port mappings:

```bash
cd /var/www/nesquena-hermes-webui
docker compose up -d --build hermes-webui
```

Expected compose owner after the rebuild:

```bash
docker inspect hermes-webui --format 'project={{ index .Config.Labels "com.docker.compose.project" }} service={{ index .Config.Labels "com.docker.compose.service" }} config_files={{ index .Config.Labels "com.docker.compose.project.config_files" }} working_dir={{ index .Config.Labels "com.docker.compose.project.working_dir" }}'
```

Expected label values:

```text
project=nesquena-hermes-webui
service=hermes-webui
config_files=/var/www/nesquena-hermes-webui/docker-compose.yml
working_dir=/var/www/nesquena-hermes-webui
```

Keep the existing service name, container name, volumes, state directory, and
`8787:8787` port mapping. Do not rebuild or restart `hermes` or `hermes-foxu`
unless you are intentionally deploying the separate `8642` / `8643` API Server
containers.

If `docker compose up` prints a conflict like:

```text
container name "/hermes-webui" is already in use
```

stop and inspect the existing container labels:

```bash
docker inspect hermes-webui --format 'project={{ index .Config.Labels "com.docker.compose.project" }} service={{ index .Config.Labels "com.docker.compose.service" }} config_files={{ index .Config.Labels "com.docker.compose.project.config_files" }} working_dir={{ index .Config.Labels "com.docker.compose.project.working_dir" }}'
```

Then `cd` to the printed `working_dir` and rerun:

```bash
docker compose up -d --build --force-recreate hermes-webui
```

Do not manually remove the existing `hermes-webui` container as the first fix;
the usual cause is deploying from the wrong compose project.

## Smoke Test

Run these checks on the server after deploy.

Container and health:

```bash
docker ps --format '{{.Names}}|{{.Image}}|{{.Status}}' | grep hermes-webui
curl -i http://127.0.0.1:8787/health
```

Expected:

```text
hermes-webui|hermes-webui-token-login:latest|Up ... (healthy)
HTTP/1.0 200 OK
```

Code import check:

```bash
docker exec -i hermes-webui python3 - <<'PY'
import api.routes as routes
print(routes.__file__)
print(hasattr(routes, "_requested_sessions_profile"))
PY
```

For the profile-scoped sessions deployment, expected output includes:

```text
/apptoo/api/routes.py
True
```

CORS preflight:

```bash
curl -i -X OPTIONS http://127.0.0.1:8787/api/auth/token-login \
  -H 'Origin: http://localhost:5173' \
  -H 'Access-Control-Request-Method: POST'
```

Expected:

```text
HTTP/1.0 204 No Content
Access-Control-Allow-Origin: http://localhost:5173
Access-Control-Allow-Credentials: true
Access-Control-Allow-Methods: GET, POST, PATCH, DELETE, OPTIONS
Access-Control-Allow-Headers: Content-Type, Authorization
Vary: Origin
```

Token login without printing the token:

```bash
TOKEN="$(cat /root/.hermes/webui-mvp/api_token.digital-employee-local-test.secret)"
curl -sS -D /tmp/hermes-token-login.headers \
  -c /tmp/hermes-cookie.jar \
  -o /tmp/hermes-token-login.json \
  -X POST http://127.0.0.1:8787/api/auth/token-login \
  -H 'Content-Type: application/json' \
  -H 'Origin: http://localhost:5173' \
  -d "{\"token\":\"${TOKEN}\"}"
python3 -m json.tool /tmp/hermes-token-login.json
grep -qi 'Set-Cookie: hermes_session=' /tmp/hermes-token-login.headers && echo 'set-cookie: ok'
```

Expected JSON:

```json
{
  "ok": true,
  "token_id": "digital-employee-local-test"
}
```

Protected profile API with the cookie:

```bash
curl -sS -b /tmp/hermes-cookie.jar \
  -o /tmp/hermes-profiles.json \
  -w 'profiles_http=%{http_code}\n' \
  http://127.0.0.1:8787/api/profiles
python3 -m json.tool /tmp/hermes-profiles.json | head -40
```

Expected:

```text
profiles_http=200
```

Public smoke test from local machine:

```bash
curl -i --max-time 8 http://172.234.237.195:8787/health
curl -i --max-time 8 -X OPTIONS http://172.234.237.195:8787/api/auth/token-login \
  -H 'Origin: http://localhost:5173' \
  -H 'Access-Control-Request-Method: POST'
```

Expected:

```text
HTTP/1.0 200 OK
HTTP/1.0 204 No Content
Access-Control-Allow-Origin: http://localhost:5173
Access-Control-Allow-Credentials: true
```

## Frontend Integration Target

For the `digital_employee` Vue app, use a Vite proxy to `8787`:

```js
server: {
  proxy: {
    '/hermes': {
      target: 'http://172.234.237.195:8787',
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/hermes/, ''),
    },
  },
}
```

Initial request sequence:

```http
POST /hermes/api/auth/token-login
GET  /hermes/api/auth/status
GET  /hermes/api/profiles
POST /hermes/api/session/new
POST /hermes/api/chat/start
GET  /hermes/api/chat/stream?stream_id=...
GET  /hermes/api/sessions
GET  /hermes/api/session?session_id=...&messages=1&resolve_model=0
GET  /hermes/api/chat/cancel?stream_id=...
```

All `fetch` requests should keep cookies:

```js
credentials: 'include'
```

SSE should also keep cookies:

```js
new EventSource('/hermes/api/chat/stream?stream_id=...', {
  withCredentials: true,
})
```

## Rollback

To roll back only API service code, use the previous image or previous git commit while preserving all volumes:

```bash
cd /var/www/nesquena-hermes-webui
git log --oneline -5
```

Pick the previous known-good commit in `/var/www/nesquena-hermes-webui`, then
rebuild the active compose project:

```bash
cd /var/www/nesquena-hermes-webui
docker compose up -d --build --force-recreate hermes-webui
```

Do not delete `/root/.hermes`, `/root/.hermes/webui-mvp`, or
`/root/.hermes/workspace` during rollback.

# Deploy 172.234.237.195

This runbook deploys the Hermes API service from a local development machine to
the existing production server.

```text
host: 172.234.237.195
ssh user: root
public API URL: http://172.234.237.195:8787
active server repo: /var/www/nesquena-hermes-webui
active compose file: /var/www/nesquena-hermes-webui/docker-compose.yml
```

Do not write SSH passwords, service passwords, `hermes_session` cookies,
long-lived API tokens, or provider API keys into this repository. Enter secrets
interactively or load them from a local password manager.

If you keep a private local deployment helper, commit only blank secret fields:

```bash
SSH_HOST="172.234.237.195"
SSH_USER="root"
SSH_PASSWORD=""
```

## Current Services

Current production relationship on `172.234.237.195`:

| Port | Container | Image | Purpose |
|---:|---|---|---|
| `8787` | `hermes-webui` | `hermes-webui-token-login:latest` | Hermes API service. External frontends call this layer through `/api/*`. |
| `8642` | `hermes` | `hermes-agent-profile-foxu:latest` | Separate Hermes OpenAI-compatible `/v1` API Server. Publicly reachable, requires API key. |

`8787` is not a proxy to `8642`. It runs this repository's profile, session,
chat, stream, auth, memory, workspace, and skill APIs against the mounted Hermes
home and agent source.

```text
Vue / browser -> http://172.234.237.195:8787/api/* -> Hermes API service
OpenAI-compatible client -> http://172.234.237.195:8642/v1/*
```

Do not rebuild or restart the `hermes` container when deploying this WebUI API
service unless you are intentionally deploying the separate OpenAI-compatible
API server.

## Current Server State

The `hermes-webui` container is owned by this compose project:

```text
project=nesquena-hermes-webui
service=hermes-webui
config_files=/var/www/nesquena-hermes-webui/docker-compose.yml
working_dir=/var/www/nesquena-hermes-webui
```

Important host paths:

```text
repo / compose dir: /var/www/nesquena-hermes-webui
Hermes home:        /var/www/hermes-agent/.hermes
API service state:  /var/www/hermes-agent/.hermes/webui-mvp
default workspace:  /var/www/hermes-agent/.hermes/profiles
agent source:       /var/www/nesquena-hermes-webui/hermes-agent-src
skills hub:         /var/www/hermes_skills_hub
```

Important container paths:

```text
/home/hermeswebui/.hermes
/.hermes
/home/hermeswebui/.hermes/webui-mvp
/.hermes/profiles
/home/hermeswebui/.hermes/hermes-agent
/var/www/hermes_skills_hub
```

Current important mounts:

```text
/var/www/hermes-agent/.hermes:/home/hermeswebui/.hermes
/var/www/hermes-agent/.hermes:/.hermes
/var/www/nesquena-hermes-webui/hermes-agent-src:/home/hermeswebui/.hermes/hermes-agent
/var/www/hermes_skills_hub:/var/www/hermes_skills_hub:ro
```

Current important environment:

```text
HERMES_WEBUI_HOST=0.0.0.0
HERMES_WEBUI_PORT=8787
HERMES_WEBUI_STATE_DIR=/home/hermeswebui/.hermes/webui-mvp
HERMES_WEBUI_API_TOKENS_FILE=/home/hermeswebui/.hermes/webui-mvp/api_tokens.json
HERMES_WEBUI_DEFAULT_WORKSPACE=/.hermes/profiles
HERMES_HOME=/home/hermeswebui/.hermes
HERMES_WEBUI_AGENT_DIR=/home/hermeswebui/.hermes/hermes-agent
HERMES_SKILLS_HUB_DIR=/var/www/hermes_skills_hub
HERMES_COMMUNITY_SKILLS_DIR=/var/www/hermes_skills_hub/hermes-community-skills
HERMES_BUILT_IN_SKILLS_DIR=/var/www/hermes_skills_hub/hermes-built-in-skills
HERMES_OPTIONAL_SKILLS_DIR=/var/www/hermes_skills_hub/hermes-optional-skills
HERMES_BIOCLAW_SKILLS_DIR=/var/www/hermes_skills_hub/hermes-bioclaw-skills
HERMES_TALENT_MARKET_DIR=/var/www/hermes_skills_hub/hermes_talent_market
HERMES_WEBUI_CORS_ALLOW_ALL=1
```

The old `/root/.hermes` layout may appear in historical notes, but it is not the
current WebUI API service state path. Do not use `/root/.hermes` as the source of
truth for this compose deployment.

## Token Login State

The API service supports long-lived token login:

```http
POST /api/auth/token-login
```

Token config is stored on the server at:

```text
/var/www/hermes-agent/.hermes/webui-mvp/api_tokens.json
```

The current test token id is:

```text
digital-employee-local-test
```

The plaintext test token is stored only on the server in a root-only file:

```text
/var/www/hermes-agent/.hermes/webui-mvp/api_token.digital-employee-local-test.secret
```

Do not print that token in logs or paste it into docs. The JSON config stores
only `sha256:<hex>` token hashes.

## Deploy From Local Machine

On the local development machine, commit and push the API service changes:

```bash
cd '/Users/mac/Documents/ljl-project/nesquena:hermes-webui'
git status --short
git push origin master
```

Then SSH into the server:

```bash
ssh root@172.234.237.195
```

All remaining commands in this section run on the server.

First confirm the active container and compose ownership:

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}'
docker inspect hermes-webui --format 'project={{ index .Config.Labels "com.docker.compose.project" }} service={{ index .Config.Labels "com.docker.compose.service" }} config_files={{ index .Config.Labels "com.docker.compose.project.config_files" }} working_dir={{ index .Config.Labels "com.docker.compose.project.working_dir" }}'
docker inspect hermes-webui --format '{{range .Mounts}}{{println .Source "=>" .Destination "(" .Mode ")"}}{{end}}'
```

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

Build and recreate only the WebUI API service:

```bash
cd /var/www/nesquena-hermes-webui
docker compose up -d --build hermes-webui
```

This preserves the existing service name, container name, port mapping, and
mounted state volumes.

Confirm the compose owner after deploy:

```bash
docker inspect hermes-webui --format 'project={{ index .Config.Labels "com.docker.compose.project" }} service={{ index .Config.Labels "com.docker.compose.service" }} config_files={{ index .Config.Labels "com.docker.compose.project.config_files" }} working_dir={{ index .Config.Labels "com.docker.compose.project.working_dir" }}'
```

Expected:

```text
project=nesquena-hermes-webui service=hermes-webui config_files=/var/www/nesquena-hermes-webui/docker-compose.yml working_dir=/var/www/nesquena-hermes-webui
```

## Conflict Handling

If `docker compose up` prints:

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

Do not manually remove `hermes-webui` as the first fix. A name conflict usually
means the command was run from the wrong compose project.

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
docker exec -i hermes-webui /app/venv/bin/python - <<'PY'
import api.routes as routes
print(routes.__file__)
print(hasattr(routes, "_requested_sessions_profile"))
PY
```

Expected output includes:

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
Access-Control-Allow-Credentials: true
Access-Control-Allow-Methods: GET, POST, PATCH, DELETE, OPTIONS
Access-Control-Allow-Headers: Content-Type, Authorization
Vary: Origin
```

Token login without printing the token:

```bash
TOKEN="$(cat /var/www/hermes-agent/.hermes/webui-mvp/api_token.digital-employee-local-test.secret)"
curl -sS -D /tmp/hermes-token-login.headers \
  -c /tmp/hermes-cookie.jar \
  -o /tmp/hermes-token-login.json \
  -X POST http://127.0.0.1:8787/api/auth/token-login \
  -H 'Content-Type: application/json' \
  -H 'Origin: http://localhost:5173' \
  -d "{\"token\":\"${TOKEN}\"}"
python3 -m json.tool /tmp/hermes-token-login.json
grep -qi 'Set-Cookie: hermes_session=' /tmp/hermes-token-login.headers && echo 'set-cookie: ok'
unset TOKEN
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

Public smoke test from the local development machine:

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

To roll back only the WebUI API service code, choose a previous known-good
commit in `/var/www/nesquena-hermes-webui`:

```bash
cd /var/www/nesquena-hermes-webui
git log --oneline -5
git checkout <known-good-commit>
docker compose up -d --build --force-recreate hermes-webui
```

After the rollback, run the smoke tests above.

Do not delete these host paths during rollback:

```text
/var/www/hermes-agent/.hermes
/var/www/hermes-agent/.hermes/webui-mvp
/var/www/hermes-agent/.hermes/profiles
/var/www/hermes_skills_hub
```

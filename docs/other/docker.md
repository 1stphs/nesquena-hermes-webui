# Hermes WebUI — Docker setup guide

This is the comprehensive Docker reference. For a 5-minute quickstart, see the [README Docker section](../../README.md#docker).

## TL;DR

This repo keeps one Compose entrypoint: `docker-compose.yml`. From the repo
directory, the normal server workflow is:

```bash
docker compose down
docker compose up -d
```

## 5-minute quickstart (single container)

```bash
git clone https://github.com/nesquena/hermes-webui
cd hermes-webui
cp .env.docker.example .env
# Edit .env if needed (most users can skip this on Linux)
docker compose up -d
open http://localhost:8787
```

That's it. Your existing `~/.hermes` directory is mounted, your `~/workspace` is browsable, and the WebUI auto-detects your UID/GID from the mounted volume.

## What goes wrong (and how to fix it)

### 1. "Permission denied" at startup

**Symptom**: Container starts but immediately crashes, logs show:
```
PermissionError: [Errno 13] Permission denied: '/home/hermeswebui/.hermes/...'
```

**Cause**: The container's user (UID 1000 by default) can't read your bind-mounted directory because your host files are owned by a different UID.

**Fix**: Set `UID` and `GID` in `.env` to match your host:
```bash
echo "UID=$(id -u)" >> .env
echo "GID=$(id -g)" >> .env
docker compose down && docker compose up -d
```

On macOS, host UIDs start at 501. On Linux, the first interactive user is usually UID 1000.

> **macOS Docker Desktop**: if UID mapping still misbehaves after the env fix, try toggling **Settings → General → File sharing implementation** between VirtioFS and gRPC-FUSE. Different implementations preserve UIDs across the host/container boundary differently.

### 2. ".env file mode 0640 → permission denied" (#1389)

**Symptom**: You set `HERMES_HOME_MODE=0640` (or some other group-readable mode) on your host `.env` file, container starts, then errors out:
```
[security] fixed permissions on .env (0o640 -> 0600)
failed to load .env: open .env: permission denied
```

**Cause**: WebUI's `fix_credential_permissions()` startup hook enforces 0600 by default. This is the right thing for a clean install but conflicts with operator-set modes.

**Fix**: Set one of these env vars in your `.env`:
- `HERMES_SKIP_CHMOD=1` — bypass the fixer entirely
- `HERMES_HOME_MODE=0640` — allow group bits, only strip world-readable

Both are documented in `api/startup.py::fix_credential_permissions()`.

> Note: `HERMES_HOME_MODE=0640` is a WebUI credential-file threshold. Do not
> copy it to an agent container's `HERMES_HOME` directory mode.

### 3. "Workspace appears empty even though my files are there"

**Symptom**: WebUI loads but `/workspace` shows no files.

**Cause**: Same as #1 — UID mismatch on the bind mount.

**Fix**: Same as #1 — match host UID/GID via `.env`.

### 4. "WebUI can't find agent source" (#858)

**Symptom**: WebUI logs at startup:
```
!! WARNING: hermes-agent source not found.
!!   Looked in: /home/hermeswebui/.hermes/hermes-agent
!!              /opt/hermes
```

**Cause**: The agent source needs to be mounted where the WebUI startup script expects it, or `HERMES_WEBUI_AGENT_DIR` needs to point at the mounted source path.

**Fix**: Mount the agent source at `/home/hermeswebui/.hermes/hermes-agent`, or set `HERMES_WEBUI_AGENT_DIR` to the path you mounted.

### 5. "config.yaml not loaded"

**Symptom**: You have a `config.yaml` in your host `~/.hermes/`, but the WebUI shows "no model configured" or doesn't pick up your custom providers.

**Cause**: Either the file isn't readable (UID/GID issue, see #1) or it's not in the expected path inside the container.

**Fix**:
- Verify: `docker exec hermes-webui ls -la /home/hermeswebui/.hermes/config.yaml`
- If it doesn't exist: your host bind mount is pointing at the wrong directory.
- If it exists but is unreadable: see #1 for the UID/GID fix.

## Bind-mount migration (advanced)

If you really need to bind-mount an existing host `~/.hermes` (e.g. you're keeping config in dotfiles, sharing with a non-Docker `hermes` install, etc.):

```yaml
volumes:
  hermes-home:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /home/youruser/.hermes
```

**Critical requirements**:

1. The host directory MUST be readable by your container UID. Run `id -u` on the host and ensure `~/.hermes` is owned by that UID (or readable via group bits).
2. Set `UID=$(id -u)` and `GID=$(id -g)` in `.env` when your host ownership does not match the container default.
3. If your host `.env` is mode 0640, set `HERMES_SKIP_CHMOD=1` or `HERMES_HOME_MODE=0640` so the startup hook doesn't try to enforce 0600.

## Reference

- [`docker-compose.yml`](../../docker-compose.yml) — single container (recommended)
- [`.env.docker.example`](../../.env.docker.example) — environment variable template
- [`Dockerfile`](../../Dockerfile) — single-container build
- [`docker_init.bash`](../../docker_init.bash) — container entrypoint script

## Related issues

- #1389 — `HERMES_HOME_MODE` override (fixed in v0.50.254 — agent honors `HERMES_SKIP_CHMOD` and `HERMES_HOME_MODE`)
- #1399 — UID alignment in compose files (fixed in v0.50.260 via PR #1428 + this guide)
- #858 — agent source path confusion
- #668 — auto-detect UID/GID from mounted volume
- #569 — UID/GID detection priority order

If you hit a new failure mode not covered here, please [open an issue](https://github.com/nesquena/hermes-webui/issues/new) with:

1. Which compose file you used
2. The error from `docker logs hermes-webui`
3. `docker exec hermes-webui id` output
4. `docker exec hermes-webui ls -la /home/hermeswebui/.hermes` output

#!/usr/bin/env bash
set -uo pipefail

# Clean a Hermes skills directory, keep one local skill, then install official
# optional skills through the Hermes Skills Hub. Credentials are read only from
# environment variables; do not hard-code tokens in this file.

SKILLS_DIR="${SKILLS_DIR:-/root/.hermes/skills}"
KEEP_SKILL_NAME="${KEEP_SKILL_NAME:-email}"
HERMES_HOME="${HERMES_HOME:-$(dirname "$SKILLS_DIR")}"
BACKUP_ROOT="${BACKUP_ROOT:-$HERMES_HOME/skills-backups}"
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/skills-install-$TS"
DISCOVERED_FILE="$BACKUP_DIR/discovered-official-skills.txt"
FAILED_FILE="$BACKUP_DIR/failed-official-skills.txt"
INSTALLED_FILE="$BACKUP_DIR/installed-official-skills.txt"
BROWSE_LOG="$BACKUP_DIR/hermes-skills-browse-official.log"
DRY_RUN="${DRY_RUN:-0}"
HERMES_BIN="${HERMES_BIN:-}"
HERMES_AGENT_DIR="${HERMES_AGENT_DIR:-}"
HERMES_AGENT_REPO="${HERMES_AGENT_REPO:-NousResearch/hermes-agent}"
HERMES_AGENT_REF="${HERMES_AGENT_REF:-main}"

usage() {
  cat <<'USAGE'
Usage:
  GITHUB_TOKEN=... bash scripts/install_official_skills_keep_email.sh

Environment:
  SKILLS_DIR       Target skills dir. Default: /root/.hermes/skills
  KEEP_SKILL_NAME  Skill directory to keep. Default: email
  HERMES_HOME      Hermes home. Default: parent of SKILLS_DIR
  BACKUP_ROOT      Backup root. Default: $HERMES_HOME/skills-backups
  DRY_RUN          1 = print plan only, do not move/install. Default: 0
  HERMES_BIN       Explicit hermes CLI path. Auto-detected when unset.
  HERMES_AGENT_DIR Explicit hermes-agent source dir. Auto-detected when unset.
  HERMES_AGENT_REPO GitHub repo for official optional skills. Default: NousResearch/hermes-agent
  HERMES_AGENT_REF  Git ref for official optional skills. Default: main

The script:
  1. Backs up the current skills directory.
  2. Moves all top-level entries except KEEP_SKILL_NAME and .bundled_manifest.
  3. Uses GitHub API to discover official optional skills.
  4. Downloads each official optional skill directly into SKILLS_DIR.
  5. Runs `hermes skills audit` and `hermes skills list --source hub` when available.
USAGE
}

log() {
  printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

run_or_echo() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf 'DRY_RUN:'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

resolve_hermes_bin() {
  local candidates=()
  if [[ -n "$HERMES_BIN" ]]; then
    candidates+=("$HERMES_BIN")
  fi
  if command -v hermes >/dev/null 2>&1; then
    candidates+=("$(command -v hermes)")
  fi
  candidates+=(
    "/var/www/hermes-agent-src/hermes"
    "/var/www/hermes-agent-src/venv/bin/hermes"
    "/root/.local/bin/hermes"
    "/usr/local/bin/hermes"
    "$HERMES_HOME/hermes-agent/hermes"
    "$HERMES_HOME/hermes-agent/venv/bin/hermes"
  )

  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate" && -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

resolve_agent_dir() {
  local candidates=()
  if [[ -n "$HERMES_AGENT_DIR" ]]; then
    candidates+=("$HERMES_AGENT_DIR")
  fi
  candidates+=(
    "/var/www/hermes-agent-src"
    "$HERMES_HOME/hermes-agent"
    "$(dirname "$HERMES_CLI")"
    "$(dirname "$(dirname "$HERMES_CLI")")"
    "$(dirname "$(dirname "$(dirname "$HERMES_CLI")")")"
  )

  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -d "$candidate" && -d "$candidate/optional-skills" ]]; then
      cd "$candidate" >/dev/null 2>&1 && pwd
      return 0
    fi
  done

  return 1
}

discover_official_from_agent_dir() {
  local agent_dir="$1"
  local optional_dir="$agent_dir/optional-skills"
  [[ -d "$optional_dir" ]] || return 1

  find "$optional_dir" -mindepth 3 -maxdepth 3 -type f -name SKILL.md \
    | while IFS= read -r skill_file; do
        skill_dir="$(dirname "$skill_file")"
        category="$(basename "$(dirname "$skill_dir")")"
        skill="$(basename "$skill_dir")"
        printf 'official/%s/%s\n' "$category" "$skill"
      done \
    | sort -u
}

discover_official_from_browse() {
  local page=1
  local total_pages=1
  local page_log

  : > "$BROWSE_LOG"
  while [[ "$page" -le "$total_pages" ]]; do
    page_log="$BACKUP_DIR/hermes-skills-browse-official-page-$page.log"
    if ! "$HERMES_CLI" skills browse --source official --page "$page" > "$page_log" 2>&1; then
      tail -80 "$page_log" >&2 || true
      return 1
    fi

    {
      printf '\n===== page %s =====\n' "$page"
      cat "$page_log"
    } >> "$BROWSE_LOG"

    if [[ "$page" == "1" ]]; then
      total_pages="$(
        grep -Eo 'page[[:space:]]+[0-9]+/[0-9]+' "$page_log" \
          | head -1 \
          | sed -E 's/.*\/([0-9]+)/\1/' \
          || true
      )"
      [[ -n "$total_pages" ]] || total_pages=1
    fi

    awk '
      /^[[:space:]]*│[[:space:]]*[0-9]+[[:space:]]*│/ {
        line=$0
        sub(/^[[:space:]]*│[[:space:]]*[0-9]+[[:space:]]*│[[:space:]]*/, "", line)
        sub(/[[:space:]]*│.*/, "", line)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
        if (line != "" && line !~ /…/ && line !~ /\.\.\./) print line
      }
    ' "$page_log"

    page=$((page + 1))
  done | sort -u
}

download_official_skills_from_github() {
  python3 - "$HERMES_AGENT_REPO" "$HERMES_AGENT_REF" "$SKILLS_DIR" "$DISCOVERED_FILE" "$INSTALLED_FILE" "$FAILED_FILE" "$BACKUP_DIR/github-download.log" <<'PY'
import base64
import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

repo, ref, skills_dir, discovered_file, installed_file, failed_file, log_file = sys.argv[1:]
skills_root = Path(skills_dir)
token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def log(message):
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(message + "\n")


def github_get(url):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "hermes-webui-skills-installer",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def safe_part(value):
    return value and "/" not in value and ".." not in value


quoted_ref = urllib.parse.quote(ref, safe="")
tree_url = f"https://api.github.com/repos/{repo}/git/trees/{quoted_ref}?recursive=1"
tree_payload = github_get(tree_url)
tree = tree_payload.get("tree") or []

skill_dirs = set()
for item in tree:
    path = item.get("path") or ""
    parts = path.split("/")
    if (
        len(parts) == 4
        and parts[0] == "optional-skills"
        and parts[3] == "SKILL.md"
        and safe_part(parts[1])
        and safe_part(parts[2])
    ):
        skill_dirs.add((parts[1], parts[2]))

if not skill_dirs:
    raise SystemExit("no optional skills found in GitHub tree")

with open(discovered_file, "w", encoding="utf-8") as f:
    for category, skill in sorted(skill_dirs):
        f.write(f"official/{category}/{skill}\n")

by_dir = {}
for item in tree:
    path = item.get("path") or ""
    parts = path.split("/")
    if len(parts) >= 4 and parts[0] == "optional-skills":
        key = (parts[1], parts[2])
        if key in skill_dirs and item.get("type") == "blob":
            by_dir.setdefault(key, []).append(item)

skills_root.mkdir(parents=True, exist_ok=True)

for category, skill in sorted(skill_dirs):
    skill_id = f"official/{category}/{skill}"
    dest_root = skills_root / category / skill
    try:
        if dest_root.exists():
            shutil.rmtree(dest_root)
        dest_root.mkdir(parents=True, exist_ok=True)

        for item in by_dir.get((category, skill), []):
            rel_parts = item["path"].split("/")[3:]
            if not rel_parts:
                continue
            rel_path = Path(*rel_parts)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                raise RuntimeError(f"unsafe path from GitHub tree: {item['path']}")

            blob = github_get(item["url"])
            encoding = blob.get("encoding")
            content = blob.get("content") or ""
            if encoding != "base64":
                raise RuntimeError(f"unsupported blob encoding for {item['path']}: {encoding}")
            data = base64.b64decode(content)
            target = dest_root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        if not (dest_root / "SKILL.md").exists():
            raise RuntimeError(f"downloaded skill missing SKILL.md: {skill_id}")
        with open(installed_file, "a", encoding="utf-8") as f:
            f.write(skill_id + "\n")
        log(f"installed {skill_id} -> {dest_root}")
    except Exception as exc:
        with open(failed_file, "a", encoding="utf-8") as f:
            f.write(skill_id + "\n")
        log(f"failed {skill_id}: {exc}")

PY
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

HERMES_CLI="$(resolve_hermes_bin)" || die "hermes command not found. Set HERMES_BIN=/path/to/hermes or add hermes to PATH"
AGENT_DIR="$(resolve_agent_dir || true)"

export HERMES_HOME

if [[ -n "${GITHUB_TOKEN:-}" && -z "${GH_TOKEN:-}" ]]; then
  export GH_TOKEN="$GITHUB_TOKEN"
fi

if [[ -n "${GITHUB_TOKEN:-}${GH_TOKEN:-}" ]]; then
  log "GitHub token detected via environment; authenticated GitHub access enabled"
else
  log "No GitHub token detected; GitHub API access may be rate limited"
fi

[[ "$SKILLS_DIR" == "$HERMES_HOME/skills" ]] || log "SKILLS_DIR is not HERMES_HOME/skills; using explicit SKILLS_DIR=$SKILLS_DIR"

mkdir -p "$BACKUP_DIR"
mkdir -p "$SKILLS_DIR"
: > "$FAILED_FILE"
: > "$INSTALLED_FILE"

log "Hermes home: $HERMES_HOME"
log "Skills dir:  $SKILLS_DIR"
log "Backup dir:  $BACKUP_DIR"
log "GitHub repo: $HERMES_AGENT_REPO@$HERMES_AGENT_REF"
log "Hermes CLI:  $HERMES_CLI"
[[ -n "$AGENT_DIR" ]] && log "Agent dir:   $AGENT_DIR"

if [[ -d "$SKILLS_DIR" ]]; then
  log "Backing up current skills directory"
  run_or_echo tar -czf "$BACKUP_DIR/full-skills-before-install.tgz" -C "$HERMES_HOME" "$(basename "$SKILLS_DIR")"
fi

log "Cleaning top-level skills entries, keeping '$KEEP_SKILL_NAME' and '.bundled_manifest'"
while IFS= read -r -d '' entry; do
  name="$(basename "$entry")"
  case "$name" in
    "$KEEP_SKILL_NAME"|".bundled_manifest")
      log "Keeping $entry"
      ;;
    *)
      log "Moving $entry to backup"
      run_or_echo mv "$entry" "$BACKUP_DIR/"
      ;;
  esac
done < <(find "$SKILLS_DIR" -mindepth 1 -maxdepth 1 -print0)

log "Discovering official optional skills"
if [[ "$DRY_RUN" == "1" ]]; then
  log "Skipping live discovery in DRY_RUN mode"
  : > "$DISCOVERED_FILE"
else
  download_official_skills_from_github || die "failed to download official skills from GitHub; see $BACKUP_DIR/github-download.log"
  if [[ ! -s "$DISCOVERED_FILE" ]]; then
    die "no official skills discovered from GitHub; see $BACKUP_DIR/github-download.log"
  fi
fi

log "Official install plan saved to $DISCOVERED_FILE"
if [[ -s "$DISCOVERED_FILE" ]]; then
  sed 's/^/  - /' "$DISCOVERED_FILE"
fi

if [[ "$DRY_RUN" == "1" ]]; then
  log "DRY_RUN complete"
  exit 0
fi

log "Official optional skills downloaded directly into $SKILLS_DIR"

log "Running hub audit"
"$HERMES_CLI" skills audit > "$BACKUP_DIR/audit.log" 2>&1 || log "Audit returned non-zero; see $BACKUP_DIR/audit.log"

log "Listing hub-installed skills"
"$HERMES_CLI" skills list --source hub > "$BACKUP_DIR/hub-installed-skills.txt" 2>&1 || log "Hub list returned non-zero; see $BACKUP_DIR/hub-installed-skills.txt"

log "Remaining top-level entries in $SKILLS_DIR"
find "$SKILLS_DIR" -mindepth 1 -maxdepth 2 -name SKILL.md -print | sort

installed_count="$(wc -l < "$INSTALLED_FILE" | tr -d ' ')"
failed_count="$(wc -l < "$FAILED_FILE" | tr -d ' ')"
log "Done. Installed: $installed_count; failed: $failed_count"
log "Backup and logs: $BACKUP_DIR"

if [[ "$failed_count" != "0" ]]; then
  log "Failed installs:"
  sed 's/^/  - /' "$FAILED_FILE"
  exit 2
fi

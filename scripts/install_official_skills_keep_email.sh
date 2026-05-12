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

The script:
  1. Backs up the current skills directory.
  2. Moves all top-level entries except KEEP_SKILL_NAME and .bundled_manifest.
  3. Uses `hermes skills browse --source official` to discover official optional skills.
  4. Installs each official optional skill with `hermes skills install`.
  5. Runs `hermes skills audit` and `hermes skills list --source hub`.
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

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

command -v hermes >/dev/null 2>&1 || die "hermes command not found in PATH"

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
  if ! hermes skills browse --source official > "$BROWSE_LOG" 2>&1; then
    tail -80 "$BROWSE_LOG" >&2 || true
    die "failed to browse official skills; see $BROWSE_LOG"
  fi

  grep -Eo 'official/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+' "$BROWSE_LOG" \
    | sort -u > "$DISCOVERED_FILE" || true

  if [[ ! -s "$DISCOVERED_FILE" ]]; then
    tail -120 "$BROWSE_LOG" >&2 || true
    die "no official skill identifiers found in browse output; see $BROWSE_LOG"
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

log "Installing official optional skills"
while IFS= read -r skill_id; do
  [[ -z "$skill_id" || "$skill_id" =~ ^# ]] && continue
  log "Installing $skill_id"
  if hermes skills install "$skill_id" >> "$BACKUP_DIR/install.log" 2>&1; then
    printf '%s\n' "$skill_id" >> "$INSTALLED_FILE"
  else
    printf '%s\n' "$skill_id" >> "$FAILED_FILE"
    log "Install failed for $skill_id; continuing"
  fi
done < "$DISCOVERED_FILE"

log "Running hub audit"
hermes skills audit > "$BACKUP_DIR/audit.log" 2>&1 || log "Audit returned non-zero; see $BACKUP_DIR/audit.log"

log "Listing hub-installed skills"
hermes skills list --source hub > "$BACKUP_DIR/hub-installed-skills.txt" 2>&1 || log "Hub list returned non-zero; see $BACKUP_DIR/hub-installed-skills.txt"

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

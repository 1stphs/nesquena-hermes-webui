"""Regression tests for v0.50.260 — Docker compose file invariants.

PR #1428 fixed Docker UID/GID handling. This test module pins the remaining
single-compose invariants that prevent the related bugs from coming back:

- The Docker environment template documents the bind-mount permission escape hatches
  (`HERMES_SKIP_CHMOD`, `HERMES_HOME_MODE`) inline so users hit by #1389
  or #1399 see the fix in the file they're reading
- The `.env.docker.example` template ships and documents the same vars
- Stale README references to `/root/.hermes` are gone (the agent images
  use `/home/hermes/.hermes`)
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


# ── 1: bind-mount permission escape hatches documented (#1389, #1399) ──────


def test_env_template_documents_skip_chmod_escape_hatch():
    """The env template must mention HERMES_SKIP_CHMOD inline so users
    hit by #1389 (the auth.json/.env chmod-override bug) can find the fix
    in the file they're reading. The fix shipped in v0.50.254 but Docker
    users may not be reading CHANGELOGs."""
    src = (REPO / ".env.docker.example").read_text(encoding="utf-8")
    assert "HERMES_SKIP_CHMOD" in src, (
        ".env.docker.example must document HERMES_SKIP_CHMOD as a bind-mount "
        "escape hatch so users hit by #1389 find the fix inline."
    )
    assert "HERMES_HOME_MODE" in src, (
        ".env.docker.example must document HERMES_HOME_MODE alongside HERMES_SKIP_CHMOD"
    )


# ── 2: .env.docker.example exists and documents the same vars ──────────────


def test_env_docker_example_exists():
    """The .env.docker.example template must ship in the repo so users
    can `cp .env.docker.example .env` as the first step of the quickstart."""
    p = REPO / ".env.docker.example"
    assert p.exists(), ".env.docker.example must exist in repo root"
    src = p.read_text(encoding="utf-8")

    # Must document the critical vars
    for var in ("UID", "GID", "HERMES_HOME", "HERMES_WORKSPACE",
                "HERMES_WEBUI_PASSWORD", "HERMES_SKIP_CHMOD", "HERMES_HOME_MODE"):
        assert var in src, (
            f".env.docker.example must document {var} — without it, users "
            f"hit by the related failure mode have no in-template hint."
        )


# ── 3: docs/docker.md comprehensive guide ──────────────────────────────────


def test_docs_docker_md_exists_and_covers_failure_modes():
    """The docs/docker.md guide must exist and cover the recurring failure
    modes seen in #1399, #1389, #858, #668."""
    p = REPO / "docs" / "docker.md"
    assert p.exists(), "docs/docker.md must exist as the comprehensive guide"
    src = p.read_text(encoding="utf-8")

    # Must mention each documented failure mode by issue ref
    for issue in ("#1389", "#1399", "#858"):
        assert issue in src, (
            f"docs/docker.md must reference issue {issue} so users searching "
            f"for the symptom find the right diagnostic path."
        )


# ── 4: stale /root/.hermes references removed from README ──────────────────


def test_readme_no_stale_root_hermes_path():
    """REGRESSION: stale /root/.hermes paths confuse users reading the README
    to debug their own setup."""
    src = (REPO / "README.md").read_text(encoding="utf-8")
    assert "/root/.hermes" not in src, (
        "README.md must not reference /root/.hermes. Stale paths in docs are worse "
        "than no docs at all."
    )


def test_readme_links_to_docker_md():
    """The README Docker section should point at docs/docker.md for the
    deep dive so we don't have to keep two copies of the same content
    in sync."""
    src = (REPO / "README.md").read_text(encoding="utf-8")
    assert "docs/docker.md" in src, (
        "README.md should reference docs/docker.md so users with deeper "
        "needs (bind mounts, Docker permissions) find the full guide."
    )


# ── 5: compose file parses as valid YAML ───────────────────────────────────


def test_compose_file_parses_as_valid_yaml():
    """The compose file must parse as valid YAML — without this guard,
    a stray indentation or unquoted ${VAR} could ship a broken compose
    file that breaks `docker compose up` for everyone."""
    import yaml

    path = REPO / "docker-compose.yml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise AssertionError(f"docker-compose.yml is not valid YAML: {e}")
    assert isinstance(data, dict), "docker-compose.yml must parse to a dict"
    assert "services" in data, "docker-compose.yml must define a `services:` block"


def test_env_docker_example_warns_about_home_mode_asymmetry():
    """The .env.docker.example template must warn that HERMES_HOME_MODE has
    different semantics across services."""
    src = (REPO / ".env.docker.example").read_text(encoding="utf-8")
    assert "different meanings in the WebUI and agent" in src, (
        ".env.docker.example must warn about the HERMES_HOME_MODE semantic "
        "asymmetry between WebUI and agent."
    )

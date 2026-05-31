"""Skill endpoint handlers re-exported by api.routes."""

import logging
import os
import shutil
import uuid
from pathlib import Path

from api.routes_handlers._base import _routes_binding


logger = logging.getLogger(__name__)


def _handle_skill_save(handler, body):
    try:
        _routes_binding("require")(body, "name", "content")
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e))
    skill_name = body["name"].strip().lower().replace(" ", "-")
    if not skill_name or "/" in skill_name or ".." in skill_name:
        return _routes_binding("bad")(handler, "Invalid skill name")
    category = body.get("category", "").strip()
    if category and ("/" in category or ".." in category):
        return _routes_binding("bad")(handler, "Invalid category")
    from tools.skills_tool import SKILLS_DIR

    if category:
        skill_dir = SKILLS_DIR / category / skill_name
    else:
        skill_dir = SKILLS_DIR / skill_name
    try:
        skill_dir.resolve().relative_to(SKILLS_DIR.resolve())
    except ValueError:
        return _routes_binding("bad")(handler, "Invalid skill path")
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(body["content"], encoding="utf-8")
    return _routes_binding("j")(handler, {"ok": True, "name": skill_name, "path": str(skill_file)})


def _handle_skill_delete(handler, body):
    try:
        _routes_binding("require")(body, "name")
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e))
    from tools.skills_tool import SKILLS_DIR

    matches = list(SKILLS_DIR.rglob(f"{body['name']}/SKILL.md"))
    if not matches:
        return _routes_binding("bad")(handler, "Skill not found", 404)
    skill_dir = matches[0].parent
    shutil.rmtree(str(skill_dir))
    return _routes_binding("j")(handler, {"ok": True, "name": body["name"]})


def _configured_skills_source_root(
    env_names: tuple[str, ...],
    legacy_default: str,
    hub_child: str,
) -> Path:
    for env_name in env_names:
        raw = os.getenv(env_name, "").strip()
        if raw:
            return Path(raw).expanduser().resolve()

    hub_root = os.getenv("HERMES_SKILLS_HUB_DIR", "").strip()
    if hub_root:
        return (Path(hub_root).expanduser() / hub_child).resolve()

    return Path(legacy_default).expanduser().resolve()


def _community_skills_root() -> Path:
    return _configured_skills_source_root(
        ("HERMES_COMMUNITY_SKILLS_DIR",),
        "/var/www/hermes-community-skills",
        "hermes-community-skills",
    )


def _built_in_skills_root() -> Path:
    return _configured_skills_source_root(
        ("HERMES_BUILT_IN_SKILLS_DIR", "HERMES_BUILTIN_SKILLS_DIR"),
        "/var/www/hermes-built-in-skills",
        "hermes-built-in-skills",
    )


def _optional_skills_root() -> Path:
    return _configured_skills_source_root(
        ("HERMES_OPTIONAL_SKILLS_DIR",),
        "/var/www/hermes-optional-skills",
        "hermes-optional-skills",
    )


def _bioclaw_skills_root() -> Path:
    return _configured_skills_source_root(
        ("HERMES_BIOCLAW_SKILLS_DIR",),
        "/var/www/hermes-bioclaw-skills",
        "hermes-bioclaw-skills",
    )


def _community_skill_roots() -> tuple[Path, ...]:
    return (
        _community_skills_root(),
        _built_in_skills_root(),
        _optional_skills_root(),
        _bioclaw_skills_root(),
    )


def _body_first_path(body: dict, *keys: str) -> str:
    for key in keys:
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _coerce_hermes_home_path(value: str) -> Path:
    normalized = str(value or "").strip().replace("\\", "/")
    lowered = normalized.lower()
    suffix = None
    for prefix in ("/.hermes", ".hermes"):
        if lowered == prefix:
            suffix = ""
            break
        if lowered.startswith(prefix + "/"):
            suffix = normalized[len(prefix):].lstrip("/")
            break

    if suffix is None:
        marker = "/.hermes"
        marker_with_sep = marker + "/"
        idx = lowered.find(marker_with_sep)
        if idx >= 0:
            suffix = normalized[idx + len(marker):].lstrip("/")
        elif lowered.endswith(marker):
            suffix = ""

    if suffix is None:
        return Path(value).expanduser().resolve()

    from api.profiles import _DEFAULT_HERMES_HOME

    base_home = Path(_DEFAULT_HERMES_HOME).expanduser().resolve()
    candidate = (base_home / suffix).resolve()
    candidate.relative_to(base_home)
    return candidate


def _handle_skill_install_community(handler, body):
    source_raw = _body_first_path(
        body,
        "source_path",
        "skill_path",
        "community_skill_path",
        "path",
    )
    target_raw = _body_first_path(
        body,
        "profile_skills_path",
        "target_skills_path",
        "skills_path",
        "target_path",
    )
    if not source_raw:
        return _routes_binding("bad")(handler, "source_path is required")
    if not target_raw:
        return _routes_binding("bad")(handler, "profile_skills_path is required")

    try:
        source_dir = Path(source_raw).expanduser().resolve()
        target_skills_dir = _coerce_hermes_home_path(target_raw)
        if not any(
            source_dir == root or source_dir.is_relative_to(root)
            for root in _community_skill_roots()
        ):
            raise ValueError
    except ValueError:
        return _routes_binding("bad")(handler, "source_path must be inside the community skills directory", 400)
    except OSError as e:
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e), 400)

    if not source_dir.exists() or not source_dir.is_dir():
        return _routes_binding("bad")(handler, "Skill source not found", 404)
    if not (source_dir / "SKILL.md").is_file():
        return _routes_binding("bad")(handler, "Skill source must contain SKILL.md", 400)
    if target_skills_dir.name != "skills":
        return _routes_binding("bad")(handler, "profile_skills_path must be a skills directory", 400)

    skill_name = source_dir.name
    if not skill_name or skill_name in (".", "..") or "/" in skill_name or "\\" in skill_name:
        return _routes_binding("bad")(handler, "Invalid skill directory name", 400)

    destination = target_skills_dir / skill_name
    overwrite = bool(body.get("overwrite", False))
    if destination.exists() and not overwrite:
        return _routes_binding("bad")(handler, "Skill already installed", 409)

    temp_destination = target_skills_dir / f".{skill_name}.installing-{uuid.uuid4().hex}"
    try:
        target_skills_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, temp_destination, symlinks=True)
        if destination.exists():
            if destination.is_dir() and not destination.is_symlink():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        temp_destination.rename(destination)
    except OSError as e:
        try:
            if temp_destination.exists():
                shutil.rmtree(temp_destination)
        except OSError:
            pass
        logger.exception("Failed to install community skill")
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e), 500)

    return _routes_binding("j")(
        handler,
        {
            "ok": True,
            "name": skill_name,
            "source_path": str(source_dir),
            "profile_skills_path": str(target_skills_dir),
            "installed_path": str(destination),
            "overwritten": overwrite,
        },
    )


def _handle_skill_uninstall_profile(handler, body):
    skill_name = str(body.get("name") or body.get("skill_name") or "").strip()
    target_raw = _body_first_path(
        body,
        "profile_skills_path",
        "target_skills_path",
        "skills_path",
        "target_path",
    )
    if not skill_name:
        return _routes_binding("bad")(handler, "name is required")
    if not target_raw:
        return _routes_binding("bad")(handler, "profile_skills_path is required")
    if skill_name in (".", "..") or "/" in skill_name or "\\" in skill_name or ".." in skill_name:
        return _routes_binding("bad")(handler, "Invalid skill name")

    try:
        target_skills_dir = _coerce_hermes_home_path(target_raw)
        if target_skills_dir.name != "skills":
            return _routes_binding("bad")(handler, "profile_skills_path must be a skills directory", 400)
        destination = (target_skills_dir / skill_name).resolve()
        destination.relative_to(target_skills_dir)
    except ValueError:
        return _routes_binding("bad")(handler, "Invalid skill path", 400)
    except OSError as e:
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e), 400)

    if not destination.exists() or not destination.is_dir() or not (destination / "SKILL.md").is_file():
        return _routes_binding("bad")(handler, "Skill not found", 404)

    try:
        shutil.rmtree(destination)
    except OSError as e:
        logger.exception("Failed to uninstall profile skill")
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e), 500)

    return _routes_binding("j")(
        handler,
        {
            "ok": True,
            "name": skill_name,
            "profile_skills_path": str(target_skills_dir),
            "removed_path": str(destination),
        },
    )

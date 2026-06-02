"""Profile endpoint handlers re-exported by api.routes."""

import json
import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import parse_qs

from api.routes_handlers._base import _routes_binding


logger = logging.getLogger(__name__)


def _known_profile_memory_roots() -> tuple[Path, Path, set[Path]]:
    """Return canonical profile roots accepted by the path-scoped memory API."""
    from api.profiles import _DEFAULT_HERMES_HOME, _profiles_root, list_profiles_api

    base_home = Path(_DEFAULT_HERMES_HOME).expanduser().resolve()
    profiles_root = Path(_profiles_root()).expanduser().resolve()
    known_paths = {base_home}
    for item in list_profiles_api():
        raw_path = str((item or {}).get("path") or "").strip()
        if not raw_path:
            continue
        try:
            known_paths.add(Path(raw_path).expanduser().resolve())
        except (OSError, RuntimeError):
            continue
    return base_home, profiles_root, known_paths

def _normalize_profile_memory_path(raw_path: str, base_home: Path) -> Path:
    raw_path = str(raw_path or "").strip()
    if not raw_path:
        raise ValueError("path is required")
    if raw_path == "/.hermes":
        return base_home
    if raw_path.startswith("/.hermes/"):
        return (base_home.parent / raw_path.lstrip("/")).resolve()
    return Path(raw_path).expanduser().resolve()

def _resolve_profile_memory_file(profile_path: str, filename: str = "MEMORY.md") -> tuple[Path, Path]:
    base_home, profiles_root, known_paths = _known_profile_memory_roots()
    profile_home = _normalize_profile_memory_path(profile_path, base_home)

    is_known_profile = profile_home in known_paths
    is_named_profile = profile_home.parent == profiles_root
    is_root_profile = profile_home == base_home
    if not (is_known_profile or is_named_profile or is_root_profile):
        raise ValueError("profile_path must point to a Hermes profile directory")

    if not profile_home.exists() or not profile_home.is_dir():
        raise FileNotFoundError("Profile not found")

    memory_dir = (profile_home / "memories").resolve()
    try:
        memory_dir.relative_to(profile_home)
    except ValueError as exc:
        raise ValueError("Invalid memories directory") from exc

    return profile_home, memory_dir / filename

def _profile_memory_payload(raw_path: str, profile_home: Path, memory_file: Path) -> dict:
    exists = memory_file.exists()
    content = (
        memory_file.read_text(encoding="utf-8", errors="replace")
        if exists
        else ""
    )
    return {
        "path": str(raw_path or profile_home),
        "profile_path": str(profile_home),
        "content": content,
    }

def _handle_profile_memory_read(handler, parsed):
    qs = parse_qs(parsed.query)
    profile_path = qs.get("path", qs.get("profile_path", [""]))[0]
    try:
        profile_home, memory_file = _resolve_profile_memory_file(profile_path)
        return _routes_binding("j")(handler, _profile_memory_payload(profile_path, profile_home, memory_file))
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e), 400)
    except FileNotFoundError as e:
        return _routes_binding("bad")(handler, str(e), 404)
    except OSError as e:
        logger.exception("Failed to read profile memory")
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e), 500)

def _handle_profile_user_read(handler, parsed):
    qs = parse_qs(parsed.query)
    profile_path = qs.get("path", qs.get("profile_path", [""]))[0]
    try:
        profile_home, user_file = _resolve_profile_memory_file(profile_path, "USER.md")
        return _routes_binding("j")(handler, _profile_memory_payload(profile_path, profile_home, user_file))
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e), 400)
    except FileNotFoundError as e:
        return _routes_binding("bad")(handler, str(e), 404)
    except OSError as e:
        logger.exception("Failed to read profile user file")
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e), 500)

_PROFILE_AGENT_NAME_MAX = 50

_PROFILE_AGENT_DESCRIPTION_MAX = 80

_PROFILE_AGENT_PROMPT_MAX = 1000

_PROFILE_AGENT_AVATAR_MAX = 200_000

_PROFILE_AGENT_STATUSES = {"active", "draft"}

_PROFILE_AGENT_RECOMMENDED_SKILLS = (
    "web-search",
    "doc-summary",
    "document-summary",
    "table-analysis",
    "spreadsheet-analysis",
    "meeting-notes",
    "ocr-and-documents",
    "google-workspace",
)

def _profile_agent_text(body: dict, keys: tuple[str, ...], label: str,
                        *, required: bool = True, max_len: int | None = None) -> str:
    value = ""
    for key in keys:
        if key in body and body.get(key) is not None:
            value = str(body.get(key) or "").strip()
            break
    if required and not value:
        raise ValueError(f"{label} is required")
    if max_len is not None and len(value) > max_len:
        raise ValueError(f"{label} must be at most {max_len} characters")
    return value

def _slugify_profile_agent_id(display_name: str, explicit_id: str = "") -> str:
    raw = str(explicit_id or "").strip().lower()
    if raw:
        raw = raw.replace(" ", "-")
        raw = re.sub(r"[^a-z0-9_-]+", "-", raw)
    else:
        raw = str(display_name or "").strip().lower()
        raw = re.sub(r"[^a-z0-9_-]+", "-", raw)
    raw = re.sub(r"-{2,}", "-", raw).strip("-_")
    if not raw:
        raw = f"agent-{uuid.uuid4().hex[:8]}"
    if len(raw) > 150:
        raw = raw[:150].strip("-_")
    if raw == "default" or not re.fullmatch(r"^[a-z0-9][a-z0-9_-]{0,149}$", raw):
        raise ValueError(
            "profile_id must use lowercase letters, numbers, hyphens, underscores, "
            "start with a letter or number, and be at most 150 characters"
        )
    return raw

def _normalize_profile_agent_status(body: dict) -> str:
    if bool(body.get("draft")):
        return "draft"
    status = str(body.get("status") or "active").strip().lower()
    if status not in _PROFILE_AGENT_STATUSES:
        raise ValueError("status must be active or draft")
    return status

def _load_profile_agent_skills_catalog() -> list[dict]:
    try:
        from tools.skills_tool import skills_list as _skills_list
    except Exception:
        return []

    raw = _skills_list()
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict):
        return []

    result: list[dict] = []
    seen: set[str] = set()
    for item in data.get("skills", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append({
            "name": name,
            "description": str(item.get("description") or ""),
            "category": str(item.get("category") or ""),
        })
    return result

def _profile_agent_skill_matches(skill: dict, query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(
        str(skill.get(key) or "").lower()
        for key in ("name", "description", "category")
    )
    return query.lower() in haystack

def _recommended_profile_agent_skills(catalog: list[dict], limit: int = 8) -> list[dict]:
    by_name = {str(skill.get("name") or ""): skill for skill in catalog}
    recommended: list[dict] = []
    seen: set[str] = set()
    for name in _PROFILE_AGENT_RECOMMENDED_SKILLS:
        skill = by_name.get(name)
        if skill and name not in seen:
            recommended.append(skill)
            seen.add(name)
    for skill in catalog:
        name = str(skill.get("name") or "")
        if len(recommended) >= limit:
            break
        if name and name not in seen:
            recommended.append(skill)
            seen.add(name)
    return recommended[:limit]

def _normalize_profile_agent_skills(
    raw_skills,
    catalog: list[dict],
    *,
    required: bool = True,
) -> list[str]:
    if isinstance(raw_skills, str):
        values = [part.strip() for part in raw_skills.split(",")]
    elif isinstance(raw_skills, list):
        values = []
        for part in raw_skills:
            if isinstance(part, dict):
                value = part.get("name") or part.get("id") or part.get("skill")
                values.append(str(value or "").strip())
            else:
                values.append(str(part).strip())
    else:
        values = []

    selected: list[str] = []
    selected_seen: set[str] = set()
    for value in values:
        if not value:
            continue
        key = value.lower()
        if key in selected_seen:
            continue
        selected_seen.add(key)
        selected.append(value)

    if not selected and required:
        raise ValueError("skills is required")
    if not selected:
        return []

    available = {
        str(skill.get("name") or "").lower(): str(skill.get("name") or "")
        for skill in catalog
        if str(skill.get("name") or "").strip()
    }
    if not available:
        raise ValueError("No skills are available to mount")

    unknown = [name for name in selected if name.lower() not in available]
    if unknown:
        raise ValueError(f"Unknown skill(s): {', '.join(unknown)}")

    return [available[name.lower()] for name in selected]

_PROFILE_AGENT_DEFAULT_CLONE_FROM = "template_profile"

def _profile_agent_create_options(body: dict) -> dict:
    clone_from = body.get("clone_from", _PROFILE_AGENT_DEFAULT_CLONE_FROM)
    if clone_from is not None:
        clone_from = str(clone_from).strip() or None
        if clone_from and not re.fullmatch(r"^[a-z0-9][a-z0-9_-]{0,149}$", clone_from):
            raise ValueError("Invalid clone_from name")

    base_url = body.get("base_url", "")
    base_url = str(base_url).strip() if base_url else None
    if base_url and not base_url.startswith(("http://", "https://")):
        raise ValueError("base_url must start with http:// or https://")

    api_key = body.get("api_key", "")
    api_key = str(api_key).strip() if api_key else None

    return {
        "clone_from": clone_from,
        "clone_config": bool(body.get("clone_config", True)),
        "base_url": base_url,
        "api_key": api_key,
    }

def _profile_agent_markdown(agent: dict) -> str:
    frontmatter = {
        "profile_id": agent["profile_id"],
        "profile_key": agent["profile_id"],
        "profile_name": agent["profile_name"],
        "avatar": agent["avatar"],
        "description": agent["description"],
        "status": agent["status"],
        "skills": agent["skills"],
        "created_at": agent["created_at"],
        "updated_at": agent["updated_at"],
    }
    try:
        import yaml as _yaml

        metadata = _yaml.safe_dump(
            frontmatter,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).strip()
    except Exception:
        metadata = json.dumps(frontmatter, ensure_ascii=False, indent=2)
    return f"---\n{metadata}\n---\n\n{agent['prompt'].rstrip()}\n"

def _write_profile_agent_files(profile_path: Path, agent: dict) -> dict:
    profile_path = Path(profile_path).expanduser().resolve()
    profile_path.mkdir(parents=True, exist_ok=True)

    soul_path = profile_path / "SOUL.md"
    soul_path.write_text(agent["prompt"].rstrip() + "\n", encoding="utf-8")

    agent_dir = profile_path / "profiles"
    agent_dir.mkdir(parents=True, exist_ok=True)
    agent_file = agent_dir / "default.md"
    agent_file.write_text(_profile_agent_markdown(agent), encoding="utf-8")

    webui_dir = profile_path / "webui"
    webui_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = webui_dir / "agent.json"
    metadata_path.write_text(
        json.dumps(agent, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return {
        "profile_path": str(profile_path),
        "soul_path": str(soul_path),
        "agent_file_path": str(agent_file),
        "metadata_path": str(metadata_path),
    }

def _read_profile_agent_metadata(profile_path: Path) -> dict:
    metadata_path = Path(profile_path).expanduser() / "webui" / "agent.json"
    if not metadata_path.exists():
        return {}
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError("existing agent metadata is invalid") from exc
    return data if isinstance(data, dict) else {}

def _coerce_profile_soul_candidate(value: str) -> Path:
    normalized = value.replace("\\", "/").strip()
    lowered = normalized.lower()
    suffix = None
    for prefix in ("/.hermes", ".hermes"):
        if lowered == prefix:
            suffix = ""
            break
        if lowered.startswith(prefix + "/"):
            suffix = normalized[len(prefix):].lstrip("/")
            break

    literal = Path(value).expanduser()
    if suffix is None and literal.exists():
        return literal

    if suffix is None:
        marker = "/.hermes"
        marker_with_sep = marker + "/"
        idx = lowered.find(marker_with_sep)
        if idx >= 0:
            suffix = normalized[idx + len(marker):].lstrip("/")
        elif lowered.endswith(marker):
            suffix = ""

    if suffix is None:
        return literal

    from api.profiles import _DEFAULT_HERMES_HOME

    base_home = Path(_DEFAULT_HERMES_HOME).expanduser().resolve()
    candidate = (base_home / suffix).resolve()
    try:
        candidate.relative_to(base_home)
    except ValueError as exc:
        raise ValueError("path must stay within Hermes home") from exc
    return candidate

def _resolve_profile_soul_path(raw_path: str) -> Path:
    value = str(raw_path or "").strip()
    if not value:
        raise ValueError("path is required")

    candidate = _coerce_profile_soul_candidate(value)
    if candidate.name.lower() == "soul.md":
        profile_path = candidate.parent
        soul_path = candidate
    else:
        if candidate.exists() and not candidate.is_dir():
            raise ValueError("path must point to a profile directory or SOUL.md")
        profile_path = candidate
        soul_path = candidate / "SOUL.md"

    profile_path = profile_path.resolve()
    if not profile_path.exists() or not profile_path.is_dir():
        raise FileNotFoundError("profile path not found")

    soul_path = soul_path.resolve()
    if soul_path.name.lower() != "soul.md":
        raise ValueError("path must point to a profile directory or SOUL.md")
    try:
        soul_path.relative_to(profile_path)
    except ValueError as exc:
        raise ValueError("SOUL.md must stay within the profile directory") from exc
    if not soul_path.exists():
        raise FileNotFoundError("SOUL.md not found")
    if not soul_path.is_file():
        raise ValueError("SOUL.md is not a file")
    return soul_path

def _profile_soul_path_from_body(body: dict) -> str:
    raw_path = body.get("path")
    if raw_path is None:
        raw_path = body.get("profile_path")
    if raw_path is None:
        raw_path = body.get("soul_path")
    return str(raw_path or "")

def _profile_soul_path_from_query(parsed) -> str:
    qs = parse_qs(parsed.query)
    raw_path = qs.get("path", [None])[0]
    if raw_path is None:
        raw_path = qs.get("profile_path", [None])[0]
    if raw_path is None:
        raw_path = qs.get("soul_path", [None])[0]
    return str(raw_path or "")

def _handle_profile_soul_read(handler, parsed):
    try:
        soul_path = _resolve_profile_soul_path(_profile_soul_path_from_query(parsed))
        return _routes_binding("j")(handler, {
            "path": str(soul_path),
            "profile_path": str(soul_path.parent),
            "content": soul_path.read_text(encoding="utf-8"),
        })
    except FileNotFoundError as e:
        return _routes_binding("bad")(handler, str(e), 404)
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e), 400)
    except OSError as e:
        logger.exception("Failed to read profile SOUL.md")
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e), 500)

def _handle_profile_change_soul(handler, body):
    try:
        if "content" not in body or body.get("content") is None:
            raise ValueError("content is required")

        soul_path = _resolve_profile_soul_path(_profile_soul_path_from_body(body))
        content = str(body.get("content"))
        soul_path.write_text(content, encoding="utf-8")
        return _routes_binding("j")(handler, {
            "ok": True,
            "path": str(soul_path),
            "profile_path": str(soul_path.parent),
        })
    except FileNotFoundError as e:
        return _routes_binding("bad")(handler, str(e), 404)
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e), 400)
    except OSError as e:
        logger.exception("Failed to update profile SOUL.md")
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e), 500)

def _profile_agent_detail_from_profile(profile: dict) -> dict:
    raw_path = str(profile.get("path") or "").strip()
    metadata: dict = {}
    if raw_path:
        try:
            metadata = _read_profile_agent_metadata(Path(raw_path))
        except ValueError:
            metadata = {}

    skills = metadata.get("skills")
    if not isinstance(skills, list):
        skills = []

    skill_count = profile.get("skill_count")
    if not isinstance(skill_count, int):
        skill_count = len(skills)

    return {
        "profile_name": str(
            metadata.get("profile_name")
            or profile.get("profile_name")
            or profile.get("name")
            or ""
        ),
        "description": str(metadata.get("description") or ""),
        "prompt": str(metadata.get("prompt") or ""),
        "skills": [str(skill) for skill in skills],
        "avatar": str(metadata.get("avatar") or profile.get("avatar") or ""),
        "skill_count": skill_count,
    }

def _resolve_profile_agent_update_target(body: dict) -> tuple[str, Path]:
    requested_profile = str(
        body.get("profile_id")
        or body.get("profile")
        or body.get("profile_key")
        or ""
    ).strip()

    if requested_profile:
        from api.profiles import _PROFILE_ID_RE, get_hermes_home_for_profile

        if requested_profile != "default" and not _PROFILE_ID_RE.fullmatch(requested_profile):
            raise ValueError("invalid profile_id")
        return requested_profile, Path(get_hermes_home_for_profile(requested_profile)).expanduser()

    from api.profiles import get_active_profile_name, get_active_hermes_home

    return (
        get_active_profile_name() or "default",
        Path(get_active_hermes_home()).expanduser(),
    )

def _handle_profile_agent_skills(handler, parsed):
    qs = parse_qs(parsed.query)
    query = qs.get("q", [""])[0].strip()
    catalog = _routes_binding("_load_profile_agent_skills_catalog")()
    filtered = [
        skill for skill in catalog
        if _profile_agent_skill_matches(skill, query)
    ]
    return _routes_binding("j")(handler, {
        "query": query,
        "skills": filtered,
        "recommended": _recommended_profile_agent_skills(catalog),
        "count": len(filtered),
    })

def _handle_profile_agents_list(handler):
    from api.profiles import list_profiles_api, get_active_profile_name

    profiles = [
        _profile_agent_detail_from_profile(profile)
        for profile in list_profiles_api()
        if isinstance(profile, dict)
    ]
    return _routes_binding("j")(handler, {
        "profiles": profiles,
        "active": get_active_profile_name(),
    })

def _talent_market_profiles_root() -> Path:
    raw = os.getenv("HERMES_TALENT_MARKET_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()

    hub_root = os.getenv("HERMES_SKILLS_HUB_DIR", "").strip()
    if hub_root:
        return (Path(hub_root).expanduser() / "hermes_talent_market").resolve()

    return Path("/var/www/hermes_talent_market").expanduser().resolve()

def _coerce_profile_install_path(raw_path: str) -> Path:
    value = str(raw_path or "").strip()
    if not value:
        raise ValueError("profile_path is required")

    normalized = value.replace("\\", "/")
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

def _handle_profile_install_profiles(handler, body):
    profile_name = str(body.get("profile_name") or "").strip()
    source_raw = str(body.get("source_path") or "").strip()
    profile_path_raw = str(body.get("profile_path") or "").strip()
    if not profile_name:
        return _routes_binding("bad")(handler, "profile_name is required")
    if not source_raw:
        return _routes_binding("bad")(handler, "source_path is required")

    try:
        from api.profiles import _invalidate_root_profile_cache, _profiles_root, _validate_profile_name

        _validate_profile_name(profile_name)
        talent_root = _talent_market_profiles_root()
        source_dir = Path(source_raw).expanduser().resolve()
        try:
            source_dir.relative_to(talent_root)
        except ValueError as exc:
            raise ValueError("source_path must be inside the talent market directory") from exc

        profiles_root = Path(_profiles_root()).expanduser().resolve()
        destination = (
            _coerce_profile_install_path(profile_path_raw).resolve()
            if profile_path_raw
            else (profiles_root / profile_name).resolve()
        )
        try:
            destination.relative_to(profiles_root)
        except ValueError as exc:
            raise ValueError("profile_path must be inside the Hermes profiles directory") from exc
        if destination.name != profile_name:
            raise ValueError("profile_path must end with profile_name")
        if destination.parent != profiles_root:
            raise ValueError("profile_path must be a direct child of the Hermes profiles directory")
        if source_dir == destination:
            raise ValueError("source_path and profile_path must be different")

        if not source_dir.exists() or not source_dir.is_dir():
            return _routes_binding("bad")(handler, "Profile source not found", 404)

        overwrite = bool(body.get("overwrite", False))
        if destination.exists() and not overwrite:
            return _routes_binding("bad")(handler, "Profile already installed", 409)

        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_destination = destination.parent / f".{profile_name}.installing-{uuid.uuid4().hex}"
        try:
            shutil.copytree(source_dir, temp_destination, symlinks=True)
            if destination.exists():
                if destination.is_dir() and not destination.is_symlink():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
            temp_destination.rename(destination)
        except OSError as exc:
            try:
                if temp_destination.exists():
                    if temp_destination.is_dir() and not temp_destination.is_symlink():
                        shutil.rmtree(temp_destination)
                    else:
                        temp_destination.unlink()
            except OSError:
                pass
            logger.exception("Failed to install talent market profile")
            return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(exc), 500)

        _invalidate_root_profile_cache()
        return _routes_binding("j")(handler, {
            "ok": True,
            "profile": {
                "name": profile_name,
                "path": str(destination),
            },
            "source_path": str(source_dir),
            "installed_path": str(destination),
            "overwritten": overwrite,
        })
    except ValueError as exc:
        return _routes_binding("bad")(handler, str(exc), 400)
    except OSError as exc:
        logger.exception("Failed to install talent market profile")
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(exc), 500)

def _handle_profile_agent_create(handler, body):
    try:
        profile_name = _profile_agent_text(
            body,
            ("profile_name", "display_name", "name"),
            "name",
            max_len=_PROFILE_AGENT_NAME_MAX,
        )
        description = _profile_agent_text(
            body,
            ("description", "summary", "one_liner"),
            "description",
            max_len=_PROFILE_AGENT_DESCRIPTION_MAX,
        )
        prompt = _profile_agent_text(
            body,
            ("prompt", "system_prompt"),
            "prompt",
            max_len=_PROFILE_AGENT_PROMPT_MAX,
        )
        avatar = _profile_agent_text(
            body,
            ("avatar", "avatar_url", "icon"),
            "avatar",
            required=False,
            max_len=_PROFILE_AGENT_AVATAR_MAX,
        )
        if "skills" in body or "skill_names" in body:
            catalog = _routes_binding("_load_profile_agent_skills_catalog")()
            skills = _normalize_profile_agent_skills(
                body.get("skills", body.get("skill_names")),
                catalog,
            )
        else:
            skills = []
        profile_id = _slugify_profile_agent_id(
            profile_name,
            str(body.get("profile_id") or body.get("profile_key") or "").strip(),
        )
        status = _normalize_profile_agent_status(body)
        create_options = _profile_agent_create_options(body)

        from api.profiles import create_profile_api

        profile = create_profile_api(profile_id, **create_options)
        raw_profile_path = str(profile.get("path") or "").strip()
        if not raw_profile_path:
            raise RuntimeError("created profile did not return a path")
        profile_path = Path(raw_profile_path).expanduser()

        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        agent = {
            "profile_id": profile_id,
            "profile_name": profile_name,
            "avatar": avatar,
            "description": description,
            "prompt": prompt,
            "skills": skills,
            "status": status,
            "created_at": now,
            "updated_at": now,
        }
        paths = _write_profile_agent_files(profile_path, agent)

        return _routes_binding("j")(handler, {
            "ok": True,
            "profile": profile,
            "agent": {**agent, **paths},
        })
    except (ValueError, FileExistsError, RuntimeError) as e:
        return _routes_binding("bad")(handler, str(e), 400)
    except OSError as e:
        logger.exception("Failed to write profile agent files")
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e), 500)

def _handle_profile_agent_update(handler, body):
    try:
        profile_name = _profile_agent_text(
            body,
            ("profile_name", "display_name", "name"),
            "name",
            max_len=_PROFILE_AGENT_NAME_MAX,
        )
        description = _profile_agent_text(
            body,
            ("description", "summary", "one_liner"),
            "description",
            max_len=_PROFILE_AGENT_DESCRIPTION_MAX,
        )
        prompt = _profile_agent_text(
            body,
            ("prompt", "system_prompt"),
            "prompt",
            max_len=_PROFILE_AGENT_PROMPT_MAX,
        )
        if "skills" not in body and "skill_names" not in body:
            raise ValueError("skills is required")
        catalog = _routes_binding("_load_profile_agent_skills_catalog")()
        skills = _normalize_profile_agent_skills(
            body.get("skills", body.get("skill_names")),
            catalog,
            required=False,
        )
        active_profile_name, profile_path = _resolve_profile_agent_update_target(body)
        profile_path = profile_path.resolve()
        if not profile_path.exists():
            raise FileNotFoundError(f"profile not found: {active_profile_name}")

        existing = _read_profile_agent_metadata(profile_path)

        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        profile_id = str(existing.get("profile_id") or active_profile_name or "default")
        avatar = (
            _profile_agent_text(
                body,
                ("avatar", "avatar_url", "icon"),
                "avatar",
                required=False,
                max_len=_PROFILE_AGENT_AVATAR_MAX,
            )
            if any(key in body for key in ("avatar", "avatar_url", "icon"))
            else str(existing.get("avatar") or "")
        )
        status = str(existing.get("status") or "active").strip().lower()
        if status not in _PROFILE_AGENT_STATUSES:
            status = "active"

        agent = {
            "profile_id": profile_id,
            "profile_name": profile_name,
            "avatar": avatar,
            "description": description,
            "prompt": prompt,
            "skills": skills,
            "status": status,
            "created_at": str(existing.get("created_at") or now),
            "updated_at": now,
        }
        paths = _write_profile_agent_files(profile_path, agent)

        return _routes_binding("j")(handler, {
            "ok": True,
            "profile": {
                "name": active_profile_name,
                "path": str(profile_path),
            },
            "agent": {**agent, **paths},
        })
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        return _routes_binding("bad")(handler, str(e), 400)
    except OSError as e:
        logger.exception("Failed to update profile agent files")
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e), 500)

def _handle_profile_memory_write(handler, body):
    profile_path = body.get("path", body.get("profile_path", ""))
    if "content" not in body or body.get("content") is None:
        return _routes_binding("bad")(handler, "content is required")
    content = str(body.get("content"))
    return _write_profile_memory_file(handler, profile_path, content, "MEMORY.md", "memory_path")

def _handle_profile_user_write(handler, body):
    profile_path = body.get("path", body.get("profile_path", ""))
    if "content" not in body or body.get("content") is None:
        return _routes_binding("bad")(handler, "content is required")
    content = str(body.get("content"))
    return _write_profile_memory_file(handler, profile_path, content, "USER.md", "user_path")

def _write_profile_memory_file(handler, profile_path: str, content: str, filename: str, path_key: str):
    try:
        profile_home, memory_file = _resolve_profile_memory_file(profile_path, filename)
        memory_dir = memory_file.parent
        memory_dir.mkdir(parents=True, exist_ok=True)
        temp_file = memory_dir / f".{filename}.{uuid.uuid4().hex}.tmp"
        try:
            temp_file.write_text(content, encoding="utf-8")
            temp_file.replace(memory_file)
        finally:
            try:
                if temp_file.exists():
                    temp_file.unlink()
            except OSError:
                pass
        return _routes_binding("j")(
            handler,
            {
                "ok": True,
                "path": str(profile_path),
                "profile_path": str(profile_home),
                path_key: str(memory_file),
                "content": content,
                "mtime": memory_file.stat().st_mtime,
                "bytes": len(content.encode("utf-8")),
            },
        )
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e), 400)
    except FileNotFoundError as e:
        return _routes_binding("bad")(handler, str(e), 404)
    except OSError as e:
        logger.exception("Failed to write profile %s", filename)
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e), 500)

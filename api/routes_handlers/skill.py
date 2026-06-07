"""Skill endpoint handlers re-exported by api.routes."""

import logging
import os
import re
import shutil
import urllib.parse
import uuid
from contextlib import contextmanager
from pathlib import Path

from api.routes_handlers._base import _routes_binding


logger = logging.getLogger(__name__)


_PROFILE_INSTALLED_SKILL_EXCERPT_CHARS = 4000
_PROFILE_INSTALLED_SKILL_TEXT_LIMIT = 280
USER_SKILLS_ROOT = Path("/home/hermeswebui/.hermes/webui-mvp/users")
_USER_MY_SKILLS_DIR_NAME = "my-skills"
_USER_SKILL_NAME_MAX = 64
_USER_SKILL_SLUG_MAX = 150
_USER_SKILL_ENGLISH_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


class _UserSkillError(ValueError):
    def __init__(self, message: str, *, status: int = 400, code: str = "invalid_user_skill_request"):
        super().__init__(message)
        self.status = status
        self.code = code


def _clean_profile_installed_skill_text(
    value,
    *,
    limit: int = _PROFILE_INSTALLED_SKILL_TEXT_LIMIT,
) -> str:
    text = " ".join(str(value or "").replace("\r", "\n").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _split_skill_frontmatter(text: str) -> tuple[dict, str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        return {}, normalized

    lines = normalized.split("\n")
    closing_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = index
            break

    if closing_index is None:
        raise ValueError("missing frontmatter terminator")

    frontmatter_text = "\n".join(lines[1:closing_index])
    body = "\n".join(lines[closing_index + 1 :])
    try:
        import yaml as _yaml

        metadata = _yaml.safe_load(frontmatter_text) or {}
    except ImportError:
        metadata = _parse_simple_skill_frontmatter(frontmatter_text)
    except Exception as exc:
        raise ValueError("invalid frontmatter") from exc

    if not isinstance(metadata, dict):
        raise ValueError("frontmatter must be an object")

    return metadata, body


def _parse_simple_skill_frontmatter(frontmatter_text: str) -> dict:
    metadata = {}
    for raw_line in str(frontmatter_text or "").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line[:1].isspace():
            continue
        if ":" not in raw_line:
            raise ValueError("invalid frontmatter line")

        key, value = raw_line.split(":", 1)
        key = key.strip()
        if not key:
            raise ValueError("invalid frontmatter key")

        value = value.strip()
        if (value.startswith("[") and not value.endswith("]")) or (
            value.startswith("{") and not value.endswith("}")
        ):
            raise ValueError("invalid frontmatter value")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        metadata[key] = value
    return metadata


def _first_skill_body_summary(body: str) -> str:
    for raw_line in str(body or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line == "---":
            continue
        return _clean_profile_installed_skill_text(line)
    return ""


def _user_skill_error_response(handler, exc: _UserSkillError):
    return _routes_binding("j")(
        handler,
        {
            "error": str(exc),
            "code": exc.code,
        },
        status=exc.status,
    )


def _validate_user_skill_segment(value, field: str, *, max_length: int = _USER_SKILL_SLUG_MAX) -> str:
    segment = str(value or "").strip()
    if not segment:
        raise _UserSkillError(f"{field} is required", code=f"missing_{field}")
    if (
        segment in (".", "..")
        or "/" in segment
        or "\\" in segment
        or ".." in segment
        or "\x00" in segment
        or len(segment) > max_length
    ):
        raise _UserSkillError(f"Invalid {field}", code=f"invalid_{field}")
    return segment


def _validate_user_skill_english_name(value) -> str:
    english_name = _validate_user_skill_segment(value, "english_name", max_length=80)
    if not _USER_SKILL_ENGLISH_NAME_RE.fullmatch(english_name):
        raise _UserSkillError(
            "english_name must start with a letter or digit and contain only letters, digits, '-' or '_'",
            code="invalid_english_name",
        )
    return english_name


def _validate_user_skill_name(value) -> str:
    name = str(value or "").strip()
    if not name:
        raise _UserSkillError("name is required", code="missing_name")
    if len(name) > _USER_SKILL_NAME_MAX:
        raise _UserSkillError("name is too long", code="invalid_name")
    return name


def _resolve_inside(root: Path, *parts: str) -> Path:
    root_resolved = Path(root).expanduser().resolve(strict=False)
    target = root_resolved.joinpath(*parts).resolve(strict=False)
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise _UserSkillError("Invalid skill path", code="invalid_skill_path") from exc
    return target


def _user_my_skills_dir(user_id: str, *, create: bool = False) -> Path:
    user_segment = _validate_user_skill_segment(user_id, "user_id", max_length=128)
    root = _resolve_inside(USER_SKILLS_ROOT, user_segment, _USER_MY_SKILLS_DIR_NAME)
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def _assert_no_symlink_tree(source_dir: Path) -> None:
    if source_dir.is_symlink():
        raise _UserSkillError("Skill source cannot be a symlink", code="skill_source_symlink")
    try:
        for item in source_dir.rglob("*"):
            if item.is_symlink():
                raise _UserSkillError(
                    "Skill source cannot contain symlinks",
                    code="skill_source_symlink",
                )
    except OSError as exc:
        raise _UserSkillError(
            "Failed to inspect skill source",
            status=500,
            code="skill_source_inspect_failed",
        ) from exc


def _safe_skill_child_dir(root: Path, skill_slug: str) -> Path:
    slug = _validate_user_skill_segment(skill_slug, "skill_slug")
    return _resolve_inside(root, slug)


@contextmanager
def _skill_destination_lock(parent: Path, skill_name: str):
    lock_dir = parent / f".{skill_name}.lock"
    acquired = False
    try:
        parent.mkdir(parents=True, exist_ok=True)
        lock_dir.mkdir()
        acquired = True
        yield
    except FileExistsError as exc:
        raise _UserSkillError(
            "Skill already exists",
            status=409,
            code="skill_conflict",
        ) from exc
    finally:
        if acquired:
            try:
                lock_dir.rmdir()
            except OSError:
                pass


def _format_skill_frontmatter(metadata: dict) -> str:
    try:
        import yaml as _yaml

        dumped = _yaml.safe_dump(
            metadata,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
        lines = [line for line in dumped.splitlines() if line.strip() != "..."]
        return "\n".join(lines).rstrip() + "\n"
    except ImportError:
        lines = []
        for key, value in metadata.items():
            if isinstance(value, (list, tuple)):
                lines.append(f"{key}:")
                lines.extend(f"  - {item}" for item in value)
            elif isinstance(value, dict):
                lines.append(f"{key}: {value}")
            else:
                text = str(value).replace('"', '\\"')
                lines.append(f'{key}: "{text}"')
        return "\n".join(lines).rstrip() + "\n"


def _update_skill_frontmatter_name(skill_file: Path, name: str) -> None:
    if skill_file.is_symlink():
        raise _UserSkillError("SKILL.md cannot be a symlink", code="skill_file_symlink")
    try:
        content = skill_file.read_text(encoding="utf-8")
        metadata, body = _split_skill_frontmatter(content)
        metadata["name"] = name
        next_content = f"---\n{_format_skill_frontmatter(metadata)}---\n{body}"
        temp_file = skill_file.with_name(f".{skill_file.name}.tmp-{uuid.uuid4().hex}")
        temp_file.write_text(next_content, encoding="utf-8")
        temp_file.replace(skill_file)
    except _UserSkillError:
        raise
    except ValueError as exc:
        raise _UserSkillError(
            "Invalid SKILL.md frontmatter",
            code="invalid_skill_frontmatter",
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise _UserSkillError(
            "Failed to update SKILL.md",
            status=500,
            code="skill_file_update_failed",
        ) from exc
    finally:
        try:
            temp_file
        except UnboundLocalError:
            return
        try:
            if temp_file.exists():
                temp_file.unlink()
        except OSError:
            pass


def _copy_skill_tree_atomic(source_dir: Path, destination: Path) -> None:
    if not source_dir.is_dir() or not (source_dir / "SKILL.md").is_file():
        raise _UserSkillError("Skill source not found", status=404, code="skill_not_found")
    if destination.exists():
        raise _UserSkillError("Skill already exists", status=409, code="skill_conflict")

    _assert_no_symlink_tree(source_dir)
    destination_parent = destination.parent
    temp_destination = destination_parent / f".{destination.name}.installing-{uuid.uuid4().hex}"
    try:
        with _skill_destination_lock(destination_parent, destination.name):
            if destination.exists():
                raise _UserSkillError("Skill already exists", status=409, code="skill_conflict")
            shutil.copytree(source_dir, temp_destination, symlinks=False)
            if destination.exists():
                raise _UserSkillError("Skill already exists", status=409, code="skill_conflict")
            temp_destination.rename(destination)
    except _UserSkillError:
        raise
    except OSError as exc:
        raise _UserSkillError(
            "Failed to copy skill",
            status=500,
            code="skill_copy_failed",
        ) from exc
    finally:
        try:
            if temp_destination.exists():
                shutil.rmtree(temp_destination)
        except OSError:
            pass


def _read_user_skill(skill_dir: Path, my_skills_dir: Path) -> dict | None:
    skill_name = skill_dir.name
    if (
        not skill_name
        or skill_name in (".", "..")
        or "/" in skill_name
        or "\\" in skill_name
        or ".." in skill_name
    ):
        return None
    if not skill_dir.is_dir() or skill_dir.is_symlink():
        return None

    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file() or skill_file.is_symlink():
        return None

    try:
        skill_dir.resolve().relative_to(my_skills_dir.resolve())
        with skill_file.open("r", encoding="utf-8") as handle:
            excerpt = handle.read(_PROFILE_INSTALLED_SKILL_EXCERPT_CHARS)
        metadata, body = _split_skill_frontmatter(excerpt)
    except (OSError, UnicodeError, ValueError):
        logger.debug("Skipping unreadable or invalid user skill: %s", skill_dir)
        return None

    name = _clean_profile_installed_skill_text(metadata.get("name")) or skill_name
    description = _clean_profile_installed_skill_text(
        metadata.get("description")
    ) or _first_skill_body_summary(body)

    return {
        "id": skill_name,
        "englishName": skill_name,
        "title": skill_name,
        "name": name,
        "title_cn": name,
        "summary": description,
        "description": description,
        "path": skill_name,
        "skill_file": f"{skill_name}/SKILL.md",
        "source": "user",
    }


def _get_current_user_id(handler) -> str:
    from api.user_provider import UserProviderAuthError, current_user_id_from_handler

    try:
        return current_user_id_from_handler(handler)
    except UserProviderAuthError as exc:
        raise _UserSkillError(str(exc), status=exc.status, code=exc.code) from exc


def _get_owned_profile_home(handler, profile_name: str) -> Path:
    from api.profiles import _PROFILE_ID_RE, get_hermes_home_for_profile
    from api.user_provider import UserProviderAuthError, verify_user_profile_access

    profile = str(profile_name or "").strip()
    if not profile:
        raise _UserSkillError("profile_name is required", code="missing_profile_name")
    if not _PROFILE_ID_RE.fullmatch(profile):
        raise _UserSkillError("Invalid profile_name", code="invalid_profile_name")

    user_id = _get_current_user_id(handler)
    try:
        verify_user_profile_access(user_id, profile)
    except UserProviderAuthError as exc:
        raise _UserSkillError(str(exc), status=exc.status, code=exc.code) from exc

    try:
        return Path(get_hermes_home_for_profile(profile)).expanduser().resolve()
    except OSError as exc:
        raise _UserSkillError(
            "Failed to resolve profile home",
            status=500,
            code="profile_home_resolve_failed",
        ) from exc


def _read_profile_installed_skill(skill_dir: Path, skills_dir: Path) -> dict | None:
    skill_name = skill_dir.name
    if (
        not skill_name
        or skill_name in (".", "..")
        or "/" in skill_name
        or "\\" in skill_name
        or ".." in skill_name
    ):
        return None
    if not skill_dir.is_dir() or skill_dir.is_symlink():
        return None

    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file() or skill_file.is_symlink():
        return None

    try:
        skill_dir.resolve().relative_to(skills_dir.resolve())
        with skill_file.open("r", encoding="utf-8") as handle:
            excerpt = handle.read(_PROFILE_INSTALLED_SKILL_EXCERPT_CHARS)
        metadata, body = _split_skill_frontmatter(excerpt)
    except (OSError, UnicodeError, ValueError):
        logger.debug("Skipping unreadable or invalid profile skill: %s", skill_dir)
        return None

    name = _clean_profile_installed_skill_text(metadata.get("name")) or skill_name
    title = (
        _clean_profile_installed_skill_text(metadata.get("title"))
        or _clean_profile_installed_skill_text(metadata.get("display_name"))
        or name
    )
    description = _clean_profile_installed_skill_text(
        metadata.get("description")
    ) or _first_skill_body_summary(body)

    return {
        "id": skill_name,
        "name": name,
        "title": title,
        "description": description,
        "summary": description,
        "path": skill_name,
        "skill_file": f"{skill_name}/SKILL.md",
    }


def _handle_profile_installed_skills(handler, parsed):
    query = urllib.parse.parse_qs(parsed.query or "")
    profile = (query.get("profile") or [""])[0].strip()
    if not profile:
        return _routes_binding("j")(
            handler,
            {"error": "Missing profile", "code": "missing_profile"},
            status=400,
        )

    from api.profiles import _PROFILE_ID_RE, get_hermes_home_for_profile
    from api.user_provider import (
        UserProviderAuthError,
        current_user_id_from_handler,
        verify_user_profile_access,
    )

    if not _PROFILE_ID_RE.fullmatch(profile):
        return _routes_binding("j")(
            handler,
            {"error": "Invalid profile", "code": "invalid_profile"},
            status=400,
        )

    try:
        user_id = current_user_id_from_handler(handler)
        verify_user_profile_access(user_id, profile)
    except UserProviderAuthError as exc:
        return _routes_binding("j")(
            handler,
            {"error": str(exc), "code": exc.code},
            status=exc.status,
        )

    try:
        profile_home = Path(get_hermes_home_for_profile(profile)).expanduser()
        skills_dir = profile_home / "skills"
        skills = []
        if skills_dir.is_dir():
            for skill_dir in sorted(
                skills_dir.iterdir(),
                key=lambda item: item.name.lower(),
            ):
                skill = _read_profile_installed_skill(skill_dir, skills_dir)
                if skill:
                    skills.append(skill)
    except OSError as exc:
        logger.exception("Failed to list profile installed skills")
        return _routes_binding("bad")(
            handler,
            _routes_binding("_sanitize_error")(exc),
            500,
        )

    return _routes_binding("j")(
        handler,
        {
            "profile": profile,
            "skills_path": str(skills_dir),
            "skills": skills,
            "count": len(skills),
        },
    )


def _handle_user_skills_list(handler, parsed=None):
    try:
        user_id = _get_current_user_id(handler)
        my_skills_dir = _user_my_skills_dir(user_id)
        skills = []
        if my_skills_dir.is_dir():
            for skill_dir in sorted(
                my_skills_dir.iterdir(),
                key=lambda item: item.name.lower(),
            ):
                skill = _read_user_skill(skill_dir, my_skills_dir)
                if skill:
                    skills.append(skill)
    except _UserSkillError as exc:
        return _user_skill_error_response(handler, exc)
    except OSError as exc:
        logger.exception("Failed to list user skills")
        return _routes_binding("bad")(
            handler,
            _routes_binding("_sanitize_error")(exc),
            500,
        )

    return _routes_binding("j")(
        handler,
        {
            "skills": skills,
            "count": len(skills),
        },
    )


def _handle_user_skill_publish_from_profile(handler, body):
    try:
        profile_name = str(body.get("profile_name") or body.get("profileName") or "").strip()
        skill_slug = _validate_user_skill_segment(body.get("skill_slug") or body.get("skillSlug"), "skill_slug")
        english_name = _validate_user_skill_english_name(
            body.get("english_name") or body.get("englishName")
        )
        name = _validate_user_skill_name(body.get("name"))
        user_id = _get_current_user_id(handler)
        profile_home = _get_owned_profile_home(handler, profile_name)
        profile_skills_dir = profile_home / "skills"
        source_dir = _safe_skill_child_dir(profile_skills_dir, skill_slug)
        my_skills_dir = _user_my_skills_dir(user_id, create=True)
        destination = _resolve_inside(my_skills_dir, english_name)

        if profile_skills_dir.exists() and profile_skills_dir.is_symlink():
            raise _UserSkillError("Profile skills directory cannot be a symlink", code="profile_skills_symlink")

        _copy_skill_tree_atomic(source_dir, destination)
        try:
            _update_skill_frontmatter_name(destination / "SKILL.md", name)
        except _UserSkillError:
            try:
                if destination.exists():
                    shutil.rmtree(destination)
            except OSError:
                pass
            raise
        skill = _read_user_skill(destination, my_skills_dir)
        if not skill:
            raise _UserSkillError(
                "Published skill is unreadable",
                status=500,
                code="published_skill_unreadable",
            )
    except _UserSkillError as exc:
        return _user_skill_error_response(handler, exc)

    return _routes_binding("j")(
        handler,
        {
            "ok": True,
            "skill": skill,
        },
    )


def _handle_user_skill_install_to_profile(handler, body):
    try:
        profile_name = str(body.get("profile_name") or body.get("profileName") or "").strip()
        skill_slug = _validate_user_skill_english_name(
            body.get("skill_slug") or body.get("skillSlug")
        )
        user_id = _get_current_user_id(handler)
        my_skills_dir = _user_my_skills_dir(user_id)
        source_dir = _safe_skill_child_dir(my_skills_dir, skill_slug)
        profile_home = _get_owned_profile_home(handler, profile_name)
        target_skills_dir = profile_home / "skills"
        destination = _resolve_inside(target_skills_dir, skill_slug)

        if target_skills_dir.exists() and target_skills_dir.is_symlink():
            raise _UserSkillError("Profile skills directory cannot be a symlink", code="profile_skills_symlink")

        _copy_skill_tree_atomic(source_dir, destination)
        skill = _read_profile_installed_skill(destination, target_skills_dir)
        if not skill:
            raise _UserSkillError(
                "Installed skill is unreadable",
                status=500,
                code="installed_skill_unreadable",
            )
    except _UserSkillError as exc:
        return _user_skill_error_response(handler, exc)

    return _routes_binding("j")(
        handler,
        {
            "ok": True,
            "profile": profile_name,
            "skill": skill,
        },
    )


def _handle_user_skill_update(handler, body):
    try:
        skill_slug = _validate_user_skill_english_name(
            body.get("skill_slug") or body.get("skillSlug")
        )
        english_name = _validate_user_skill_english_name(
            body.get("english_name") or body.get("englishName")
        )
        name = _validate_user_skill_name(body.get("name"))
        user_id = _get_current_user_id(handler)
        my_skills_dir = _user_my_skills_dir(user_id)
        source_dir = _safe_skill_child_dir(my_skills_dir, skill_slug)
        destination = _resolve_inside(my_skills_dir, english_name)

        if not source_dir.is_dir() or not (source_dir / "SKILL.md").is_file():
            raise _UserSkillError("Skill not found", status=404, code="skill_not_found")
        _assert_no_symlink_tree(source_dir)

        if source_dir == destination:
            _update_skill_frontmatter_name(source_dir / "SKILL.md", name)
            updated_dir = source_dir
        else:
            with _skill_destination_lock(my_skills_dir, english_name):
                if destination.exists():
                    raise _UserSkillError("Skill already exists", status=409, code="skill_conflict")
                try:
                    source_dir.rename(destination)
                except OSError as exc:
                    raise _UserSkillError(
                        "Failed to rename skill",
                        status=500,
                        code="skill_rename_failed",
                    ) from exc
                try:
                    _update_skill_frontmatter_name(destination / "SKILL.md", name)
                except _UserSkillError:
                    try:
                        if destination.exists() and not source_dir.exists():
                            destination.rename(source_dir)
                    except OSError:
                        pass
                    raise
                updated_dir = destination

        skill = _read_user_skill(updated_dir, my_skills_dir)
        if not skill:
            raise _UserSkillError(
                "Updated skill is unreadable",
                status=500,
                code="updated_skill_unreadable",
            )
    except _UserSkillError as exc:
        return _user_skill_error_response(handler, exc)
    except OSError as exc:
        logger.exception("Failed to update user skill")
        return _routes_binding("bad")(
            handler,
            _routes_binding("_sanitize_error")(exc),
            500,
        )

    return _routes_binding("j")(
        handler,
        {
            "ok": True,
            "skill": skill,
        },
    )


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

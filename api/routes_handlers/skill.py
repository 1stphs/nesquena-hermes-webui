"""Skill endpoint handlers re-exported by api.routes."""

import io
import json
import logging
import os
import re
import shutil
import tarfile
import urllib.parse
import urllib.error
import urllib.request
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

from api.config import MAX_UPLOAD_BYTES
from api.routes_handlers._base import _routes_binding
from api.upload import parse_multipart


logger = logging.getLogger(__name__)


_PROFILE_INSTALLED_SKILL_EXCERPT_CHARS = 4000
_PROFILE_INSTALLED_SKILL_TEXT_LIMIT = 280
USER_SKILLS_ROOT = Path("/home/hermeswebui/.hermes/webui-mvp/users")
_USER_MY_SKILLS_DIR_NAME = "my-skills"
_USER_SKILLS_COLLECTION_NAME = "hermes_user_skills"
_USER_SKILL_NAME_MAX = 64
_USER_SKILL_SLUG_MAX = 150
_USER_SKILL_ENGLISH_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_USER_SKILL_STATUS_DRAFT = "draft"
_USER_SKILL_STATUS_AVAILABILITY_TESTED = "availability_tested"
_USER_SKILL_STATUS_SECURITY_TESTED = "security_tested"
_USER_SKILL_STATUS_FULLY_TESTED = "fully_tested"
_USER_SKILL_STATUS_LEGACY_ACTIVE = "active"
_USER_SKILL_STATUS_VALUES = {
    _USER_SKILL_STATUS_DRAFT,
    _USER_SKILL_STATUS_AVAILABILITY_TESTED,
    _USER_SKILL_STATUS_SECURITY_TESTED,
    _USER_SKILL_STATUS_FULLY_TESTED,
}
_USER_SKILL_INSTALLABLE_STATUSES = {
    _USER_SKILL_STATUS_AVAILABILITY_TESTED,
    _USER_SKILL_STATUS_SECURITY_TESTED,
    _USER_SKILL_STATUS_FULLY_TESTED,
}
_USER_SKILL_EDIT_MAX_BYTES = 2 * 1024 * 1024
_USER_IMPORT_MAX_EXTRACTED_BYTES = 200 * 1024 * 1024
_USER_IMPORT_ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
)


class _UserSkillError(ValueError):
    def __init__(self, message: str, *, status: int = 400, code: str = "invalid_user_skill_request"):
        super().__init__(message)
        self.status = status
        self.code = code


class _NocobaseSkillError(_UserSkillError):
    pass


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


def _normalize_user_skill_status(value) -> str:
    status = str(value or "").strip()
    if status == _USER_SKILL_STATUS_LEGACY_ACTIVE:
        return _USER_SKILL_STATUS_FULLY_TESTED
    if status in _USER_SKILL_STATUS_VALUES:
        return status
    return _USER_SKILL_STATUS_DRAFT


def _validate_user_skill_status(value) -> str:
    status = str(value or "").strip()
    if status not in _USER_SKILL_STATUS_VALUES:
        raise _UserSkillError("Invalid user skill status", code="invalid_status")
    return status


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


def _storage_path_for_user_skill(user_id: str, skill_slug: str) -> str:
    user_segment = _validate_user_skill_segment(user_id, "user_id", max_length=128)
    slug = _validate_user_skill_english_name(skill_slug)
    return f"{user_segment}/{_USER_MY_SKILLS_DIR_NAME}/{slug}"


def _normalize_nocobase_api_base_url() -> str:
    raw_api_base_url = os.getenv("NOCOBASE_API_BASE_URL", "").strip()
    if raw_api_base_url:
        return raw_api_base_url.rstrip("/")

    raw_base_url = (
        os.getenv("HERMES_USER_PROVIDER_NOCOBASE_BASE_URL")
        or os.getenv("FOXUAI_NOCOBASE_BASE_URL")
        or os.getenv("NOCOBASE_BASE_URL")
        or "https://www.foxuai.com"
    ).strip()
    if not raw_base_url:
        raise _NocobaseSkillError(
            "NoCoBase API base URL is not configured",
            status=500,
            code="nocobase_not_configured",
        )
    base_url = raw_base_url.rstrip("/")
    if base_url.endswith("/api"):
        return base_url
    return f"{base_url}/api"


def _nocobase_authorization_header() -> str:
    raw_authorization = (
        os.getenv("NOCOBASE_AUTHORIZATION")
        or os.getenv("HERMES_USER_PROVIDER_NOCOBASE_AUTHORIZATION")
        or os.getenv("FOXUAI_NOCOBASE_AUTHORIZATION")
        or ""
    ).strip()
    if not raw_authorization:
        raise _NocobaseSkillError(
            "NoCoBase authorization is not configured",
            status=500,
            code="nocobase_not_configured",
        )
    if raw_authorization.lower().startswith("bearer "):
        return raw_authorization
    return f"Bearer {raw_authorization}"


def _nocobase_headers(*, has_body: bool = False) -> dict:
    headers = {
        "Accept": "application/json",
        "Authorization": _nocobase_authorization_header(),
        "X-Hostname": os.getenv("NOCOBASE_HOSTNAME", "www.foxuai.com").strip() or "www.foxuai.com",
        "X-Authenticator": os.getenv("NOCOBASE_AUTHENTICATOR", "basic").strip() or "basic",
    }
    if has_body:
        headers["Content-Type"] = "application/json"
    return headers


def _nocobase_request(path: str, *, method: str = "GET", body: dict | None = None) -> dict:
    normalized_path = "/" + str(path or "").lstrip("/")
    url = f"{_normalize_nocobase_api_base_url()}{normalized_path}"
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=_nocobase_headers(has_body=body is not None),
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # nosec B310
            raw_text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            error_body = exc.read().decode("utf-8")
            error_payload = json.loads(error_body) if error_body else {}
        except Exception:
            error_payload = {}
        message = (
            error_payload.get("message")
            or "; ".join(str(item.get("message")) for item in error_payload.get("errors", []) if isinstance(item, dict))
            or f"NoCoBase request failed with status {exc.code}"
        )
        raise _NocobaseSkillError(
            message,
            status=502,
            code="nocobase_request_failed",
        ) from exc
    except (OSError, TimeoutError) as exc:
        raise _NocobaseSkillError(
            "NoCoBase request failed",
            status=502,
            code="nocobase_request_failed",
        ) from exc

    if not raw_text:
        return {}
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise _NocobaseSkillError(
            "NoCoBase returned invalid JSON",
            status=502,
            code="nocobase_invalid_response",
        ) from exc
    if isinstance(payload, dict) and payload.get("errors"):
        errors = payload.get("errors") or []
        message = "; ".join(str(item.get("message")) for item in errors if isinstance(item, dict))
        raise _NocobaseSkillError(
            message or "NoCoBase request failed",
            status=502,
            code="nocobase_request_failed",
        )
    return payload if isinstance(payload, dict) else {"data": payload}


def _nocobase_user_skill_record_to_skill(record: dict) -> dict | None:
    if not isinstance(record, dict):
        return None
    skill_slug = _clean_profile_installed_skill_text(
        record.get("skill_slug") or record.get("skillSlug") or record.get("englishName")
    )
    if not skill_slug:
        return None
    name = _clean_profile_installed_skill_text(record.get("name")) or skill_slug
    description = _clean_profile_installed_skill_text(record.get("description"))
    source = _clean_profile_installed_skill_text(record.get("source")) or "user"
    return {
        "recordId": str(record.get("id") or ""),
        "id": skill_slug,
        "englishName": skill_slug,
        "title": skill_slug,
        "name": name,
        "title_cn": name,
        "summary": description,
        "description": description,
        "path": skill_slug,
        "skill_file": f"{skill_slug}/SKILL.md",
        "source": source,
        "sourceFilename": _clean_profile_installed_skill_text(
            record.get("source_filename") or record.get("sourceFilename")
        ),
        "sourceType": _clean_profile_installed_skill_text(
            record.get("source_type") or record.get("sourceType")
        ),
        "sourceProfileName": _clean_profile_installed_skill_text(
            record.get("source_profile_name") or record.get("sourceProfileName")
        ),
        "sourceSkillSlug": _clean_profile_installed_skill_text(
            record.get("source_skill_slug") or record.get("sourceSkillSlug")
        ),
        "storagePath": _clean_profile_installed_skill_text(
            record.get("storage_path") or record.get("storagePath")
        ),
        "skillFilePath": _clean_profile_installed_skill_text(
            record.get("skill_file_path") or record.get("skillFilePath") or "SKILL.md"
        ),
        "fileCount": int(record.get("file_count") or record.get("fileCount") or 0),
        "sizeBytes": int(record.get("size_bytes") or record.get("sizeBytes") or 0),
        "status": _normalize_user_skill_status(record.get("status")),
        "createdAt": record.get("createdAt") or record.get("created_at") or "",
        "updatedAt": record.get("updatedAt") or record.get("updated_at") or "",
        "raw": record,
    }


def _nocobase_list_user_skill_records(user_id: str, *, skill_slug: str = "") -> list[dict]:
    params = [
        ("paginate", "false"),
        ("filter[user_id]", user_id),
        ("sort", "-createdAt"),
    ]
    normalized_skill_slug = str(skill_slug or "").strip()
    if normalized_skill_slug:
        params.append(("filter[skill_slug]", normalized_skill_slug))
    query = urllib.parse.urlencode(params)
    payload = _nocobase_request(f"/{_USER_SKILLS_COLLECTION_NAME}:list?{query}")
    records = payload.get("data")
    return records if isinstance(records, list) else []


def _nocobase_get_user_skill_record(user_id: str, skill_slug: str) -> dict | None:
    records = _nocobase_list_user_skill_records(user_id, skill_slug=skill_slug)
    return records[0] if records else None


def _nocobase_create_user_skill_record(record: dict) -> dict:
    user_id = str(record.get("user_id") or "").strip()
    skill_slug = str(record.get("skill_slug") or "").strip()
    if _nocobase_get_user_skill_record(user_id, skill_slug):
        raise _NocobaseSkillError("Skill already exists", status=409, code="skill_conflict")
    payload = _nocobase_request(
        f"/{_USER_SKILLS_COLLECTION_NAME}:create",
        method="POST",
        body=record,
    )
    data = payload.get("data")
    if isinstance(data, list):
        created = data[0] if data else {}
    elif isinstance(data, dict):
        created = data
    else:
        created = payload
    return created if isinstance(created, dict) and created else record


def _nocobase_update_user_skill_record(user_id: str, original_skill_slug: str, patch: dict) -> dict:
    record = _nocobase_get_user_skill_record(user_id, original_skill_slug)
    if not record:
        raise _NocobaseSkillError("Skill record not found", status=404, code="skill_record_not_found")
    record_id = str(record.get("id") or "").strip()
    if not record_id:
        raise _NocobaseSkillError("Skill record missing id", status=502, code="skill_record_invalid")
    params = urllib.parse.urlencode([
        ("filterByTk", record_id),
        ("filter[user_id]", user_id),
    ])
    payload = _nocobase_request(
        f"/{_USER_SKILLS_COLLECTION_NAME}:update?{params}",
        method="POST",
        body=patch,
    )
    data = payload.get("data")
    if isinstance(data, list):
        updated = data[0] if data else {}
    elif isinstance(data, dict):
        updated = data
    else:
        updated = payload
    return updated if isinstance(updated, dict) and updated else {**record, **patch}


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


def _get_upload_source_type(filename: str) -> str:
    lower_name = str(filename or "").lower()

    if lower_name.endswith(".md"):
        return "markdown"

    if lower_name.endswith(_USER_IMPORT_ARCHIVE_SUFFIXES):
        return "archive"

    raise _UserSkillError(
        "仅支持上传 .md 或 zip/tar 压缩包",
        code="unsupported_skill_upload_type",
    )


def _validate_import_skill_metadata(content: str) -> tuple[dict, str]:
    try:
        metadata, _body = _split_skill_frontmatter(content)
    except ValueError as exc:
        raise _UserSkillError("Skill frontmatter 格式无效", code="invalid_skill_frontmatter") from exc

    name = _clean_profile_installed_skill_text(metadata.get("name"))
    description = _clean_profile_installed_skill_text(metadata.get("description"))

    if not name:
        raise _UserSkillError("SKILL.md 缺少 name", code="missing_skill_name")

    if not description:
        raise _UserSkillError("SKILL.md 缺少 description", code="missing_skill_description")

    return metadata, description


def _read_import_skill_file(skill_file: Path) -> tuple[dict, str, int]:
    if not skill_file.is_file() or skill_file.is_symlink():
        raise _UserSkillError("SKILL.md 不存在或不可读取", code="skill_file_not_found")

    try:
        content = skill_file.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise _UserSkillError("SKILL.md 必须是 UTF-8 文本", code="invalid_skill_encoding") from exc
    except OSError as exc:
        raise _UserSkillError(
            "读取 SKILL.md 失败",
            status=500,
            code="skill_file_read_failed",
        ) from exc

    metadata, description = _validate_import_skill_metadata(content)
    return metadata, description, len(content.encode("utf-8"))


def _write_markdown_import(temp_dir: Path, file_bytes: bytes) -> tuple[dict, str, int]:
    try:
        content = file_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise _UserSkillError("Skill markdown 必须是 UTF-8 文本", code="invalid_skill_encoding") from exc

    metadata, description = _validate_import_skill_metadata(content)
    skill_file = temp_dir / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8")
    return metadata, description, len(file_bytes)


def _assert_archive_member_path(root: Path, member_name: str) -> Path:
    if "\x00" in member_name:
        raise _UserSkillError("压缩包包含非法文件路径", code="invalid_archive_path")

    target_path = (root / member_name).resolve(strict=False)
    try:
        target_path.relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise _UserSkillError("压缩包包含路径穿越文件", code="archive_path_traversal") from exc
    return target_path


def _extract_zip_import(file_bytes: bytes, temp_dir: Path) -> None:
    total_extracted = 0

    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue

                target_path = _assert_archive_member_path(temp_dir, member.filename)
                if member.external_attr >> 16 & 0o170000 == 0o120000:
                    raise _UserSkillError("压缩包不能包含符号链接", code="archive_symlink")

                target_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target_path.open("wb") as destination:
                    while True:
                        chunk = source.read(65536)
                        if not chunk:
                            break
                        total_extracted += len(chunk)
                        if total_extracted > _USER_IMPORT_MAX_EXTRACTED_BYTES:
                            raise _UserSkillError("压缩包解压后过大", code="archive_too_large")
                        destination.write(chunk)
    except zipfile.BadZipFile as exc:
        raise _UserSkillError("压缩包格式无效", code="invalid_archive") from exc


def _extract_tar_import(file_bytes: bytes, temp_dir: Path) -> None:
    total_extracted = 0

    try:
        with tarfile.open(fileobj=io.BytesIO(file_bytes)) as archive:
            for member in archive.getmembers():
                if member.issym() or member.islnk():
                    raise _UserSkillError("压缩包不能包含链接文件", code="archive_link")

                if not member.isfile():
                    continue

                target_path = _assert_archive_member_path(temp_dir, member.name)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    continue

                with source, target_path.open("wb") as destination:
                    while True:
                        chunk = source.read(65536)
                        if not chunk:
                            break
                        total_extracted += len(chunk)
                        if total_extracted > _USER_IMPORT_MAX_EXTRACTED_BYTES:
                            raise _UserSkillError("压缩包解压后过大", code="archive_too_large")
                        destination.write(chunk)
    except tarfile.TarError as exc:
        raise _UserSkillError("压缩包格式无效", code="invalid_archive") from exc


def _find_archive_skill_file(extract_dir: Path) -> Path:
    skill_files = [
        path
        for path in extract_dir.rglob("*")
        if path.is_file() and not path.is_symlink() and path.name.lower() == "skill.md"
    ]

    if not skill_files:
        raise _UserSkillError("压缩包内缺少 SKILL.md", code="missing_skill_file")

    if len(skill_files) > 1:
        raise _UserSkillError("压缩包内包含多个 SKILL.md", code="multiple_skill_files")

    return skill_files[0]


def _write_archive_import(temp_dir: Path, file_bytes: bytes, filename: str) -> tuple[dict, str, int]:
    extract_dir = temp_dir / ".extract"
    extract_dir.mkdir(parents=True, exist_ok=True)
    lower_name = filename.lower()

    if lower_name.endswith(".zip"):
        _extract_zip_import(file_bytes, extract_dir)
    else:
        _extract_tar_import(file_bytes, extract_dir)

    source_skill_file = _find_archive_skill_file(extract_dir)
    source_root = source_skill_file.parent
    _assert_no_symlink_tree(source_root)
    metadata, description, skill_file_bytes = _read_import_skill_file(source_skill_file)

    for item in source_root.iterdir():
        shutil.move(str(item), str(temp_dir / item.name))

    shutil.rmtree(extract_dir, ignore_errors=True)
    imported_skill_file = temp_dir / source_skill_file.name
    if imported_skill_file.name != "SKILL.md":
        imported_skill_file.rename(temp_dir / "SKILL.md")
    return metadata, description, skill_file_bytes


def _collect_import_tree_stats(root: Path) -> tuple[int, int]:
    file_count = 0
    size_bytes = 0

    for item in root.rglob("*"):
        if item.is_symlink():
            raise _UserSkillError("Skill 不能包含符号链接", code="skill_source_symlink")
        if item.is_file():
            file_count += 1
            size_bytes += item.stat().st_size

    return file_count, size_bytes


def _normalize_user_skill_file_path(value) -> str:
    relative_path = str(value or "").strip().replace("\\", "/")
    if not relative_path:
        raise _UserSkillError("path is required", code="missing_path")
    if (
        relative_path.startswith("/")
        or relative_path in (".", "..")
        or "/../" in f"/{relative_path}/"
        or relative_path.startswith("../")
        or relative_path.endswith("/..")
        or "\x00" in relative_path
    ):
        raise _UserSkillError("Invalid file path", code="invalid_file_path")
    normalized_parts = []
    for part in relative_path.split("/"):
        if not part or part in (".", ".."):
            raise _UserSkillError("Invalid file path", code="invalid_file_path")
        normalized_parts.append(part)
    return "/".join(normalized_parts)


def _get_owned_user_skill_dir(user_id: str, skill_slug: str) -> tuple[Path, Path, dict]:
    slug = _validate_user_skill_english_name(skill_slug)
    record = _nocobase_get_user_skill_record(user_id, slug)
    if not record:
        raise _UserSkillError("Skill record not found", status=404, code="skill_record_not_found")

    my_skills_dir = _user_my_skills_dir(user_id)
    if my_skills_dir.exists() and my_skills_dir.is_symlink():
        raise _UserSkillError("My skills directory cannot be a symlink", code="my_skills_dir_symlink")

    skill_dir = _safe_skill_child_dir(my_skills_dir, slug)
    if not skill_dir.is_dir() or skill_dir.is_symlink():
        raise _UserSkillError("Skill not found", status=404, code="skill_not_found")
    _assert_no_symlink_tree(skill_dir)
    return my_skills_dir, skill_dir, record


def _resolve_user_skill_file(skill_dir: Path, relative_path: str) -> Path:
    normalized_path = _normalize_user_skill_file_path(relative_path)
    target = _resolve_inside(skill_dir, *normalized_path.split("/"))
    if not target.exists():
        raise _UserSkillError("File not found", status=404, code="skill_file_not_found")
    if target.is_symlink():
        raise _UserSkillError("Skill file cannot be a symlink", code="skill_file_symlink")
    if not target.is_file():
        raise _UserSkillError("Path must point to a file", code="skill_file_not_file")
    return target


def _relative_skill_path(skill_dir: Path, item: Path) -> str:
    return item.relative_to(skill_dir).as_posix()


def _build_user_skill_file_tree(skill_dir: Path) -> tuple[list[dict], list[dict], str]:
    files: list[dict] = []

    def build_node(path: Path) -> dict:
        children = []
        for child in sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower(), item.name)):
            if child.is_symlink():
                raise _UserSkillError("Skill source cannot contain symlinks", code="skill_source_symlink")
            if child.is_dir():
                children.append(build_node(child))
                continue
            if child.is_file():
                try:
                    size_bytes = child.stat().st_size
                except OSError as exc:
                    raise _UserSkillError(
                        "Failed to inspect skill file",
                        status=500,
                        code="skill_file_inspect_failed",
                    ) from exc
                relative_path = _relative_skill_path(skill_dir, child)
                file_node = {
                    "type": "file",
                    "name": child.name,
                    "path": relative_path,
                    "sizeBytes": size_bytes,
                    "editable": size_bytes <= _USER_SKILL_EDIT_MAX_BYTES,
                }
                files.append(file_node)
                children.append(file_node)
        return {
            "type": "directory",
            "name": path.name,
            "path": "" if path == skill_dir else _relative_skill_path(skill_dir, path),
            "children": children,
        }

    root_node = build_node(skill_dir)
    files.sort(key=lambda file: str(file.get("path") or "").lower())
    selected_path = ""
    editable_files = [file for file in files if file.get("editable")]
    skill_file = next((file for file in editable_files if file.get("path") == "SKILL.md"), None)
    if skill_file:
        selected_path = "SKILL.md"
    elif editable_files:
        selected_path = str(editable_files[0].get("path") or "")
    return root_node["children"], files, selected_path


def _read_user_skill_text_file(target: Path) -> str:
    try:
        size_bytes = target.stat().st_size
    except OSError as exc:
        raise _UserSkillError(
            "Failed to inspect skill file",
            status=500,
            code="skill_file_inspect_failed",
        ) from exc
    if size_bytes > _USER_SKILL_EDIT_MAX_BYTES:
        raise _UserSkillError(
            "该文件过大，暂不支持在线编辑",
            status=413,
            code="skill_file_too_large",
        )
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise _UserSkillError(
            "该文件类型暂不支持编辑",
            code="unsupported_skill_file_text",
        ) from exc
    except OSError as exc:
        raise _UserSkillError(
            "读取 Skill 文件失败",
            status=500,
            code="skill_file_read_failed",
        ) from exc


def _validate_user_skill_text_content(content) -> str:
    text = str(content if content is not None else "")
    if len(text.encode("utf-8")) > _USER_SKILL_EDIT_MAX_BYTES:
        raise _UserSkillError(
            "该文件过大，暂不支持在线编辑",
            status=413,
            code="skill_file_too_large",
        )
    return text


def _build_user_skill_file_update_patch(
    *,
    user_id: str,
    skill_slug: str,
    skill_dir: Path,
    relative_path: str,
    existing_record: dict,
) -> dict:
    file_count, size_bytes = _collect_import_tree_stats(skill_dir)
    patch = {
        "status": _USER_SKILL_STATUS_DRAFT,
        "file_count": file_count,
        "size_bytes": size_bytes,
    }

    if relative_path == "SKILL.md":
        metadata, description = _read_user_skill_record_metadata(skill_dir)
        patch.update(
            {
                "name": _clean_profile_installed_skill_text(metadata.get("name")) or skill_slug,
                "description": description,
                "skill_file_path": "SKILL.md",
            }
        )
    else:
        existing_skill_file_path = _clean_profile_installed_skill_text(
            existing_record.get("skill_file_path") or existing_record.get("skillFilePath") or "SKILL.md"
        )
        patch["skill_file_path"] = existing_skill_file_path or "SKILL.md"

    patch["storage_path"] = _storage_path_for_user_skill(user_id, skill_slug)
    return patch


def _validate_user_skill_storage_path(user_id: str, storage_path: str, skill_slug: str = "") -> str:
    normalized_path = str(storage_path or "").strip().replace("\\", "/")
    user_segment = _validate_user_skill_segment(user_id, "user_id", max_length=128)
    prefix = f"{user_segment}/{_USER_MY_SKILLS_DIR_NAME}/"

    if not normalized_path.startswith(prefix):
        raise _UserSkillError("Invalid user skill storage path", code="invalid_storage_path")

    slug = normalized_path.removeprefix(prefix).strip("/")
    if "/" in slug:
        raise _UserSkillError("Invalid user skill storage path", code="invalid_storage_path")

    if skill_slug and slug != skill_slug:
        raise _UserSkillError("Storage path does not match skill", code="storage_path_mismatch")

    return _validate_user_skill_english_name(slug)


def _user_skill_record_body(
    *,
    user_id: str,
    skill_slug: str,
    destination: Path,
    source: str,
    source_filename: str = "",
    source_type: str,
    source_profile_name: str = "",
    source_skill_slug: str = "",
    metadata: dict,
    description: str,
) -> dict:
    file_count, size_bytes = _collect_import_tree_stats(destination)
    name = _clean_profile_installed_skill_text(metadata.get("name")) or skill_slug

    return {
        "user_id": user_id,
        "skill_slug": skill_slug,
        "name": name,
        "description": description,
        "source": source,
        "source_filename": source_filename,
        "source_type": source_type,
        "source_profile_name": source_profile_name,
        "source_skill_slug": source_skill_slug,
        "storage_path": _storage_path_for_user_skill(user_id, skill_slug),
        "skill_file_path": "SKILL.md",
        "file_count": file_count,
        "size_bytes": size_bytes,
        "status": _USER_SKILL_STATUS_DRAFT,
    }


def _user_skill_response_payload(record: dict) -> dict:
    skill = _nocobase_user_skill_record_to_skill(record)
    if not skill:
        raise _UserSkillError(
            "User skill record is unreadable",
            status=500,
            code="user_skill_record_unreadable",
        )
    return {
        "ok": True,
        "skill": skill,
        "skillSlug": skill["englishName"],
        "storagePath": skill.get("storagePath", ""),
        "skillFilePath": skill.get("skillFilePath", "SKILL.md"),
        "fileCount": skill.get("fileCount", 0),
        "sizeBytes": skill.get("sizeBytes", 0),
    }


def _read_user_skill_record_metadata(skill_dir: Path) -> tuple[dict, str]:
    metadata, description, _skill_file_bytes = _read_import_skill_file(skill_dir / "SKILL.md")
    return metadata, description


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
        records = _nocobase_list_user_skill_records(user_id)
        skills = [
            skill
            for skill in (_nocobase_user_skill_record_to_skill(record) for record in records)
            if skill
        ]
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


def _handle_user_skill_files_list(handler, parsed):
    try:
        query = urllib.parse.parse_qs(parsed.query or "")
        skill_slug = _validate_user_skill_english_name(
            (query.get("skill_slug") or query.get("skillSlug") or [""])[0]
        )
        user_id = _get_current_user_id(handler)
        my_skills_dir, skill_dir, record = _get_owned_user_skill_dir(user_id, skill_slug)
        tree, files, selected_path = _build_user_skill_file_tree(skill_dir)
        skill = _nocobase_user_skill_record_to_skill(record)
        if not skill:
            raise _UserSkillError(
                "User skill record is unreadable",
                status=500,
                code="user_skill_record_unreadable",
            )
    except _UserSkillError as exc:
        return _user_skill_error_response(handler, exc)
    except OSError as exc:
        logger.exception("Failed to list user skill files")
        return _routes_binding("bad")(
            handler,
            _routes_binding("_sanitize_error")(exc),
            500,
        )

    return _routes_binding("j")(
        handler,
        {
            "ok": True,
            "skillSlug": skill_slug,
            "skillRoot": str(skill_dir.relative_to(my_skills_dir)),
            "tree": tree,
            "files": files,
            "selectedPath": selected_path,
            "skill": skill,
        },
    )


def _handle_user_skill_file_read(handler, parsed):
    try:
        query = urllib.parse.parse_qs(parsed.query or "")
        skill_slug = _validate_user_skill_english_name(
            (query.get("skill_slug") or query.get("skillSlug") or [""])[0]
        )
        relative_path = _normalize_user_skill_file_path((query.get("path") or [""])[0])
        user_id = _get_current_user_id(handler)
        _my_skills_dir, skill_dir, record = _get_owned_user_skill_dir(user_id, skill_slug)
        target = _resolve_user_skill_file(skill_dir, relative_path)
        content = _read_user_skill_text_file(target)
        skill = _nocobase_user_skill_record_to_skill(record)
        if not skill:
            raise _UserSkillError(
                "User skill record is unreadable",
                status=500,
                code="user_skill_record_unreadable",
            )
    except _UserSkillError as exc:
        return _user_skill_error_response(handler, exc)

    return _routes_binding("j")(
        handler,
        {
            "ok": True,
            "skillSlug": skill_slug,
            "path": relative_path,
            "content": content,
            "sizeBytes": target.stat().st_size,
            "skill": skill,
        },
    )


def _handle_user_skill_import(handler):
    destination = None
    temp_dir = None

    try:
        content_type = handler.headers.get("Content-Type", "")
        content_length = int(handler.headers.get("Content-Length", 0) or 0)
        if content_length > MAX_UPLOAD_BYTES:
            raise _UserSkillError("上传文件过大", status=413, code="upload_too_large")

        fields, files = parse_multipart(handler.rfile, content_type, content_length)
        if "file" not in files:
            raise _UserSkillError("请选择要导入的 Skill 文件", code="missing_file")

        filename, file_bytes = files["file"]
        source_filename = Path(str(filename or "")).name
        if not source_filename:
            raise _UserSkillError("上传文件缺少文件名", code="missing_filename")

        source_type = _get_upload_source_type(source_filename)
        user_id = _get_current_user_id(handler)
        skill_slug = _validate_user_skill_english_name(
            fields.get("english_name") or fields.get("englishName") or fields.get("skill_slug") or fields.get("skillSlug")
        )
        my_skills_dir = _user_my_skills_dir(user_id, create=True)
        if my_skills_dir.is_symlink():
            raise _UserSkillError("My skills directory cannot be a symlink", code="my_skills_dir_symlink")
        destination = _safe_skill_child_dir(my_skills_dir, skill_slug)
        temp_dir = _safe_skill_child_dir(my_skills_dir, f".{skill_slug}.uploading")

        if destination.exists() or temp_dir.exists():
            raise _UserSkillError("Skill already exists", status=409, code="skill_conflict")

        temp_dir.mkdir(parents=True)
        if source_type == "markdown":
            metadata, description, _skill_file_bytes = _write_markdown_import(temp_dir, file_bytes)
        else:
            metadata, description, _skill_file_bytes = _write_archive_import(
                temp_dir,
                file_bytes,
                source_filename,
            )

        _assert_no_symlink_tree(temp_dir)
        if not (temp_dir / "SKILL.md").is_file():
            raise _UserSkillError("SKILL.md 不存在或不可读取", code="skill_file_not_found")

        temp_dir.rename(destination)
        temp_dir = None
        record_body = _user_skill_record_body(
            user_id=user_id,
            skill_slug=skill_slug,
            destination=destination,
            source="imported",
            source_filename=source_filename,
            source_type=source_type,
            metadata=metadata,
            description=description,
        )
        try:
            record = _nocobase_create_user_skill_record(record_body)
        except _UserSkillError:
            if destination and destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            raise
        payload = _user_skill_response_payload(record)
    except _UserSkillError as exc:
        if temp_dir and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        if destination and destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        return _user_skill_error_response(handler, exc)
    except ValueError as exc:
        if temp_dir and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        if destination and destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        return _user_skill_error_response(
            handler,
            _UserSkillError(str(exc) or "导入请求格式无效", code="invalid_import_request"),
        )
    except OSError as exc:
        logger.exception("Failed to import user skill")
        if temp_dir and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        if destination and destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        return _routes_binding("j")(
            handler,
            {
                "error": "导入 Skill 失败",
                "code": "skill_import_failed",
            },
            status=500,
        )

    return _routes_binding("j")(handler, payload)


def _handle_user_skill_import_cancel(handler, body):
    try:
        user_id = _get_current_user_id(handler)
        skill_slug = str(body.get("skill_slug") or body.get("skillSlug") or body.get("importId") or "").strip()
        storage_path = str(body.get("storage_path") or body.get("storagePath") or "").strip()

        if storage_path:
            skill_slug = _validate_user_skill_storage_path(user_id, storage_path, skill_slug)
        else:
            skill_slug = _validate_user_skill_english_name(skill_slug)

        my_skills_dir = _user_my_skills_dir(user_id)
        destination = _safe_skill_child_dir(my_skills_dir, skill_slug)

        if destination.exists():
            if not destination.is_dir() or destination.is_symlink():
                raise _UserSkillError("Invalid user skill destination", code="invalid_user_skill_destination")
            shutil.rmtree(destination)
    except _UserSkillError as exc:
        return _user_skill_error_response(handler, exc)
    except OSError as exc:
        logger.exception("Failed to cancel user skill import")
        return _routes_binding("bad")(
            handler,
            _routes_binding("_sanitize_error")(exc),
            500,
        )

    return _routes_binding("j")(
        handler,
        {
            "ok": True,
            "importId": skill_slug,
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
        metadata, description = _read_user_skill_record_metadata(destination)
        record_body = _user_skill_record_body(
            user_id=user_id,
            skill_slug=english_name,
            destination=destination,
            source="profile",
            source_type="profile",
            source_profile_name=profile_name,
            source_skill_slug=skill_slug,
            metadata=metadata,
            description=description,
        )
        try:
            record = _nocobase_create_user_skill_record(record_body)
        except _UserSkillError:
            try:
                if destination.exists():
                    shutil.rmtree(destination)
            except OSError:
                pass
            raise
        payload = _user_skill_response_payload(record)
    except _UserSkillError as exc:
        return _user_skill_error_response(handler, exc)

    return _routes_binding("j")(handler, payload)


def _handle_user_skill_install_to_profile(handler, body):
    try:
        profile_name = str(body.get("profile_name") or body.get("profileName") or "").strip()
        skill_slug = _validate_user_skill_english_name(
            body.get("skill_slug") or body.get("skillSlug")
        )
        user_id = _get_current_user_id(handler)
        my_skills_dir = _user_my_skills_dir(user_id)
        source_dir = _safe_skill_child_dir(my_skills_dir, skill_slug)
        record = _nocobase_get_user_skill_record(user_id, skill_slug)
        skill_status = _normalize_user_skill_status(record.get("status") if record else "")

        if skill_status not in _USER_SKILL_INSTALLABLE_STATUSES:
            raise _UserSkillError(
                "Skill must pass availability or security testing before installation",
                code="user_skill_not_tested",
            )

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


def _handle_user_skill_file_update(handler, body):
    target = None
    original_content = None
    try:
        skill_slug = _validate_user_skill_english_name(
            body.get("skill_slug") or body.get("skillSlug")
        )
        relative_path = _normalize_user_skill_file_path(body.get("path"))
        next_content = _validate_user_skill_text_content(body.get("content"))
        user_id = _get_current_user_id(handler)
        _my_skills_dir, skill_dir, record = _get_owned_user_skill_dir(user_id, skill_slug)
        target = _resolve_user_skill_file(skill_dir, relative_path)
        original_content = _read_user_skill_text_file(target)

        if relative_path == "SKILL.md":
            _validate_import_skill_metadata(next_content)

        temp_file = target.with_name(f".{target.name}.tmp-{uuid.uuid4().hex}")
        try:
            temp_file.write_text(next_content, encoding="utf-8")
            temp_file.replace(target)
        finally:
            try:
                if temp_file.exists():
                    temp_file.unlink()
            except OSError:
                pass

        update_patch = _build_user_skill_file_update_patch(
            user_id=user_id,
            skill_slug=skill_slug,
            skill_dir=skill_dir,
            relative_path=relative_path,
            existing_record=record,
        )
        updated_record = _nocobase_update_user_skill_record(user_id, skill_slug, update_patch)
        payload = _user_skill_response_payload(updated_record)
    except _UserSkillError as exc:
        if isinstance(exc, _NocobaseSkillError) and target and original_content is not None:
            try:
                target.write_text(original_content, encoding="utf-8")
            except OSError:
                return _user_skill_error_response(
                    handler,
                    _UserSkillError(
                        "Skill file was updated but NoCoBase sync failed and rollback failed",
                        status=500,
                        code="user_skill_file_update_partially_failed",
                    ),
                )
        return _user_skill_error_response(handler, exc)
    except OSError as exc:
        logger.exception("Failed to update user skill file")
        return _routes_binding("bad")(
            handler,
            _routes_binding("_sanitize_error")(exc),
            500,
        )

    return _routes_binding("j")(
        handler,
        {
            **payload,
            "path": relative_path,
            "skillSlug": skill_slug,
        },
    )


def _handle_user_skill_update(handler, body):
    source_dir = None
    destination = None
    original_content = None
    did_rename = False
    try:
        skill_slug = _validate_user_skill_english_name(
            body.get("skill_slug") or body.get("skillSlug")
        )
        user_id = _get_current_user_id(handler)

        if "status" in body:
            next_status = _validate_user_skill_status(body.get("status"))
            record = _nocobase_update_user_skill_record(user_id, skill_slug, {"status": next_status})
            payload = _user_skill_response_payload(record)
            return _routes_binding("j")(handler, payload)

        english_name = _validate_user_skill_english_name(
            body.get("english_name") or body.get("englishName")
        )
        name = _validate_user_skill_name(body.get("name"))
        my_skills_dir = _user_my_skills_dir(user_id)
        source_dir = _safe_skill_child_dir(my_skills_dir, skill_slug)
        destination = _resolve_inside(my_skills_dir, english_name)

        if not source_dir.is_dir() or not (source_dir / "SKILL.md").is_file():
            raise _UserSkillError("Skill not found", status=404, code="skill_not_found")
        _assert_no_symlink_tree(source_dir)
        original_content = (source_dir / "SKILL.md").read_text(encoding="utf-8")

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
                did_rename = True
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
        metadata, description = _read_user_skill_record_metadata(updated_dir)
        record_body = _user_skill_record_body(
            user_id=user_id,
            skill_slug=english_name,
            destination=updated_dir,
            source="",
            source_type="",
            metadata=metadata,
            description=description,
        )
        update_patch = {
            "skill_slug": record_body["skill_slug"],
            "name": record_body["name"],
            "description": record_body["description"],
            "storage_path": record_body["storage_path"],
            "skill_file_path": record_body["skill_file_path"],
            "file_count": record_body["file_count"],
            "size_bytes": record_body["size_bytes"],
        }
        record = _nocobase_update_user_skill_record(user_id, skill_slug, update_patch)
        payload = _user_skill_response_payload(record)
    except _UserSkillError as exc:
        if isinstance(exc, _NocobaseSkillError):
            try:
                if did_rename and destination and destination.exists() and source_dir and not source_dir.exists():
                    destination.rename(source_dir)
                if original_content is not None and source_dir:
                    (source_dir / "SKILL.md").write_text(original_content, encoding="utf-8")
            except OSError:
                return _user_skill_error_response(
                    handler,
                    _UserSkillError(
                        "Skill file was updated but NoCoBase sync failed and rollback failed",
                        status=500,
                        code="user_skill_update_partially_failed",
                    ),
                )
        return _user_skill_error_response(handler, exc)
    except OSError as exc:
        logger.exception("Failed to update user skill")
        return _routes_binding("bad")(
            handler,
            _routes_binding("_sanitize_error")(exc),
            500,
        )

    return _routes_binding("j")(handler, payload)


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

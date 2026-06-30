"""Current-user contact lookup backed by NocoBase.

The public functions in this module never accept a caller-supplied owner id from
the model. Callers must pass the authenticated WebUI user id that was resolved
server-side from request context.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

from api.user_provider import UserProviderAuthError, UserProviderLookupError


DEFAULT_NOCOBASE_BASE_URL = "https://www.foxuai.com"
NOCOBASE_TIMEOUT_SECONDS = 6.0
MAX_QUERY_LENGTH = 100
DEFAULT_LIMIT = 5
MAX_LIMIT = 20
CONTACT_RELATION_COLLECTION = "hermes_users_contacts"
HERMES_USERS_COLLECTION = "hermes_users"
CONTACT_RELATION_FIELDS = (
    "id,affiliated_user_id,contact_added_id,nickname,email,phone,company,department"
)
CONTACT_USER_FIELDS = "id,username,nickname,email,role,phone,company,department"


class UserContactRequestError(ValueError):
    """Raised when a contact lookup request is invalid."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 400,
        code: str = "invalid_contact_lookup_request",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_nocobase_api_base_url() -> str:
    raw_api_base_url = os.getenv("NOCOBASE_API_BASE_URL", "").strip()
    if raw_api_base_url:
        base_url = raw_api_base_url.rstrip("/")
        if base_url.endswith("/api"):
            return base_url
        return f"{base_url}/api"

    raw_base_url = (
        os.getenv("HERMES_USER_PROVIDER_NOCOBASE_BASE_URL")
        or os.getenv("FOXUAI_NOCOBASE_BASE_URL")
        or os.getenv("NOCOBASE_BASE_URL")
        or DEFAULT_NOCOBASE_BASE_URL
    ).strip()
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
        raise UserProviderLookupError("NoCoBase authorization is not configured")
    if raw_authorization.lower().startswith(("bearer ", "basic ")):
        return raw_authorization
    return f"Bearer {raw_authorization}"


def _redact_error(value: Any, authorization: str | None = None) -> str:
    text = str(value or "")
    if authorization:
        text = text.replace(authorization, "[REDACTED]")
    return text


def _nocobase_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": _nocobase_authorization_header(),
        "X-Hostname": os.getenv("NOCOBASE_HOSTNAME", "www.foxuai.com").strip()
        or "www.foxuai.com",
        "X-Authenticator": os.getenv("NOCOBASE_AUTHENTICATOR", "basic").strip() or "basic",
    }


def _safe_json_loads(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    return json.loads(text)


def _nocobase_list(collection: str, params: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    base_url = _normalize_nocobase_api_base_url()
    query = urllib.parse.urlencode(params, doseq=True)
    url = f"{base_url}/{collection}:list?{query}"
    headers = _nocobase_headers()
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=NOCOBASE_TIMEOUT_SECONDS) as resp:
            payload = _safe_json_loads(resp.read(2_000_000))
    except Exception as exc:
        raise UserProviderLookupError(
            _redact_error(exc, headers.get("Authorization"))
        ) from exc

    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        data = data["data"]
    if not isinstance(data, list):
        raise UserProviderLookupError("NoCoBase response data is not a list")
    return [item for item in data if isinstance(item, dict)]


def _normalize_limit(value: Any) -> int:
    if value in (None, ""):
        return DEFAULT_LIMIT
    try:
        limit = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise UserContactRequestError("limit must be a positive integer") from exc
    if limit <= 0:
        raise UserContactRequestError("limit must be a positive integer")
    return min(limit, MAX_LIMIT)


def _normalize_query(value: Any) -> str:
    query = _normalize_text(value)
    if not query:
        raise UserContactRequestError("query is required", code="missing_query")
    if len(query) > MAX_QUERY_LENGTH:
        raise UserContactRequestError(
            f"query must be at most {MAX_QUERY_LENGTH} characters",
            code="query_too_long",
        )
    return query


def _field(record: dict[str, Any], *names: str) -> str:
    for name in names:
        value = _normalize_text(record.get(name))
        if value:
            return value
    return ""


def _record_matches_query(
    relation: dict[str, Any],
    user: dict[str, Any] | None,
    query: str,
) -> bool:
    needle = query.lower()
    haystacks = [
        _field(relation, "nickname"),
        _field(relation, "email"),
        _field(relation, "phone"),
        _field(relation, "company"),
        _field(relation, "department"),
    ]
    if user:
        haystacks.extend(
            [
                _field(user, "username"),
                _field(user, "nickname"),
                _field(user, "email"),
                _field(user, "phone"),
                _field(user, "company"),
                _field(user, "department"),
            ]
        )
    return any(needle in item.lower() for item in haystacks if item)


def _user_matches_query(user: dict[str, Any], query: str) -> bool:
    needle = query.lower()
    haystacks = [
        _field(user, "username"),
        _field(user, "nickname"),
        _field(user, "email"),
        _field(user, "phone"),
        _field(user, "company"),
        _field(user, "department"),
        _field(user, "role"),
    ]
    return any(needle in item.lower() for item in haystacks if item)


def _normalize_contact(
    relation: dict[str, Any],
    user: dict[str, Any] | None,
) -> dict[str, str]:
    user = user or {}
    name = _field(relation, "nickname") or _field(user, "nickname", "username")
    email = _field(relation, "email") or _field(user, "email")
    return {
        "name": name,
        "email": email,
        "phone": _field(relation, "phone") or _field(user, "phone"),
        "company": _field(relation, "company") or _field(user, "company"),
        "department": _field(relation, "department") or _field(user, "department"),
        "source": "personal_contact",
    }


def _normalize_company_contact(user: dict[str, Any]) -> dict[str, str]:
    name = _field(user, "nickname", "username")
    email = _field(user, "email")
    return {
        "name": name,
        "email": email,
        "phone": _field(user, "phone"),
        "company": _field(user, "company"),
        "department": _field(user, "department"),
        "source": "company_contact",
    }


def search_current_user_contacts(
    user_id: str,
    *,
    query: Any,
    limit: Any = DEFAULT_LIMIT,
) -> dict[str, Any]:
    normalized_user_id = _normalize_text(user_id)
    if not normalized_user_id:
        raise UserProviderAuthError(
            "Missing user context",
            status=400,
            code="missing_user_context",
        )
    normalized_query = _normalize_query(query)
    normalized_limit = _normalize_limit(limit)

    relations = _nocobase_list(
        CONTACT_RELATION_COLLECTION,
        [
            ("paginate", "false"),
            ("fields", CONTACT_RELATION_FIELDS),
            ("filter[affiliated_user_id]", normalized_user_id),
            ("sort", "-createdAt"),
        ],
    )
    contact_user_ids = []
    for relation in relations:
        contact_user_id = _field(relation, "contact_added_id")
        if contact_user_id and contact_user_id not in contact_user_ids:
            contact_user_ids.append(contact_user_id)

    users_by_id: dict[str, dict[str, Any]] = {}
    if contact_user_ids:
        params: list[tuple[str, Any]] = [
            ("paginate", "false"),
            ("fields", CONTACT_USER_FIELDS),
        ]
        params.extend(("filter[id][$in][]", user_id) for user_id in contact_user_ids)
        for user in _nocobase_list(HERMES_USERS_COLLECTION, params):
            user_id_value = _field(user, "id")
            if user_id_value:
                users_by_id[user_id_value] = user

    company_users = _nocobase_list(
        HERMES_USERS_COLLECTION,
        [
            ("paginate", "false"),
            ("fields", CONTACT_USER_FIELDS),
            ("sort", "nickname"),
        ],
    )

    contacts = []
    included_user_ids = set()
    for relation in relations:
        contact_user_id = _field(relation, "contact_added_id")
        user = users_by_id.get(contact_user_id)
        if not _record_matches_query(relation, user, normalized_query):
            continue
        contact = _normalize_contact(relation, user)
        if contact.get("name") or contact.get("email"):
            contacts.append(contact)
            if contact_user_id:
                included_user_ids.add(contact_user_id)
        if len(contacts) >= normalized_limit:
            break

    if len(contacts) < normalized_limit:
        for user in company_users:
            company_user_id = _field(user, "id")
            if not company_user_id or company_user_id == normalized_user_id:
                continue
            if company_user_id in included_user_ids:
                continue
            if not _user_matches_query(user, normalized_query):
                continue
            contact = _normalize_company_contact(user)
            if contact.get("name") or contact.get("email"):
                contacts.append(contact)
                included_user_ids.add(company_user_id)
            if len(contacts) >= normalized_limit:
                break

    return {
        "ok": True,
        "query": normalized_query,
        "contacts": contacts,
        "count": len(contacts),
        "limit": normalized_limit,
    }


def search_current_user_contacts_payload(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return search_current_user_contacts(
        user_id,
        query=(body or {}).get("query"),
        limit=(body or {}).get("limit", DEFAULT_LIMIT),
    )


def error_payload(error: Exception) -> tuple[dict[str, Any], int]:
    if isinstance(error, UserContactRequestError):
        return {"ok": False, "error": str(error), "code": error.code}, error.status
    if isinstance(error, UserProviderAuthError):
        return {"ok": False, "error": str(error), "code": error.code}, error.status
    if isinstance(error, UserProviderLookupError):
        return {"ok": False, "error": str(error), "code": "contact_lookup_failed"}, 503
    return {"ok": False, "error": str(error), "code": "contact_request_failed"}, 500

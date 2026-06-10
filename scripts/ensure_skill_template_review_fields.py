"""Check or create NoCoBase fields required by skill-template review reports.

Default mode is read-only. Pass --apply only after production schema changes
have been reviewed and approved.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
COLLECTION_NAME = "hermes_skills_templates"
REQUIRED_FIELDS = (
    "security_test_result",
    "security_tested_at",
    "availability_test_result",
    "availability_tested_at",
)
ENV_FILES = (
    ".env.webui.local",
    ".env.local",
    ".env",
)


def _load_env_files() -> None:
    for env_name in ENV_FILES:
        env_path = REPO_ROOT / env_name
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            os.environ[key] = value.strip().strip("'\"")


def _normalize_api_base_url() -> str:
    raw_api_base_url = os.getenv("NOCOBASE_API_BASE_URL", "").strip()
    if raw_api_base_url:
        return raw_api_base_url.rstrip("/")

    raw_base_url = (
        os.getenv("HERMES_USER_PROVIDER_NOCOBASE_BASE_URL")
        or os.getenv("FOXUAI_NOCOBASE_BASE_URL")
        or os.getenv("NOCOBASE_BASE_URL")
        or "https://www.foxuai.com"
    ).strip()
    base_url = raw_base_url.rstrip("/")
    if base_url.endswith("/api"):
        return base_url
    return f"{base_url}/api"


def _authorization_header() -> str:
    raw_authorization = (
        os.getenv("NOCOBASE_AUTHORIZATION")
        or os.getenv("FOXUAI_NOCOBASE_AUTHORIZATION")
        or os.getenv("HERMES_USER_PROVIDER_NOCOBASE_AUTHORIZATION")
        or ""
    ).strip()
    if not raw_authorization:
        raise RuntimeError(
            "Missing NoCoBase authorization. Set NOCOBASE_AUTHORIZATION, "
            "FOXUAI_NOCOBASE_AUTHORIZATION, or HERMES_USER_PROVIDER_NOCOBASE_AUTHORIZATION."
        )
    if raw_authorization.lower().startswith("bearer "):
        return raw_authorization
    return f"Bearer {raw_authorization}"


def _headers(*, has_body: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Authorization": _authorization_header(),
        "X-Hostname": os.getenv("NOCOBASE_HOSTNAME", "www.foxuai.com").strip()
        or "www.foxuai.com",
        "X-Authenticator": os.getenv("NOCOBASE_AUTHENTICATOR", "basic").strip()
        or "basic",
    }
    if has_body:
        headers["Content-Type"] = "application/json"
    return headers


def _request(
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_path = "/" + str(path or "").lstrip("/")
    url = f"{_normalize_api_base_url()}{normalized_path}"
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=_headers(has_body=body is not None),
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # nosec B310
            raw_text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        message = f"NoCoBase request failed: HTTP {exc.code}"
        try:
            payload = json.loads(error_text) if error_text else {}
            if isinstance(payload, dict):
                message = str(payload.get("message") or message)
        except json.JSONDecodeError:
            pass
        raise RuntimeError(message) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"NoCoBase request failed: {exc.reason}") from exc
    if not raw_text:
        return {}
    payload = json.loads(raw_text)
    if not isinstance(payload, dict):
        raise RuntimeError("NoCoBase returned a non-object response")
    return payload


def _field_payloads() -> dict[str, dict[str, Any]]:
    return {
        "security_test_result": {
            "name": "security_test_result",
            "type": "json",
            "interface": "json",
            "uiSchema": {
                "type": "object",
                "x-component": "JSON",
                "title": "安全测试结果",
            },
            "description": "Submitted user Skill security scan JSON snapshot",
        },
        "security_tested_at": {
            "name": "security_tested_at",
            "type": "date",
            "interface": "datetime",
            "uiSchema": {
                "type": "string",
                "x-component": "DatePicker",
                "x-component-props": {
                    "showTime": True,
                    "utc": True,
                    "picker": "date",
                    "dateFormat": "YYYY-MM-DD",
                    "gmt": False,
                    "timeFormat": "HH:mm:ss",
                },
                "title": "安全测试时间",
            },
            "description": "Submitted user Skill security scan completion time",
        },
        "availability_test_result": {
            "name": "availability_test_result",
            "type": "json",
            "interface": "json",
            "uiSchema": {
                "type": "object",
                "x-component": "JSON",
                "title": "有效性测试结果",
            },
            "description": "Submitted user Skill Promptfoo JSON snapshot",
        },
        "availability_tested_at": {
            "name": "availability_tested_at",
            "type": "date",
            "interface": "datetime",
            "uiSchema": {
                "type": "string",
                "x-component": "DatePicker",
                "x-component-props": {
                    "showTime": True,
                    "utc": True,
                    "picker": "date",
                    "dateFormat": "YYYY-MM-DD",
                    "gmt": False,
                    "timeFormat": "HH:mm:ss",
                },
                "title": "有效性测试时间",
            },
            "description": "Submitted user Skill Promptfoo completion time",
        },
    }


def _list_field_names() -> set[str]:
    payload = _request(f"/collections:get?filterByTk={COLLECTION_NAME}&appends=fields")
    data = payload.get("data") if isinstance(payload, dict) else {}
    fields = data.get("fields") if isinstance(data, dict) else []
    if not isinstance(fields, list):
        raise RuntimeError(f"NoCoBase collection {COLLECTION_NAME} returned invalid fields")
    return {
        str(field.get("name") or "").strip()
        for field in fields
        if isinstance(field, dict) and str(field.get("name") or "").strip()
    }


def _create_field(payload: dict[str, Any]) -> None:
    _request(
        f"/collections/{COLLECTION_NAME}/fields:create",
        method="POST",
        body=payload,
    )


def _print_summary(*, existing: set[str], missing: list[str], apply_changes: bool) -> None:
    payloads = _field_payloads()
    existing_required = [field_name for field_name in REQUIRED_FIELDS if field_name in existing]
    print(f"NoCoBase API: {_normalize_api_base_url()}")
    print(f"Collection: {COLLECTION_NAME}")
    print(f"Mode: {'apply' if apply_changes else 'dry-run'}")
    print("Existing required fields:")
    if existing_required:
        for field_name in existing_required:
            print(f"  - {field_name}")
    else:
        print("  - none")
    print("Missing required fields:")
    if missing:
        for field_name in missing:
            payload = payloads[field_name]
            print(
                "  - "
                f"{field_name} "
                f"(type={payload.get('type')}, interface={payload.get('interface')})"
            )
    else:
        print("  - none")
    if missing and not apply_changes:
        print("Dry-run only. Re-run with --apply after schema change approval.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check or create hermes_skills_templates fields required by "
            "skill market review report snapshots."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create missing fields. Without this flag the script is read-only.",
    )
    args = parser.parse_args()

    _load_env_files()
    existing = _list_field_names()
    missing = [field_name for field_name in REQUIRED_FIELDS if field_name not in existing]
    _print_summary(existing=existing, missing=missing, apply_changes=args.apply)

    if not missing:
        return 0
    if not args.apply:
        return 1

    payloads = _field_payloads()
    for field_name in missing:
        _create_field(payloads[field_name])
        print(f"Created field: {field_name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc

import json
import sys
import urllib.parse
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_SRC = REPO_ROOT / "hermes-agent-src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


from api.features import user_contacts


def test_search_current_user_contacts_filters_owner_and_returns_whitelist(monkeypatch):
    calls = []

    def fake_list(collection, params):
        calls.append((collection, list(params)))
        if collection == user_contacts.CONTACT_RELATION_COLLECTION:
            return [
                {
                    "id": "relation-1",
                    "affiliated_user_id": "user-1",
                    "contact_added_id": "contact-1",
                    "nickname": "张三",
                    "email": "custom@example.com",
                    "phone": "123",
                    "company": "Foxu",
                    "department": "Sales",
                    "password_hash": "must-not-return",
                }
            ]
        if collection == user_contacts.HERMES_USERS_COLLECTION:
            return [
                {
                    "id": "contact-1",
                    "username": "zhangsan",
                    "nickname": "张三系统名",
                    "email": "base@example.com",
                    "phone": "456",
                    "company": "BaseCo",
                    "department": "BaseDept",
                    "password_hash": "must-not-return",
                }
            ]
        raise AssertionError(collection)

    monkeypatch.setattr(user_contacts, "_nocobase_list", fake_list)

    result = user_contacts.search_current_user_contacts("user-1", query="张三", limit=5)

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["contacts"] == [
        {
            "name": "张三",
            "email": "custom@example.com",
            "phone": "123",
            "company": "Foxu",
            "department": "Sales",
            "source": "personal_contact",
        }
    ]
    relation_params = dict(calls[0][1])
    assert calls[0][0] == user_contacts.CONTACT_RELATION_COLLECTION
    assert relation_params["filter[affiliated_user_id]"] == "user-1"
    assert "password_hash" not in relation_params["fields"]
    user_params = calls[1][1]
    assert calls[1][0] == user_contacts.HERMES_USERS_COLLECTION
    assert ("filter[id][$in][]", "contact-1") in user_params
    assert "password_hash" not in dict(user_params)["fields"]


def test_search_current_user_contacts_clamps_limit_and_matches_user_fallback(monkeypatch):
    captured = {}

    def fake_list(collection, params):
        captured[collection] = list(params)
        if collection == user_contacts.CONTACT_RELATION_COLLECTION:
            return [
                {
                    "id": "relation-1",
                    "affiliated_user_id": "user-1",
                    "contact_added_id": "contact-1",
                }
            ]
        if collection == user_contacts.HERMES_USERS_COLLECTION:
            return [
                {
                    "id": "contact-1",
                    "username": "alice",
                    "nickname": "Alice Chen",
                    "email": "alice@example.com",
                }
            ]
        return []

    monkeypatch.setattr(user_contacts, "_nocobase_list", fake_list)

    result = user_contacts.search_current_user_contacts("user-1", query="Alice", limit=99)

    assert result["limit"] == user_contacts.MAX_LIMIT
    assert result["count"] == 1
    assert result["contacts"][0]["name"] == "Alice Chen"
    assert result["contacts"][0]["email"] == "alice@example.com"


def test_search_current_user_contacts_includes_company_contacts(monkeypatch):
    calls = []

    def fake_list(collection, params):
        calls.append((collection, list(params)))
        if collection == user_contacts.CONTACT_RELATION_COLLECTION:
            return []
        if collection == user_contacts.HERMES_USERS_COLLECTION:
            return [
                {
                    "id": "user-1",
                    "username": "current",
                    "nickname": "Current User",
                    "email": "current@example.com",
                },
                {
                    "id": "company-1",
                    "username": "lisa",
                    "nickname": "Lisa Wang",
                    "email": "lisa@example.com",
                    "phone": "789",
                    "company": "Foxu",
                    "department": "HR",
                    "password_hash": "must-not-return",
                },
            ]
        raise AssertionError(collection)

    monkeypatch.setattr(user_contacts, "_nocobase_list", fake_list)

    result = user_contacts.search_current_user_contacts("user-1", query="Lisa", limit=5)

    assert result["count"] == 1
    assert result["contacts"] == [
        {
            "name": "Lisa Wang",
            "email": "lisa@example.com",
            "phone": "789",
            "company": "Foxu",
            "department": "HR",
            "source": "company_contact",
        }
    ]
    assert calls[0][0] == user_contacts.CONTACT_RELATION_COLLECTION
    assert calls[1][0] == user_contacts.HERMES_USERS_COLLECTION
    assert dict(calls[1][1])["fields"] == user_contacts.CONTACT_USER_FIELDS


def test_search_current_user_contacts_prefers_personal_profile_over_company_duplicate(monkeypatch):
    def fake_list(collection, params):
        if collection == user_contacts.CONTACT_RELATION_COLLECTION:
            return [
                {
                    "id": "relation-1",
                    "affiliated_user_id": "user-1",
                    "contact_added_id": "contact-1",
                    "nickname": "私人张三",
                    "email": "personal@example.com",
                }
            ]
        if collection == user_contacts.HERMES_USERS_COLLECTION:
            params_dict = dict(params)
            if any(key == "filter[id][$in][]" for key, _value in params):
                return [
                    {
                        "id": "contact-1",
                        "username": "zhangsan",
                        "nickname": "张三",
                        "email": "company@example.com",
                    }
                ]
            assert params_dict["paginate"] == "false"
            return [
                {
                    "id": "contact-1",
                    "username": "zhangsan",
                    "nickname": "张三",
                    "email": "company@example.com",
                },
                {
                    "id": "user-1",
                    "username": "current",
                    "nickname": "Current User",
                    "email": "current@example.com",
                },
            ]
        raise AssertionError(collection)

    monkeypatch.setattr(user_contacts, "_nocobase_list", fake_list)

    result = user_contacts.search_current_user_contacts("user-1", query="张三", limit=5)

    assert result["count"] == 1
    assert result["contacts"][0]["name"] == "私人张三"
    assert result["contacts"][0]["email"] == "personal@example.com"
    assert result["contacts"][0]["source"] == "personal_contact"


def test_nocobase_list_accepts_api_base_url_with_or_without_api_suffix(monkeypatch):
    captured_urls = []
    monkeypatch.setenv("NOCOBASE_AUTHORIZATION", "secret-token")
    monkeypatch.setenv("NOCOBASE_API_BASE_URL", "https://nocobase.example")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b'{"data":[]}'

    def fake_urlopen(request, timeout):
        captured_urls.append(request.full_url)
        assert request.get_header("Authorization") == "Bearer secret-token"
        assert timeout == user_contacts.NOCOBASE_TIMEOUT_SECONDS
        return FakeResponse()

    monkeypatch.setattr(user_contacts.urllib.request, "urlopen", fake_urlopen)

    user_contacts._nocobase_list("hermes_users_contacts", [("paginate", "false")])
    monkeypatch.setenv("NOCOBASE_API_BASE_URL", "https://nocobase.example/api")
    user_contacts._nocobase_list("hermes_users_contacts", [("paginate", "false")])

    assert captured_urls == [
        "https://nocobase.example/api/hermes_users_contacts:list?paginate=false",
        "https://nocobase.example/api/hermes_users_contacts:list?paginate=false",
    ]


def test_search_current_user_contacts_rejects_missing_user_context():
    with pytest.raises(Exception) as exc_info:
        user_contacts.search_current_user_contacts("", query="Alice")

    assert getattr(exc_info.value, "code", "") == "missing_user_context"


def test_current_user_contacts_tool_requires_session_user(monkeypatch):
    from gateway import session_context
    from tools import user_contacts_tool

    tokens = session_context.set_session_vars(user_id="", session_key="session-1")
    try:
        result = json.loads(user_contacts_tool.current_user_contacts_lookup_tool("Alice"))
    finally:
        session_context.clear_session_vars(tokens)

    assert result["code"] == "missing_user_context"


def test_current_user_contacts_tool_uses_session_user(monkeypatch):
    from gateway import session_context
    from tools import user_contacts_tool

    captured = {}

    def fake_search(user_id, *, query, limit=5):
        captured.update({"user_id": user_id, "query": query, "limit": limit})
        return {"ok": True, "contacts": [], "count": 0, "limit": int(limit), "query": query}

    monkeypatch.setattr(user_contacts, "search_current_user_contacts", fake_search)

    tokens = session_context.set_session_vars(user_id="user-1", session_key="session-1")
    try:
        result = json.loads(user_contacts_tool.current_user_contacts_lookup_tool("Alice", limit=3))
    finally:
        session_context.clear_session_vars(tokens)

    assert result["ok"] is True
    assert captured == {"user_id": "user-1", "query": "Alice", "limit": 3}


def test_user_contacts_toolset_is_available_by_default():
    from api.core.config import _DEFAULT_TOOLSETS
    from model_tools import get_tool_definitions

    tools = get_tool_definitions(enabled_toolsets=_DEFAULT_TOOLSETS, quiet_mode=True)
    names = {tool["function"]["name"] for tool in tools}

    assert "user_contacts" in _DEFAULT_TOOLSETS
    assert "current_user_contacts_lookup" in names


def test_webui_required_toolsets_adds_user_contacts_to_overrides():
    from api.runtime.streaming import _with_webui_required_toolsets

    assert _with_webui_required_toolsets(["web", "terminal"]) == [
        "web",
        "terminal",
        "user_contacts",
    ]
    assert _with_webui_required_toolsets(["user_contacts"]) == ["user_contacts"]


def test_user_contacts_route_uses_current_user_context(monkeypatch):
    import api.routes as routes
    import api.routes_dispatcher as dispatcher
    import api.user_provider as user_provider
    from api.features import user_contacts as user_contacts_feature

    captured = {}

    def fake_json(_handler, payload, status=200, **_kwargs):
        captured["payload"] = payload
        captured["status"] = status
        return True

    def fake_current_user(_handler):
        return "header-user"

    def fake_payload(user_id, body):
        captured["payload_call"] = {"user_id": user_id, "body": body}
        return {"ok": True, "contacts": []}

    monkeypatch.setattr(routes, "j", fake_json)
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {"query": "Alice", "user_id": "body-user"},
    )
    monkeypatch.setattr(user_provider, "current_user_id_from_handler", fake_current_user)
    monkeypatch.setattr(user_contacts_feature, "search_current_user_contacts_payload", fake_payload)

    handled = dispatcher.dispatch_post(
        object(),
        urllib.parse.urlparse("/api/user-contacts/search"),
    )

    assert handled is True
    assert captured["status"] == 200
    assert captured["payload"] == {"ok": True, "contacts": []}
    assert captured["payload_call"] == {
        "user_id": "header-user",
        "body": {"query": "Alice", "user_id": "body-user"},
    }

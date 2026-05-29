"""
Sprint 42 Tests: SessionDB injection into AIAgent for WebUI sessions (PR #356).

Covers:
- streaming.py: SessionDB is initialized inside _run_agent_streaming (import present)
- streaming.py: try/except guards SessionDB init so failures are non-fatal
- streaming.py: session_db= kwarg is passed to AIAgent constructor
- streaming.py: SessionDB init failure prints a WARNING (not silently swallowed)
- streaming.py: SessionDB init is placed before AIAgent construction
"""
import ast
import pathlib
import re
import queue
import sys
import types
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).parent.parent
STREAMING_PY = (REPO_ROOT / "api" / "streaming.py").read_text()


# ── Shared helpers for sprint-42 additional tests ────────────────────────────

REPO = REPO_ROOT  # alias used by #427 tests
_STREAMING_PY = REPO_ROOT / 'api' / 'streaming.py'
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionDBInjection(unittest.TestCase):
    """Verify SessionDB is initialized and passed to AIAgent in streaming.py."""

    def test_hermes_state_import_present(self):
        """SessionDB must be imported from hermes_state inside _run_agent_streaming."""
        self.assertIn(
            "from hermes_state import SessionDB",
            STREAMING_PY,
            "SessionDB import missing from streaming.py (PR #356)",
        )

    def test_session_db_kwarg_passed_to_agent(self):
        """session_db= must be passed to the AIAgent constructor call."""
        self.assertIn(
            "session_db=_session_db",
            STREAMING_PY,
            "session_db kwarg not passed to AIAgent (PR #356)",
        )

    def test_sessiondb_init_in_try_except(self):
        """SessionDB() init must be wrapped in try/except for non-fatal failure handling."""
        # Check that the try/except pattern surrounding SessionDB() is present
        pattern = r"try:\s*\n\s*from hermes_state import SessionDB\s*\n\s*_session_db\s*=\s*SessionDB\(\)"
        self.assertRegex(
            STREAMING_PY,
            pattern,
            "SessionDB() init must be inside a try block for non-fatal error handling (PR #356)",
        )

    def test_sessiondb_failure_logs_warning(self):
        """A failure initializing SessionDB must print a WARNING (not silently drop the error)."""
        self.assertIn(
            "WARNING: SessionDB init failed",
            STREAMING_PY,
            "SessionDB init failure must log a WARNING message (PR #356)",
        )

    def test_session_db_initialized_before_agent_construction(self):
        """SessionDB initialization must appear before the AIAgent(...) constructor call."""
        db_pos = STREAMING_PY.find("from hermes_state import SessionDB")
        agent_pos = STREAMING_PY.find("session_db=_session_db")
        self.assertGreater(
            agent_pos,
            db_pos,
            "SessionDB init must appear before AIAgent construction (PR #356)",
        )

    def test_session_db_default_is_none(self):
        """_session_db must be initialized to None before the try block (safe default)."""
        # Pattern: _session_db = None followed (eventually) by the try/SessionDB block
        pattern = r"_session_db\s*=\s*None\s*\n\s*try:"
        self.assertRegex(
            STREAMING_PY,
            pattern,
            "_session_db must default to None before try/except block (PR #356)",
        )


class TestRuntimeRouteInjection(unittest.TestCase):
    """Verify WebUI forwards the resolved runtime route into AIAgent."""

    def test_runtime_provider_keys_are_forwarded_to_agent(self):
        """WebUI must pass the runtime route fields that CLI already uses.

        Since issue #772 these are passed defensively via inspect-guarded kwargs
        so the WebUI degrades gracefully against older hermes-agent builds.
        """
        for snippet in (
            "_agent_kwargs['api_mode'] = _rt.get('api_mode')",
            "_agent_kwargs['acp_command'] = _rt.get('command')",
            "_agent_kwargs['acp_args'] = _rt.get('args')",
            "_agent_kwargs['credential_pool'] = _rt.get('credential_pool')",
        ):
            self.assertIn(
                snippet,
                STREAMING_PY,
                f"Missing defensive runtime route forwarding in streaming.py: {snippet}",
            )

    def test_runtime_route_is_forwarded_from_resolver_into_agent_init(self):
        """The resolved ACP route should be passed through to AIAgent kwargs."""
        import api.streaming as streaming

        captured = {}
        fake_session_db = object()
        resolve_runtime_provider = mock.Mock(
            return_value={
                "provider": "openai-codex",
                "base_url": "https://api.openai.com/v1",
                "api_key": "rt-key",
                "api_mode": "codex_responses",
                "command": "codex",
                "args": ["exec", "--json"],
                "credential_pool": "openai-codex",
            }
        )

        class FakeSession:
            def __init__(self):
                self.session_id = "sess-runtime-route"
                self.title = "Existing title"
                self.workspace = "/tmp"
                self.model = "gpt-5.4"
                self.messages = []
                self.personality = None
                self.input_tokens = 0
                self.output_tokens = 0
                self.estimated_cost = None
                self.tool_calls = []
                self.active_stream_id = None
                self.pending_user_message = None
                self.pending_attachments = []
                self.pending_started_at = None

            def save(self, touch_updated_at=True):
                self._saved = True

            def compact(self):
                return {
                    "session_id": self.session_id,
                    "title": self.title,
                    "workspace": self.workspace,
                    "model": self.model,
                    "created_at": 0,
                    "updated_at": 0,
                    "pinned": False,
                    "archived": False,
                    "project_id": None,
                    "profile": None,
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                    "estimated_cost": self.estimated_cost,
                    "personality": self.personality,
                }

        class CapturingAgent:
            def __init__(self, model=None, provider=None, base_url=None, api_key=None,
                         api_mode=None, acp_command=None, acp_args=None,
                         credential_pool=None, platform=None, quiet_mode=False,
                         enabled_toolsets=None, fallback_model=None, session_id=None,
                         session_db=None, stream_delta_callback=None,
                         reasoning_callback=None, tool_progress_callback=None,
                         clarify_callback=None, **kwargs):
                captured["init_kwargs"] = dict(
                    model=model, provider=provider, base_url=base_url,
                    api_key=api_key, api_mode=api_mode, acp_command=acp_command,
                    acp_args=acp_args, credential_pool=credential_pool,
                    platform=platform, quiet_mode=quiet_mode,
                    enabled_toolsets=enabled_toolsets, fallback_model=fallback_model,
                    session_id=session_id, session_db=session_db,
                    stream_delta_callback=stream_delta_callback,
                    reasoning_callback=reasoning_callback,
                    tool_progress_callback=tool_progress_callback,
                    clarify_callback=clarify_callback,
                )
                self.session_id = session_id
                self.context_compressor = None
                self.session_prompt_tokens = 0
                self.session_completion_tokens = 0
                self.session_estimated_cost_usd = None
                self.reasoning_config = None
                self.ephemeral_system_prompt = None
                self._last_error = None

            def run_conversation(self, **kwargs):
                captured["run_kwargs"] = kwargs
                return {
                    "messages": [
                        {"role": "user", "content": kwargs["persist_user_message"]},
                        {"role": "assistant", "content": "ok"},
                    ]
                }

            def interrupt(self, _message):
                captured["interrupted"] = True

        fake_session = FakeSession()
        fake_stream_id = "stream-runtime-route"
        fake_queue = queue.Queue()
        fake_runtime_module = types.ModuleType("hermes_cli.runtime_provider")
        fake_runtime_module.resolve_runtime_provider = resolve_runtime_provider
        fake_hermes_cli = types.ModuleType("hermes_cli")
        fake_hermes_cli.runtime_provider = fake_runtime_module
        fake_hermes_state = types.ModuleType("hermes_state")
        fake_hermes_state.SessionDB = mock.Mock(return_value=fake_session_db)

        with mock.patch.object(streaming, "get_session", return_value=fake_session), \
             mock.patch.object(streaming, "_get_ai_agent", return_value=CapturingAgent), \
             mock.patch.object(streaming, "resolve_model_provider", return_value=("gpt-5.4", "openai-codex", None)), \
             mock.patch("api.config.get_config", return_value={}), \
             mock.patch("api.config._resolve_cli_toolsets", return_value=[]), \
             mock.patch.dict(
                 sys.modules,
                 {
                     "hermes_cli": fake_hermes_cli,
                     "hermes_cli.runtime_provider": fake_runtime_module,
                     "hermes_state": fake_hermes_state,
                 },
             ):
            streaming.STREAMS[fake_stream_id] = fake_queue
            streaming._run_agent_streaming(
                session_id=fake_session.session_id,
                msg_text="hello from webui",
                model="gpt-5.4",
                workspace="/tmp",
                stream_id=fake_stream_id,
            )

        resolve_runtime_provider.assert_called_once_with(requested="openai-codex")
        init_kwargs = captured["init_kwargs"]
        self.assertEqual(init_kwargs["api_mode"], "codex_responses")
        self.assertEqual(init_kwargs["acp_command"], "codex")
        self.assertEqual(init_kwargs["acp_args"], ["exec", "--json"])
        self.assertEqual(init_kwargs["credential_pool"], "openai-codex")
        self.assertEqual(init_kwargs["api_key"], "rt-key")
        self.assertIs(init_kwargs["session_db"], fake_session_db)


class TestSessionDBAST(unittest.TestCase):
    """AST-level checks: verify the try/except is not inside _ENV_LOCK (deadlock guard)."""

    def setUp(self):
        self.tree = ast.parse(STREAMING_PY)

    def test_sessiondb_try_not_inside_env_lock(self):
        """The try block that wraps SessionDB init must NOT be inside a 'with _ENV_LOCK:' block.

        Putting a try/except inside _ENV_LOCK is the deadlock pattern caught by test_sprint34.
        The SessionDB try/except is outside the lock scope, which is correct.
        """
        # Find all 'with _ENV_LOCK:' nodes; check none of their bodies contain
        # a Try node that also contains 'from hermes_state import SessionDB'
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.With):
                continue
            names = [getattr(item.context_expr, "id", "") for item in node.items]
            if "_ENV_LOCK" not in names:
                continue
            # Walk the with-body for Try nodes
            for stmt in node.body:
                if isinstance(stmt, ast.Try):
                    # Check if this try imports hermes_state
                    src = ast.unparse(stmt)
                    self.assertNotIn(
                        "hermes_state",
                        src,
                        "SessionDB try/except must NOT be inside _ENV_LOCK body (deadlock risk)",
                    )


# ── Sprint 42 additional tests: context indicator (#437) ─────────────────
# ── Sprint 42 additional tests: system prompt title (#441) ──────────────
# ── Sprint 42 additional tests: thinking panel persistence (#427) ────────
def test_streaming_persists_reasoning_in_session():
    """streaming.py must accumulate reasoning_text and patch last assistant message."""
    src = (REPO / 'api' / 'streaming.py').read_text()

    # _reasoning_text must be initialised
    assert "_reasoning_text = ''" in src, \
        "_reasoning_text variable not initialised in streaming.py"

    # on_reasoning must accumulate into _reasoning_text
    assert '_reasoning_text += str(text)' in src, \
        "on_reasoning callback does not accumulate into _reasoning_text"

    # Persistence block must exist before raw_session is built
    assert "Persist reasoning trace in the session so it survives reload" in src, \
        "Reasoning persistence comment not found in streaming.py"

    assert "_rm['reasoning'] = _reasoning_text" in src, \
        "Code to set _rm['reasoning'] not found in streaming.py"

    # Persistence block must come BEFORE raw_session assignment
    persist_idx = src.index("Persist reasoning trace in the session")
    raw_session_idx = src.index("raw_session = s.compact()")
    assert persist_idx < raw_session_idx, \
        "Reasoning persistence block must appear before raw_session assignment"


def test_streaming_restores_prior_reasoning_metadata_after_followup():
    """Previous-turn thinking must survive later turns.

    The provider-facing history strips WebUI-only `reasoning` fields, so the
    streaming path must merge that metadata back onto the returned message
    history before saving the session, including reinserting dropped
    reasoning-only assistant segments.
    """
    src = (REPO / 'api' / 'streaming.py').read_text()
    assert "def _restore_reasoning_metadata(" in src, \
        "streaming.py must define a helper to restore prior reasoning metadata"
    assert "s.context_messages = _next_context_messages" in src, \
        "streaming.py must restore prior reasoning metadata into model context"
    assert "s.messages = _merge_display_messages_after_agent_result(" in src, \
        "streaming.py must merge restored result messages into the visible transcript"
    assert "updated_messages.insert(safe_pos, copy.deepcopy(prev_msg))" in src, \
        "streaming.py must reinsert dropped reasoning-only assistant messages"


def test_routes_restores_prior_reasoning_metadata_after_followup():
    """The non-streaming route path must preserve prior reasoning metadata too."""
    from tests.route_source import read_route_sources
    src = read_route_sources()
    assert "_restore_reasoning_metadata" in src, \
        "routes.py must import reasoning metadata restoration helper"
    assert "s.context_messages = _next_context_messages" in src, \
        "routes.py must restore prior reasoning metadata into model context"
    assert 's.messages = _merge_display_messages_after_agent_result(' in src, \
        "routes.py must merge restored result messages into the visible transcript"


class TestCredentialPoolBackwardCompat(unittest.TestCase):
    """Verify credential_pool and other newer kwargs are skipped gracefully
    when running against an older hermes-agent that lacks them (issue #772)."""

    def test_older_agent_without_credential_pool_does_not_crash(self):
        """WebUI must not crash with TypeError when AIAgent lacks credential_pool."""
        import api.streaming as streaming

        captured = {}

        class OlderAgent:
            """Simulates a hermes-agent build that predates credential_pool."""
            def __init__(self, model=None, provider=None, base_url=None, api_key=None,
                         platform=None, quiet_mode=False, enabled_toolsets=None,
                         fallback_model=None, session_id=None, session_db=None,
                         stream_delta_callback=None, reasoning_callback=None,
                         tool_progress_callback=None, clarify_callback=None):
                # No api_mode / acp_command / acp_args / credential_pool params
                captured["init_kwargs"] = {"session_id": session_id, "model": model}
                self.session_id = session_id
                self.context_compressor = None
                self.session_prompt_tokens = 0
                self.session_completion_tokens = 0
                self.session_estimated_cost_usd = None
                self.reasoning_config = None
                self.ephemeral_system_prompt = None
                self._last_error = None

            def run_conversation(self, **kwargs):
                return {
                    "messages": [
                        {"role": "user", "content": kwargs.get("persist_user_message", "")},
                        {"role": "assistant", "content": "ok"},
                    ]
                }

            def interrupt(self, _message):
                pass

        class FakeSession:
            session_id = "sess-compat-test"
            title = "Test"
            workspace = "/tmp"
            model = "gpt-4o"
            messages = []
            personality = None
            input_tokens = 0
            output_tokens = 0
            estimated_cost = None
            tool_calls = []
            active_stream_id = None
            pending_user_message = None
            pending_attachments = []
            pending_started_at = None

            def save(self, touch_updated_at=True):
                pass

            def compact(self):
                return {
                    "session_id": self.session_id, "title": self.title,
                    "workspace": self.workspace, "model": self.model,
                    "created_at": 0, "updated_at": 0, "pinned": False,
                    "archived": False, "project_id": None, "profile": None,
                    "input_tokens": 0, "output_tokens": 0,
                    "estimated_cost": None, "personality": None,
                }

        fake_stream_id = "stream-compat-test"
        fake_queue = queue.Queue()
        fake_rt_module = types.ModuleType("hermes_cli.runtime_provider")
        fake_rt_module.resolve_runtime_provider = mock.Mock(return_value={
            "provider": "openai", "base_url": None, "api_key": "sk-test",
            "api_mode": "chat_completions", "command": None, "args": [],
            "credential_pool": object(),
        })
        fake_hermes_cli = types.ModuleType("hermes_cli")
        fake_hermes_cli.runtime_provider = fake_rt_module
        fake_hermes_state = types.ModuleType("hermes_state")
        fake_hermes_state.SessionDB = mock.Mock(return_value=None)

        with mock.patch.object(streaming, "get_session", return_value=FakeSession()), \
             mock.patch.object(streaming, "_get_ai_agent", return_value=OlderAgent), \
             mock.patch.object(streaming, "resolve_model_provider", return_value=("gpt-4o", "openai", None)), \
             mock.patch("api.config.get_config", return_value={}), \
             mock.patch("api.config._resolve_cli_toolsets", return_value=[]), \
             mock.patch.dict(sys.modules, {
                 "hermes_cli": fake_hermes_cli,
                 "hermes_cli.runtime_provider": fake_rt_module,
                 "hermes_state": fake_hermes_state,
             }):
            streaming.STREAMS[fake_stream_id] = fake_queue
            # Must not raise TypeError
            streaming._run_agent_streaming(
                session_id="sess-compat-test",
                msg_text="hello",
                model="gpt-4o",
                workspace="/tmp",
                stream_id=fake_stream_id,
            )

        # Agent was constructed successfully
        self.assertIn("session_id", captured["init_kwargs"])
        self.assertEqual(captured["init_kwargs"]["session_id"], "sess-compat-test")


class TestWebuiGlobalEphemeralPrompt(unittest.TestCase):
    """Verify WebUI agent runs receive the profile-local cron prompt."""

    def _run_streaming_capture(self, *, personality=None, config=None):
        import api.streaming as streaming

        captured = {}
        session_id = f"sess-webui-prompt-{personality or 'none'}"
        stream_id = f"stream-webui-prompt-{personality or 'none'}"
        profile_name = "default_366843698282497"
        profile_home = pathlib.Path(
            "/var/www/hermes-agent/.hermes/profiles/default_366843698282497"
        )

        class FakeSession:
            def __init__(self):
                self.session_id = session_id
                self.title = "Prompt Test"
                self.workspace = "/tmp"
                self.model = "gpt-5.4"
                self.messages = []
                self.context_messages = []
                self.personality = personality
                self.profile = profile_name
                self.input_tokens = 0
                self.output_tokens = 0
                self.estimated_cost = None
                self.tool_calls = []
                self.active_stream_id = None
                self.pending_user_message = None
                self.pending_attachments = []
                self.pending_started_at = None
                self.gateway_routing = None
                self.gateway_routing_history = []

            def save(self, *args, **kwargs):
                self._saved = True

            def compact(self):
                return {
                    "session_id": self.session_id,
                    "title": self.title,
                    "workspace": self.workspace,
                    "model": self.model,
                    "created_at": 0,
                    "updated_at": 0,
                    "pinned": False,
                    "archived": False,
                    "project_id": None,
                    "profile": self.profile,
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                    "estimated_cost": self.estimated_cost,
                    "personality": self.personality,
                }

        class CapturingAgent:
            def __init__(self, model=None, provider=None, base_url=None, api_key=None,
                         platform=None, quiet_mode=False, enabled_toolsets=None,
                         fallback_model=None, session_id=None, session_db=None,
                         stream_delta_callback=None, reasoning_callback=None,
                         tool_progress_callback=None, clarify_callback=None, **kwargs):
                self.session_id = session_id
                self.context_compressor = None
                self.session_prompt_tokens = 0
                self.session_completion_tokens = 0
                self.session_estimated_cost_usd = None
                self.reasoning_config = None
                self.ephemeral_system_prompt = None
                self._last_error = None

            def run_conversation(self, **kwargs):
                captured["ephemeral_system_prompt"] = self.ephemeral_system_prompt
                captured["run_kwargs"] = kwargs
                return {
                    "messages": [
                        {"role": "user", "content": kwargs.get("persist_user_message", "")},
                        {"role": "assistant", "content": "ok"},
                    ]
                }

            def interrupt(self, _message):
                captured["interrupted"] = True

        fake_rt_module = types.ModuleType("hermes_cli.runtime_provider")
        fake_rt_module.resolve_runtime_provider = mock.Mock(return_value={
            "provider": "openai",
            "base_url": None,
            "api_key": "sk-test",
            "api_mode": "chat_completions",
            "command": None,
            "args": [],
            "credential_pool": None,
        })
        fake_hermes_cli = types.ModuleType("hermes_cli")
        fake_hermes_cli.runtime_provider = fake_rt_module
        fake_hermes_state = types.ModuleType("hermes_state")
        fake_hermes_state.SessionDB = mock.Mock(return_value=None)

        with mock.patch.object(streaming, "get_session", return_value=FakeSession()), \
             mock.patch.object(streaming, "_get_ai_agent", return_value=CapturingAgent), \
             mock.patch.object(streaming, "resolve_model_provider", return_value=("gpt-5.4", "openai", None)), \
             mock.patch("api.config.get_config", return_value=config or {}), \
             mock.patch("api.config._resolve_cli_toolsets", return_value=[]), \
             mock.patch("api.profiles.get_hermes_home_for_profile", return_value=profile_home), \
             mock.patch("api.profiles.get_profile_runtime_env", return_value={}), \
             mock.patch.dict(sys.modules, {
                 "hermes_cli": fake_hermes_cli,
                 "hermes_cli.runtime_provider": fake_rt_module,
                 "hermes_state": fake_hermes_state,
             }):
            streaming.STREAMS[stream_id] = queue.Queue()
            try:
                streaming._run_agent_streaming(
                    session_id=session_id,
                    msg_text="hello",
                    model="gpt-5.4",
                    workspace="/tmp",
                    stream_id=stream_id,
                    ephemeral=True,
                )
            finally:
                streaming.STREAMS.pop(stream_id, None)
                streaming.AGENT_INSTANCES.pop(stream_id, None)

        captured["profile_home"] = str(profile_home)
        return captured

    def test_streaming_combines_global_cron_prompt_with_personality_prompt(self):
        captured = self._run_streaming_capture(
            personality="ops",
            config={
                "agent": {
                    "personalities": {
                        "ops": {
                            "system_prompt": "PERSONALITY PROMPT",
                            "tone": "direct",
                            "style": "brief",
                        }
                    }
                }
            },
        )

        prompt = captured["ephemeral_system_prompt"]
        assert "WebUI 全局定时任务规则" in prompt
        assert f'{captured["profile_home"]}/cron/jobs.json' in prompt
        assert "不要写入 root/default 的 cron/jobs.json" in prompt
        assert "PERSONALITY PROMPT" in prompt
        assert "Tone: direct" in prompt
        assert "Style: brief" in prompt

        system_message = captured["run_kwargs"]["system_message"]
        assert system_message.startswith("Active workspace at session start:")
        assert "WebUI 全局定时任务规则" not in system_message

    def test_streaming_sets_global_cron_prompt_without_personality_prompt(self):
        captured = self._run_streaming_capture(personality=None, config={})

        prompt = captured["ephemeral_system_prompt"]
        assert "WebUI 全局定时任务规则" in prompt
        assert f'{captured["profile_home"]}/cron/jobs.json' in prompt
        assert "PERSONALITY PROMPT" not in prompt

"""Locust scenario for real Hermes WebUI chat runs.

Each virtual user logs in when credentials are provided, creates an isolated
session, starts one chat turn, and keeps the SSE connection open until the
server emits a terminal event.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Iterator

from locust import HttpUser, between, task
from locust.exception import StopUser


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ScenarioConfig:
    api_token: str = _env("HERMES_LOADTEST_API_TOKEN")
    password: str = _env("HERMES_LOADTEST_PASSWORD")
    profile: str = _env("HERMES_LOADTEST_PROFILE", "default")
    workspace: str = _env("HERMES_LOADTEST_WORKSPACE")
    model: str = _env("HERMES_LOADTEST_MODEL")
    model_provider: str = _env("HERMES_LOADTEST_MODEL_PROVIDER")
    prompt: str = _env("HERMES_LOADTEST_PROMPT", "Reply with OK only.")
    stream_timeout_seconds: float = _env_float("HERMES_LOADTEST_STREAM_TIMEOUT_SECONDS", 180.0)
    request_timeout_seconds: float = _env_float("HERMES_LOADTEST_REQUEST_TIMEOUT_SECONDS", 30.0)
    wait_min_seconds: float = _env_float("HERMES_LOADTEST_WAIT_MIN_SECONDS", 1.0)
    wait_max_seconds: float = _env_float("HERMES_LOADTEST_WAIT_MAX_SECONDS", 3.0)
    cancel_on_timeout: bool = _env_bool("HERMES_LOADTEST_CANCEL_ON_TIMEOUT", True)


CONFIG = ScenarioConfig()


def _json_or_empty(response) -> dict:
    try:
        data = response.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _iter_sse_events(response, deadline: float) -> Iterator[tuple[str, str]]:
    event = "message"
    data_lines: list[str] = []
    for raw_line in response.iter_lines(chunk_size=1, decode_unicode=True):
        if time.monotonic() > deadline:
            raise TimeoutError("SSE stream exceeded timeout")
        if raw_line is None:
            continue
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                yield event, "\n".join(data_lines)
            event = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip() or "message"
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())


class HermesChatUser(HttpUser):
    wait_time = between(CONFIG.wait_min_seconds, CONFIG.wait_max_seconds)

    def on_start(self) -> None:
        if CONFIG.api_token:
            self._token_login()
        elif CONFIG.password:
            self._password_login()

    def _token_login(self) -> None:
        with self.client.post(
            "/api/auth/token-login",
            json={"token": CONFIG.api_token},
            name="/api/auth/token-login",
            timeout=CONFIG.request_timeout_seconds,
            catch_response=True,
        ) as response:
            if response.status_code != 200 or not _json_or_empty(response).get("ok"):
                response.failure("token login failed")
                raise StopUser()
            response.success()

    def _password_login(self) -> None:
        with self.client.post(
            "/api/auth/login",
            json={"password": CONFIG.password},
            name="/api/auth/login",
            timeout=CONFIG.request_timeout_seconds,
            catch_response=True,
        ) as response:
            if response.status_code != 200 or not _json_or_empty(response).get("ok"):
                response.failure("password login failed")
                raise StopUser()
            response.success()

    def _fire_metric(
        self,
        *,
        request_type: str,
        name: str,
        response_time: float,
        response_length: int = 0,
        exception: Exception | None = None,
    ) -> None:
        self.environment.events.request.fire(
            request_type=request_type,
            name=name,
            response_time=response_time,
            response_length=response_length,
            exception=exception,
            context={},
        )

    def _create_session(self) -> str:
        payload: dict[str, object] = {}
        if CONFIG.workspace:
            payload["workspace"] = CONFIG.workspace
        if CONFIG.model:
            payload["model"] = CONFIG.model
        if CONFIG.model_provider:
            payload["model_provider"] = CONFIG.model_provider
        if CONFIG.profile:
            payload["profile"] = CONFIG.profile

        with self.client.post(
            "/api/session/new",
            json=payload,
            name="/api/session/new",
            timeout=CONFIG.request_timeout_seconds,
            catch_response=True,
        ) as response:
            data = _json_or_empty(response)
            session = data.get("session") if isinstance(data.get("session"), dict) else {}
            session_id = str(session.get("session_id") or "")
            if response.status_code != 200 or not session_id:
                response.failure("session creation failed")
                raise RuntimeError("session creation failed")
            response.success()
            return session_id

    def _start_chat(self, session_id: str) -> str:
        payload: dict[str, object] = {
            "session_id": session_id,
            "message": CONFIG.prompt,
        }
        if CONFIG.workspace:
            payload["workspace"] = CONFIG.workspace
        if CONFIG.model:
            payload["model"] = CONFIG.model
        if CONFIG.model_provider:
            payload["model_provider"] = CONFIG.model_provider
        if CONFIG.profile:
            payload["profile"] = CONFIG.profile

        with self.client.post(
            "/api/chat/start",
            json=payload,
            name="/api/chat/start",
            timeout=CONFIG.request_timeout_seconds,
            catch_response=True,
        ) as response:
            data = _json_or_empty(response)
            stream_id = str(data.get("stream_id") or "")
            if response.status_code != 200 or not stream_id:
                response.failure(f"chat start failed: {data.get('error') or response.status_code}")
                raise RuntimeError("chat start failed")
            response.success()
            return stream_id

    def _cancel_stream(self, stream_id: str) -> None:
        if not CONFIG.cancel_on_timeout:
            return
        try:
            self.client.get(
                f"/api/chat/cancel?stream_id={stream_id}",
                name="/api/chat/cancel",
                timeout=CONFIG.request_timeout_seconds,
            )
        except Exception:
            pass

    def _consume_stream(self, stream_id: str) -> None:
        started = time.perf_counter()
        deadline = time.monotonic() + CONFIG.stream_timeout_seconds
        first_token_seen = False
        response_length = 0
        terminal_error: Exception | None = None
        terminal_event = ""

        try:
            with self.client.get(
                f"/api/chat/stream?stream_id={stream_id}",
                name="/api/chat/stream connect",
                stream=True,
                timeout=(CONFIG.request_timeout_seconds, CONFIG.request_timeout_seconds),
                catch_response=True,
            ) as response:
                if response.status_code != 200:
                    terminal_error = RuntimeError(f"SSE connect failed: {response.status_code}")
                    response.failure(str(terminal_error))
                    return
                response.success()
                for event, payload in _iter_sse_events(response, deadline):
                    response_length += len(payload.encode("utf-8"))
                    if event == "token" and not first_token_seen:
                        first_token_seen = True
                        first_ms = (time.perf_counter() - started) * 1000
                        self._fire_metric(
                            request_type="SSE",
                            name="/api/chat/stream first_token",
                            response_time=first_ms,
                            response_length=len(payload),
                        )
                    if event in {"stream_end", "error", "apperror", "cancel"}:
                        terminal_event = event
                        if event != "stream_end":
                            detail = payload
                            try:
                                parsed = json.loads(payload)
                                detail = parsed.get("message") or parsed.get("error") or payload
                            except Exception:
                                pass
                            terminal_error = RuntimeError(f"SSE terminal event {event}: {detail}")
                        break
                if not terminal_event:
                    terminal_error = TimeoutError("SSE stream ended without terminal event")
        except Exception as exc:
            terminal_error = exc
        finally:
            total_ms = (time.perf_counter() - started) * 1000
            if not first_token_seen:
                self._fire_metric(
                    request_type="SSE",
                    name="/api/chat/stream first_token",
                    response_time=total_ms,
                    response_length=0,
                    exception=RuntimeError("missing first token"),
                )
            self._fire_metric(
                request_type="SSE",
                name="/api/chat/stream total",
                response_time=total_ms,
                response_length=response_length,
                exception=terminal_error,
            )
            if isinstance(terminal_error, TimeoutError):
                self._cancel_stream(stream_id)

    @task
    def run_one_real_chat_turn(self) -> None:
        try:
            session_id = self._create_session()
            stream_id = self._start_chat(session_id)
        except RuntimeError:
            return
        self._consume_stream(stream_id)

"""
Hermes API service -- Optional password authentication.
Off by default. Enable by setting HERMES_WEBUI_PASSWORD env var
or configuring a stored password hash.

中文说明：Hermes API service 的可选密码认证。
默认关闭。可以通过设置 HERMES_WEBUI_PASSWORD 环境变量，
或配置已保存的密码哈希来启用。
"""
import hashlib
import hmac
import http.cookies
import json
import logging
import os
import secrets
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from api.config import STATE_DIR, load_settings

logger = logging.getLogger(__name__)

# ── Public paths (no auth required) ─────────────────────────────────────────
PUBLIC_PATHS = frozenset({
    '/', '/login', '/health', '/favicon.ico',
    '/api/auth/login', '/api/auth/status', '/api/auth/token-login',
    '/api/internal/provider-sync/root-profiles',
    '/manifest.json', '/manifest.webmanifest',
})

REMOVED_WEBUI_PATHS = frozenset({
    '/index.html', '/login', '/manifest.json', '/manifest.webmanifest',
    '/sw.js', '/favicon.ico',
})
REMOVED_WEBUI_PREFIXES = ('/session/', '/static/')

COOKIE_NAME = 'hermes_session'
SESSION_TTL = 86400 * 30  # 30 days

_SESSIONS_FILE = STATE_DIR / '.sessions.json'
_API_TOKENS_FILE = STATE_DIR / 'api_tokens.json'


def _load_sessions() -> dict[str, float]:
    """Load persisted sessions from STATE_DIR, pruning expired entries.

    Returns an empty dict on any read or parse error so startup is never
    blocked by a corrupt or missing sessions file.
    """
    try:
        if _SESSIONS_FILE.exists():
            data = json.loads(_SESSIONS_FILE.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('malformed sessions file — expected dict')
            now = time.time()
            return {t: exp for t, exp in data.items()
                    if isinstance(t, str) and isinstance(exp, (int, float)) and exp > now}
    except Exception as e:
        logger.debug("Failed to load sessions file, starting fresh: %s", e)
    return {}


def _save_sessions(sessions: dict[str, float]) -> None:
    """Atomically persist sessions to STATE_DIR/.sessions.json (0600).

    Uses a temp file + os.replace() so a crash mid-write never leaves a
    truncated file.  Mirrors the same pattern as .signing_key persistence.
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=STATE_DIR, suffix='.sessions.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(sessions, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, _SESSIONS_FILE)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.debug("Failed to persist sessions: %s", e)


# Active sessions: token -> expiry timestamp (persisted across restarts via STATE_DIR)
_sessions = _load_sessions()

# ── Login rate limiter ──────────────────────────────────────────────────────
_login_attempts = {}  # ip -> [timestamp, ...]
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW = 60  # seconds

def _check_login_rate(ip: str) -> bool:
    """Return True if the IP is allowed to attempt login."""
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Prune old attempts
    attempts = [t for t in attempts if now - t < _LOGIN_WINDOW]
    _login_attempts[ip] = attempts
    return len(attempts) < _LOGIN_MAX_ATTEMPTS

def _record_login_attempt(ip: str) -> None:
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    attempts.append(now)
    _login_attempts[ip] = attempts


def _signing_key():
    """Return a random signing key, generating and persisting one on first call."""
    key_file = STATE_DIR / '.signing_key'
    try:
        if key_file.exists():
            raw = key_file.read_bytes()
            if len(raw) >= 32:
                return raw[:32]
    except Exception:
        logger.debug("Failed to read or access signing key file, using in-memory key")
    # Generate a new random key
    key = secrets.token_bytes(32)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(key)
        key_file.chmod(0o600)
    except Exception:
        logger.debug("Failed to persist signing key, using in-memory key only")
    return key


def _hash_password(password):
    """PBKDF2-SHA256 with 600k iterations (OWASP recommendation).
    Salt is the persisted random signing key, which is secret and unique per
    installation. This keeps the stored hash format a plain hex string
    (no format change to settings.json) while replacing the predictable
    STATE_DIR-derived salt from the original implementation."""
    salt = _signing_key()
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600_000)
    return dk.hex()


def get_password_hash() -> str | None:
    """Return the active password hash, or None if auth is disabled.
    Priority: env var > settings.json."""
    env_pw = os.getenv('HERMES_WEBUI_PASSWORD', '').strip()
    if env_pw:
        return _hash_password(env_pw)
    settings = load_settings()
    return settings.get('password_hash') or None


def is_auth_enabled() -> bool:
    """True if a password is configured (env var or settings)."""
    return get_password_hash() is not None


def verify_password(plain) -> bool:
    """Verify a plaintext password against the stored hash."""
    expected = get_password_hash()
    if not expected:
        return False
    return hmac.compare_digest(_hash_password(plain), expected)


def _env_truthy(name: str) -> bool:
    return os.getenv(name, '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _api_tokens_file() -> Path:
    override = os.getenv('HERMES_WEBUI_API_TOKENS_FILE', '').strip()
    if override:
        return Path(override).expanduser()
    return _API_TOKENS_FILE


def load_api_token_records() -> list[dict]:
    """Load long-lived API token records from STATE_DIR/api_tokens.json."""
    path = _api_tokens_file()
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding='utf-8'))
        records = data.get('tokens') if isinstance(data, dict) else data
        if not isinstance(records, list):
            return []
        return [record for record in records if isinstance(record, dict)]
    except Exception as e:
        logger.warning("Failed to load API token config from %s: %s", path, e)
        return []


def _hash_api_token(token: str) -> str:
    return 'sha256:' + hashlib.sha256(token.encode()).hexdigest()


def _token_record_enabled(record: dict) -> bool:
    value = record.get('enabled', True)
    if isinstance(value, str):
        return value.strip().lower() not in {'0', 'false', 'no', 'off'}
    return bool(value)


def _expires_at_timestamp(value) -> float | None:
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith('Z'):
            raw = raw[:-1] + '+00:00'
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    raise ValueError('unsupported expires_at value')


def _token_record_expired(record: dict) -> bool:
    try:
        expires_at = _expires_at_timestamp(record.get('expires_at'))
    except Exception:
        return True
    return expires_at is not None and time.time() > expires_at


def _normalize_origin(value: str | None) -> str:
    return str(value or '').strip().rstrip('/').lower()


def _token_origin_allowed(record: dict, origin: str | None) -> bool:
    allowed = record.get('allowed_origins')
    if not allowed:
        return True
    if isinstance(allowed, str):
        allowed_values = [allowed]
    elif isinstance(allowed, list):
        allowed_values = allowed
    else:
        return False
    normalized = {_normalize_origin(item) for item in allowed_values if str(item or '').strip()}
    if '*' in normalized:
        return True
    request_origin = _normalize_origin(origin)
    if not request_origin:
        return True
    return request_origin in normalized


def verify_api_token(plain_token, origin: str | None = None) -> dict | None:
    """Return the matching token record, or None when the token is invalid."""
    if not isinstance(plain_token, str) or not plain_token:
        return None
    candidate_hash = _hash_api_token(plain_token)
    for record in load_api_token_records():
        if not _token_record_enabled(record) or _token_record_expired(record):
            continue
        expected = str(record.get('token_hash') or '').strip().lower()
        if not expected.startswith('sha256:'):
            continue
        if hmac.compare_digest(candidate_hash, expected) and _token_origin_allowed(record, origin):
            return record
    return None


def create_session() -> str:
    """Create a new auth session. Returns signed cookie value."""
    token = secrets.token_hex(32)
    _sessions[token] = time.time() + SESSION_TTL
    _save_sessions(_sessions)
    sig = hmac.new(_signing_key(), token.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{token}.{sig}"


def _prune_expired_sessions():
    """Remove all expired session entries to prevent unbounded memory growth."""
    now = time.time()
    expired = [t for t, exp in _sessions.items() if now > exp]
    if expired:
        for token in expired:
            _sessions.pop(token, None)
        _save_sessions(_sessions)


def verify_session(cookie_value) -> bool:
    """Verify a signed session cookie. Returns True if valid and not expired."""
    if not cookie_value or '.' not in cookie_value:
        return False
    _prune_expired_sessions()  # lazy cleanup on every verification attempt
    token, sig = cookie_value.rsplit('.', 1)
    expected_sig = hmac.new(_signing_key(), token.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected_sig):
        return False
    expiry = _sessions.get(token)
    if not expiry or time.time() > expiry:
        _sessions.pop(token, None)
        return False
    return True


def invalidate_session(cookie_value) -> None:
    """Remove a session token."""
    if cookie_value and '.' in cookie_value:
        token = cookie_value.rsplit('.', 1)[0]
        if token in _sessions:
            _sessions.pop(token, None)
            _save_sessions(_sessions)


def parse_cookie(handler) -> str | None:
    """Extract the auth cookie from the request headers."""
    cookie_header = handler.headers.get('Cookie', '')
    if not cookie_header:
        return None
    cookie = http.cookies.SimpleCookie()
    try:
        cookie.load(cookie_header)
    except http.cookies.CookieError:
        return None
    morsel = cookie.get(COOKIE_NAME)
    return morsel.value if morsel else None


def is_removed_webui_path(path: str) -> bool:
    """Return True for removed built-in WebUI paths that should reach JSON 410."""
    return path in REMOVED_WEBUI_PATHS or path.startswith(REMOVED_WEBUI_PREFIXES)


def check_auth(handler, parsed) -> bool:
    """Check if request is authorized. Returns True if OK.
    If not authorized, sends JSON 401 and returns False."""
    if not is_auth_enabled():
        return True
    # Public paths don't require auth.
    if parsed.path in PUBLIC_PATHS or is_removed_webui_path(parsed.path):
        return True
    # Check session cookie
    cookie_val = parse_cookie(handler)
    if cookie_val and verify_session(cookie_val):
        return True
    # Not authorized
    if parsed.path.startswith('/api/'):
        from api.helpers import send_cors_headers

        handler.send_response(401)
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Cache-Control', 'no-store')
        send_cors_headers(handler)
        handler.end_headers()
        handler.wfile.write(b'{"error":"Authentication required"}')
    else:
        from api.helpers import send_cors_headers

        handler.send_response(401)
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Cache-Control', 'no-store')
        send_cors_headers(handler)
        handler.end_headers()
        handler.wfile.write(b'{"error":"Authentication required"}')
    return False


def set_auth_cookie(handler, cookie_value) -> None:
    """Set the auth cookie on the response."""
    cookie = http.cookies.SimpleCookie()
    cookie[COOKIE_NAME] = cookie_value
    cookie[COOKIE_NAME]['httponly'] = True
    same_site = os.getenv('HERMES_WEBUI_COOKIE_SAMESITE', '').strip()
    same_site = {'lax': 'Lax', 'strict': 'Strict', 'none': 'None'}.get(same_site.lower(), 'Lax')
    cookie[COOKIE_NAME]['samesite'] = same_site
    cookie[COOKIE_NAME]['path'] = '/'
    cookie[COOKIE_NAME]['max-age'] = str(SESSION_TTL)
    # Set Secure flag when connection is HTTPS
    if (
        _env_truthy('HERMES_WEBUI_COOKIE_SECURE')
        or same_site == 'None'
        or getattr(handler.request, 'getpeercert', None) is not None
        or handler.headers.get('X-Forwarded-Proto', '') == 'https'
    ):
        cookie[COOKIE_NAME]['secure'] = True
    handler.send_header('Set-Cookie', cookie[COOKIE_NAME].OutputString())


def clear_auth_cookie(handler) -> None:
    """Clear the auth cookie on the response."""
    cookie = http.cookies.SimpleCookie()
    cookie[COOKIE_NAME] = ''
    cookie[COOKIE_NAME]['httponly'] = True
    cookie[COOKIE_NAME]['path'] = '/'
    cookie[COOKIE_NAME]['max-age'] = '0'
    handler.send_header('Set-Cookie', cookie[COOKIE_NAME].OutputString())

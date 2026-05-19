import os
import re as _re


def _normalize_host_port(value: str) -> tuple[str, str | None]:
    """Split a host or host:port string into (hostname, port|None).
    Handles IPv6 bracket notation, e.g. [::1]:8080."""
    value = value.strip().lower()
    if not value:
        return '', None
    if value.startswith('['):
        end = value.find(']')
        if end != -1:
            host = value[1:end]
            rest = value[end + 1 :]
            if rest.startswith(':') and rest[1:].isdigit():
                return host, rest[1:]
            return host, None
    if value.count(':') == 1:
        host, port = value.rsplit(':', 1)
        if port.isdigit():
            return host, port
    return value, None


def _ports_match(origin_scheme: str, origin_port: str | None, allowed_port: str | None) -> bool:
    """Return True when two ports should be considered equivalent, scheme-aware.

    Treats an absent port as the scheme default: port 80 for http, port 443 for https.
    Port 80 is NOT treated as equivalent to 443 (different protocols = different origins).
    """
    if origin_port == allowed_port:
        return True
    # Determine the default port for the origin's scheme
    default = '443' if origin_scheme == 'https' else '80'
    if not origin_port and allowed_port == default:
        return True
    if not allowed_port and origin_port == default:
        return True
    return False


def _allowed_public_origins() -> set[str]:
    """Parse HERMES_WEBUI_ALLOWED_ORIGINS env var (comma-separated) into a set.

    Each entry must include the scheme, e.g. https://myapp.example.com:8000.
    Entries without a scheme are silently skipped and a warning is printed.
    """
    raw = os.getenv('HERMES_WEBUI_ALLOWED_ORIGINS', '')
    result = set()
    for value in raw.split(','):
        value = value.strip().rstrip('/').lower()
        if not value:
            continue
        if not (value.startswith('http://') or value.startswith('https://')):
            import sys
            print(
                f"[webui] WARNING: HERMES_WEBUI_ALLOWED_ORIGINS entry {value!r} is missing "
                f"the scheme (expected https://hostname or http://hostname). Entry ignored.",
                flush=True, file=sys.stderr,
            )
            continue
        result.add(value)
    return result


def _env_truthy(name: str) -> bool:
    return os.getenv(name, '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _check_csrf(handler) -> bool:
    """Reject cross-origin POST requests. Returns True if OK."""
    if _env_truthy('HERMES_WEBUI_CORS_ALLOW_ALL'):
        return True
    origin = handler.headers.get("Origin", "")
    referer = handler.headers.get("Referer", "")
    host = handler.headers.get("Host", "")
    if not origin and not referer:
        return True  # non-browser clients (curl, agent) have no Origin
    target = origin or referer
    # Extract host:port from origin/referer
    m = _re.match(r"^https?://([^/]+)", target)
    if not m:
        return False
    origin_host = m.group(1)
    origin_scheme = m.group(0).split('://')[0].lower()  # 'http' or 'https'
    origin_name, origin_port = _normalize_host_port(origin_host)
    # Check against explicitly allowed public origins (env var)
    origin_value = m.group(0).rstrip('/').lower()
    if origin_value in _allowed_public_origins():
        return True
    # Allow same-origin: check Host, X-Forwarded-Host (reverse proxy), and
    # X-Real-Host against the origin. Reverse proxies (Caddy, nginx) set
    # X-Forwarded-Host to the client's original Host header.
    allowed_hosts = [
        h.strip()
        for h in [
            host,
            handler.headers.get("X-Forwarded-Host", ""),
            handler.headers.get("X-Real-Host", ""),
        ]
        if h.strip()
    ]
    for allowed in allowed_hosts:
        allowed_name, allowed_port = _normalize_host_port(allowed)
        if origin_name == allowed_name and _ports_match(origin_scheme, origin_port, allowed_port):
            return True
    return False

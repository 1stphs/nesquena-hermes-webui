from urllib.parse import parse_qs


def _profiles_match(row_profile, active_profile) -> bool:
    """Return True if a session/project row's profile matches the active profile.

    Treats both the literal alias 'default' and any renamed-root display name
    (per _is_root_profile) as equivalent, so legacy rows tagged 'default'
    still surface when the user has renamed the root profile to e.g. 'kinni',
    and vice versa.

    A row with no profile (`None` or empty string) is treated as belonging to
    the root profile -- that's the convention used by the legacy backfill at
    api/models.py::all_sessions, and matches the default seen in
    `static/sessions.js` (`S.activeProfile||'default'`).
    """
    from api.profiles import _is_root_profile

    row = row_profile or 'default'
    active = active_profile or 'default'
    if row == active:
        return True
    # Cross-alias the renamed root.
    if _is_root_profile(row) and _is_root_profile(active):
        return True
    return False


def _all_profiles_query_flag(parsed_url) -> bool:
    """Return True if the request URL has `?all_profiles=1` (or true/yes).

    Centralizes the opt-in parsing so /api/sessions and /api/projects use
    the same shape. Accepts 1/true/yes (case-insensitive) for ergonomics.
    """
    qs = parse_qs(parsed_url.query)
    raw = qs.get('all_profiles', [''])[0].strip().lower()
    return raw in ('1', 'true', 'yes', 'on')


def _requested_sessions_profile(parsed_url) -> str | None:
    """Return the optional profile override for /api/sessions."""
    qs = parse_qs(parsed_url.query)
    requested_profile = qs.get('hermes_profile', [''])[0].strip()
    if not requested_profile:
        return None
    from api.profiles import _PROFILE_ID_RE
    if requested_profile != 'default' and not _PROFILE_ID_RE.fullmatch(requested_profile):
        raise ValueError('invalid profile')
    return requested_profile


"""Helpers for reading request headers consistently."""

from __future__ import annotations

from collections.abc import Mapping


def header_value(headers: Mapping[str, str], *names: str) -> str:
    """First non-empty header value among candidate names, trimmed (case-insensitive)."""
    lowered = {str(k).lower(): v for k, v in headers.items()}
    for name in names:
        value = lowered.get(str(name).lower())
        if value:
            trimmed = str(value).strip()
            if trimmed:
                return trimmed
    return ""


# Cross-subdomain session cookie name (2026-09-07: the admin panel
# disappeared when moving between algorand.pxke.me/x402.pxke.me/
# algorand-registry.pxke.me because the session token lived only in
# localStorage, which is per-origin -- see auth/api/routes.py's
# _session_cookie_header for where this is set, config.py's
# session_cookie_domain for the Domain= it's scoped to).
SESSION_COOKIE_NAME = "wallet_session"


def _cookie_value(headers: Mapping[str, str], name: str) -> str:
    """One cookie's value from the raw Cookie header, or "" if absent/malformed."""
    raw = header_value(headers, "cookie")
    if not raw:
        return ""
    for part in raw.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value.strip()
    return ""


def session_token(headers: Mapping[str, str]) -> str:
    """Session token from x-session-token (any casing), falling back to the wallet_session cookie for a cross-subdomain session (2026-09-07)."""
    header = header_value(headers, "x-session-token")
    if header:
        return header
    return _cookie_value(headers, SESSION_COOKIE_NAME)


def client_ip(headers: Mapping[str, str]) -> str:
    """Real client IP for rate limiting, resistant to header spoofing.

    Trust X-Real-IP first: nginx sets it from $remote_addr and overwrites any
    client-supplied value (see deploy/nginx). X-Forwarded-For is NOT safe to
    read left-to-right — nginx's proxy_add_x_forwarded_for prepends the
    client's own XFF, so its first element is attacker-controlled and would
    hand every spoofed value its own rate-limit bucket. Fall back to the LAST
    XFF hop (the one appended by our proxy) only when X-Real-IP is absent
    (e.g. local dev).

    Returns "" when neither header is present; callers decide whether an
    unattributable request is rate-limited or waved through.
    """
    real_ip = header_value(headers, "x-real-ip")
    if real_ip:
        return real_ip
    xff = header_value(headers, "x-forwarded-for")
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    return parts[-1] if parts else ""

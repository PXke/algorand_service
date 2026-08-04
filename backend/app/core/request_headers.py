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


def session_token(headers: Mapping[str, str]) -> str:
    """Session token from x-session-token (any casing)."""
    return header_value(headers, "x-session-token")

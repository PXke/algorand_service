"""Normalize Robyn/Werkzeug-style query values to plain strings."""

from __future__ import annotations

from typing import Any


def query_param(raw: Any, default: str = "") -> str:
    """Return a single trimmed string from a query value.

    Some frameworks surface duplicate keys or MultiDict entries as lists;
    callers must not assume ``.strip()`` is safe on the raw value."""
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else default
    return str(raw if raw is not None else default).strip()

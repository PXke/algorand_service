"""Shared helpers for excluding owner/admin traffic from analytics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_OPT_OUT_COOKIE = "pxke_no_track=1"


def tracking_opted_out_from_cookie(cookie_header: str) -> bool:
    """True when the browser set the admin-wallet analytics opt-out cookie."""
    return _OPT_OUT_COOKIE in (cookie_header or "")


def _cookie_from_headers(headers: Mapping[str, Any]) -> str:
    return str(headers.get("cookie") or headers.get("Cookie") or "")


def tracking_opted_out_from_headers(headers: Mapping[str, Any]) -> bool:
    """True when the request headers carry the admin-wallet analytics opt-out cookie."""
    return tracking_opted_out_from_cookie(_cookie_from_headers(headers))

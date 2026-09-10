"""Preview-mode trigger + rate limit for the shared x402 payment gate.

Preview lets an agent see the SHAPE of a paid response — same JSON keys, real
values redacted — without paying and without this marketplace attempting a
facilitator call at all. See guard.require_payment's `preview` kwarg for the
bypass itself and paid_request.require_paid_request's `preview` kwarg for how
a route wires the two together (worked example: x402_catalog's x402_ping).

Query-param trigger (`?preview=true`), the same convention this codebase
uses for free/paid distinctions on GET routes. A route opts a caller INTO
preview by calling `preview_requested`
itself and passing the result to `require_paid_request(..., preview=...)` —
a route that never calls this is completely unaffected, preview or not.

Preview never touches the facilitator or the settlement ledger, but it is
still a real request this backend serves for free, so it gets its own
free-endpoint abuse gate (CLAUDE.md section 9: rate limit every free endpoint
per wallet and per IP) — same per-IP shape and same fail-open policy as the
catalog's own rate limits (x402_catalog/services/rate_limit.py): a Redis
blip must not take a preview surface offline, and preview has no money on
the line to protect at the cost of availability the way promo redemption
does (see promo.py's fail-CLOSED for the contrast and why).
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.core.http import Request
from app.core.query_params import query_param
from app.core.rate_limit import incr_with_expiry
from app.core.request_headers import client_ip

logger = logging.getLogger(__name__)

_RATE_LIMIT_PREFIX = "algorand:x402:preview_rl:"
_RATE_LIMIT_WINDOW_SECONDS = 3600
_TRUE_VALUES = {"1", "true", "yes"}


def preview_requested(request: Request) -> bool:
    """True when the caller asked for `?preview=true` (or `1`/`yes`), case-insensitive."""
    raw = query_param(request.query_params.get("preview", "")).lower()
    return raw in _TRUE_VALUES


def preview_rate_limited(request: Request) -> bool:
    """True when this IP has exceeded the hourly preview budget.

    Fails OPEN (a Redis failure reads as "not limited"), same reasoning as
    every other free-read rate limit in this codebase: a Redis hiccup must
    not take a free surface offline. An unattributable request (no
    X-Real-IP / X-Forwarded-For, i.e. local dev) is not limited — with no key
    to bucket on, every such caller would share one counter and starve each
    other, same as catalog_rate_limited.
    """
    ip = client_ip(request.headers)
    if not ip:
        return False
    count = incr_with_expiry(f"{_RATE_LIMIT_PREFIX}{ip}", window_seconds=_RATE_LIMIT_WINDOW_SECONDS)
    if count is None:
        return False
    return count > settings.x402_preview_rate_limit_per_hour

"""Per-IP rate limit for the free registry submit endpoint (design doc section 3.2 gate 2).

Same shared primitive x402_features/x402_directory reach for -- own key
prefix and own setting, so this product's budget is tunable independently
of every other free endpoint in the codebase.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.http import Request
from app.core.rate_limit import incr_with_expiry
from app.core.request_headers import client_ip

_SUBMIT_KEY_PREFIX = "algorand:ecosystem:submit_rl:"
_READ_KEY_PREFIX = "algorand:ecosystem:read_rl:"
_WINDOW_SECONDS = 3600


def _over_hourly_budget(request: Request, *, key_prefix: str, limit: int) -> bool:
    ip = client_ip(request.headers)
    if not ip:
        return False
    count = incr_with_expiry(f"{key_prefix}{ip}", window_seconds=_WINDOW_SECONDS)
    if count is None:
        return False
    return count > limit


def submit_rate_limited(request: Request) -> bool:
    """Return True when this IP has exceeded the hourly submit budget.

    Fails OPEN (a Redis failure -- incr_with_expiry returning None, already
    logged there -- reads as "not limited"): a Redis hiccup must not take a
    free, anonymous submission path offline (CLAUDE.md section 2.9).

    An unattributable request (no X-Real-IP and no X-Forwarded-For, i.e.
    local dev) is not limited: with no key to bucket on, every such caller
    would share one counter and starve each other.
    """
    return _over_hourly_budget(
        request, key_prefix=_SUBMIT_KEY_PREFIX, limit=settings.ecosystem_submit_rate_limit_per_hour
    )


def read_rate_limited(request: Request) -> bool:
    """Return True when this IP has exceeded the hourly free-read budget (list/detail/badge). Same fail-open/unattributable rules as submit_rate_limited."""
    return _over_hourly_budget(
        request, key_prefix=_READ_KEY_PREFIX, limit=settings.ecosystem_read_rate_limit_per_hour
    )

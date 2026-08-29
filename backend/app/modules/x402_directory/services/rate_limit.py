"""Per-IP rate limit for the free search endpoint."""

from __future__ import annotations

from app.core.config import settings
from app.core.http import Request
from app.core.rate_limit import incr_with_expiry
from app.core.request_headers import client_ip

_KEY_PREFIX = "algorand:x402:search_rl:"
_WINDOW_SECONDS = 3600


def search_rate_limited(request: Request) -> bool:
    """Return True when this IP has exceeded the hourly search budget.

    Fails OPEN (a Redis failure -- incr_with_expiry returning None -- reads as
    "not limited"): a Redis hiccup must not take the directory's free read path
    offline. The paid /x402/list route is unaffected either way — it is gated
    by payment, not by this.

    An unattributable request (no X-Real-IP and no X-Forwarded-For, i.e. local
    dev) is not limited: with no key to bucket on, every such caller would share
    one counter and starve each other.
    """
    ip = client_ip(request.headers)
    if not ip:
        return False
    count = incr_with_expiry(f"{_KEY_PREFIX}{ip}", window_seconds=_WINDOW_SECONDS)
    if count is None:
        return False
    return count > settings.x402_search_rate_limit_per_hour

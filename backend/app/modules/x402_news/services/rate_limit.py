"""Per-IP rate limit for the free headline-list endpoint."""

from __future__ import annotations

from app.core.config import settings
from app.core.http import Request
from app.core.rate_limit import incr_with_expiry
from app.core.request_headers import client_ip

_KEY_PREFIX = "algorand:x402:news_rl:"
_WINDOW_SECONDS = 3600


def news_list_rate_limited(request: Request) -> bool:
    """Return True when this IP has exceeded the hourly headline-list budget.

    Fails OPEN (a Redis failure -- incr_with_expiry returning None -- reads as
    "not limited"): a Redis hiccup must not take the free headline list
    offline. The paid routes are unaffected either way -- they are gated by
    payment, not by this.

    An unattributable request (no X-Real-IP and no X-Forwarded-For, i.e. local
    dev) is not limited: with no key to bucket on, every such caller would
    share one counter and starve each other.

    Its own key prefix and its own setting, separate from the other x402
    products' free-read budgets, so exhausting one does not lock a caller out
    of another.
    """
    ip = client_ip(request.headers)
    if not ip:
        return False
    count = incr_with_expiry(f"{_KEY_PREFIX}{ip}", window_seconds=_WINDOW_SECONDS)
    if count is None:
        return False
    return count > settings.x402_news_rate_limit_per_hour

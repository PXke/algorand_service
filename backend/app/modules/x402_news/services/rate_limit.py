"""Per-IP rate limits for the News Engine's free reads.

Two counters share one hourly budget setting (x402_news_rate_limit_per_hour)
but count separately: the free headline list, and the pre-gate article
resolution on the paid article route (which reads the full article before
any payment, so it needs its own cap on unpaid traffic).
"""

from __future__ import annotations

from app.core.config import settings
from app.core.http import Request
from app.core.rate_limit import incr_with_expiry
from app.core.request_headers import client_ip

_LIST_KEY_PREFIX = "algorand:x402:news_rl:"
_ARTICLE_KEY_PREFIX = "algorand:x402:news_article_rl:"
_WINDOW_SECONDS = 3600


def _rate_limited(request: Request, key_prefix: str) -> bool:
    """True when this IP has exceeded the hourly budget under `key_prefix`.

    Fails OPEN (a Redis failure -- incr_with_expiry returning None -- reads as
    "not limited"): a Redis hiccup must not take a free read offline.

    An unattributable request (no X-Real-IP and no X-Forwarded-For, i.e. local
    dev) is not limited: with no key to bucket on, every such caller would
    share one counter and starve each other.

    Its own key prefixes and its own setting, separate from the other x402
    products' free-read budgets, so exhausting one does not lock a caller out
    of another.
    """
    ip = client_ip(request.headers)
    if not ip:
        return False
    count = incr_with_expiry(f"{key_prefix}{ip}", window_seconds=_WINDOW_SECONDS)
    if count is None:
        return False
    return count > settings.x402_news_rate_limit_per_hour


def news_list_rate_limited(request: Request) -> bool:
    """True when this IP is over the hourly budget for the free headline list."""
    return _rate_limited(request, _LIST_KEY_PREFIX)


def news_article_rate_limited(request: Request) -> bool:
    """True when this IP is over the hourly budget for pre-gate article resolution.

    Counted on every hit of the paid article route, paid or not: it is the
    unpaid pre-gate read (full article plus view counters) that this bounds.
    """
    return _rate_limited(request, _ARTICLE_KEY_PREFIX)

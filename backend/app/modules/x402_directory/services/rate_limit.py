"""Per-IP rate limit for the free search endpoint.

Same Redis incr/expire shape the contact form uses, reading the client IP
through the shared spoof-resistant helper rather than a third private copy.
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.core.http import Request
from app.core.redis_client import get_redis
from app.core.request_headers import client_ip

logger = logging.getLogger(__name__)

_KEY_PREFIX = "algorand:x402:search_rl:"


def search_rate_limited(request: Request) -> bool:
    """Return True when this IP has exceeded the hourly search budget.

    Fails OPEN, like the contact form's limit: a Redis hiccup must not take the
    directory's free read path offline. The paid /x402/list route is unaffected
    either way — it is gated by payment, not by this.

    An unattributable request (no X-Real-IP and no X-Forwarded-For, i.e. local
    dev) is not limited: with no key to bucket on, every such caller would share
    one counter and starve each other.
    """
    ip = client_ip(request.headers)
    if not ip:
        return False
    try:
        client = get_redis()
        key = f"{_KEY_PREFIX}{ip}"
        count = client.incr(key)
        if int(count) == 1:
            client.expire(key, 3600)
        return int(count) > settings.x402_search_rate_limit_per_hour
    except Exception:
        logger.warning("x402 search rate-limit check failed; failing open", exc_info=True)
        return False

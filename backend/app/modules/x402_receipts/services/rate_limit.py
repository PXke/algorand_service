"""Per-IP rate limit for the free receipt-read endpoint.

Same shape as x402_directory's own search_rate_limited (CLAUDE.md section 9:
rate limit every free endpoint per IP), under its own key/setting so it
never shares (or steals) budget from the directory's.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.http import Request
from app.core.rate_limit import incr_with_expiry
from app.core.request_headers import client_ip

_KEY_PREFIX = "algorand:x402:receipts_rl:"
_WINDOW_SECONDS = 3600


def receipts_rate_limited(request: Request) -> bool:
    """Return True when this IP has exceeded the hourly receipt-read budget.

    Fails OPEN (a Redis failure -- incr_with_expiry returning None -- reads
    as "not limited"): a Redis hiccup must not take this free read offline.

    An unattributable request (no X-Real-IP and no X-Forwarded-For, i.e.
    local dev) is not limited: with no key to bucket on, every such caller
    would share one counter and starve each other.
    """
    ip = client_ip(request.headers)
    if not ip:
        return False
    count = incr_with_expiry(f"{_KEY_PREFIX}{ip}", window_seconds=_WINDOW_SECONDS)
    if count is None:
        return False
    return count > settings.x402_receipts_rate_limit_per_hour

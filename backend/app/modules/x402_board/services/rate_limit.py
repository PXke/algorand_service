"""Per-IP rate limits for the free board-read and click-through endpoints."""

from __future__ import annotations

from app.core.config import settings
from app.core.http import Request
from app.core.rate_limit import incr_with_expiry
from app.core.request_headers import client_ip

_KEY_PREFIX = "algorand:x402:board_rl:"
_CLICK_KEY_PREFIX = "algorand:x402:board_click_rl:"
_WINDOW_SECONDS = 3600


def _over_budget(request: Request, *, prefix: str, per_hour: int) -> bool:
    ip = client_ip(request.headers)
    if not ip:
        return False
    count = incr_with_expiry(f"{prefix}{ip}", window_seconds=_WINDOW_SECONDS)
    if count is None:
        return False
    return count > per_hour


def board_click_rate_limited(request: Request) -> bool:
    """Return True when this IP has exceeded the hourly click-through budget.

    Its own counter (own key prefix) so a burst of clicks cannot lock an IP
    out of reading the board, but the SAME hourly budget setting as the feed:
    adding a setting is outside this change's scope (config.py), and the two
    are the same order of magnitude anyway. Fails open like the feed's.
    """
    return _over_budget(
        request, prefix=_CLICK_KEY_PREFIX, per_hour=settings.x402_board_rate_limit_per_hour
    )


def board_read_rate_limited(request: Request) -> bool:
    """Return True when this IP has exceeded the hourly board-read budget.

    Fails OPEN (a Redis failure -- incr_with_expiry returning None -- reads as
    "not limited"): a Redis hiccup must not take the board's free read path
    offline. The paid POST route is unaffected either way -- it is gated by
    payment, not by this.

    An unattributable request (no X-Real-IP and no X-Forwarded-For, i.e. local
    dev) is not limited: with no key to bucket on, every such caller would
    share one counter and starve each other.

    Its own key prefix and its own setting, not the directory's: the two
    endpoints are separate products whose budgets should be tunable apart, and
    sharing a counter would let board reads exhaust a caller's search budget.
    """
    return _over_budget(
        request, prefix=_KEY_PREFIX, per_hour=settings.x402_board_rate_limit_per_hour
    )

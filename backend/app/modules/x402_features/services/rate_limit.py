"""Per-IP rate limits for the feature board's two free endpoints (browse, file)."""

from __future__ import annotations

from app.core.config import settings
from app.core.http import Request
from app.core.rate_limit import incr_with_expiry
from app.core.request_headers import client_ip

_READ_KEY_PREFIX = "algorand:x402:features_rl:"
_SUBMIT_KEY_PREFIX = "algorand:x402:features_submit_rl:"
_WINDOW_SECONDS = 3600


def _over_hourly_budget(request: Request, *, key_prefix: str, limit: int) -> bool:
    """Count one hit for this IP under `key_prefix` and report whether it exceeds `limit`.

    Fails OPEN (a Redis failure -- incr_with_expiry returning None, which has
    already logged a warning -- reads as "not limited"): a Redis hiccup must
    not take a free path offline.

    An unattributable request (no X-Real-IP and no X-Forwarded-For, i.e. local
    dev) is not limited: with no key to bucket on, every such caller would
    share one counter and starve each other.
    """
    ip = client_ip(request.headers)
    if not ip:
        return False
    count = incr_with_expiry(f"{key_prefix}{ip}", window_seconds=_WINDOW_SECONDS)
    if count is None:
        return False
    return count > limit


def features_read_rate_limited(request: Request) -> bool:
    """Return True when this IP has exceeded the hourly feature-browse budget.

    The two paid routes are unaffected either way -- they are gated by
    payment, not by this, and the paid demand read is deliberately NOT counted
    here: someone who has paid for a page must not be refused it because they
    browsed the free feed too much first.

    Its own key prefix and its own setting, not the directory's or the board's:
    these are separate products whose budgets should be tunable apart, and
    sharing a counter would let one product's reads exhaust another's.
    """
    return _over_hourly_budget(
        request,
        key_prefix=_READ_KEY_PREFIX,
        limit=settings.x402_features_rate_limit_per_hour,
    )


def features_submit_rate_limited(request: Request) -> bool:
    """Return True when this IP has exceeded the hourly free-filing budget.

    Filing is free and anonymous, so this per-IP budget is the only brake on
    a script flooding the board with requests. It is counted under its own
    key, separate from the browse budget: a caller who reads the board a lot
    should still be able to file, and one who files a lot should still be
    able to read.
    """
    return _over_hourly_budget(
        request,
        key_prefix=_SUBMIT_KEY_PREFIX,
        limit=settings.x402_features_submit_rate_limit_per_hour,
    )

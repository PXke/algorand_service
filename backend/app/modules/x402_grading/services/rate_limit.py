"""Per-IP rate limits for the free graded-listing index and the free per-URL summary."""

from __future__ import annotations

from app.core.config import settings
from app.core.http import Request
from app.core.rate_limit import incr_with_expiry
from app.core.request_headers import client_ip

_KEY_PREFIX = "algorand:x402:grading_rl:"
_SUMMARY_KEY_PREFIX = "algorand:x402:grading_summary_rl:"
_WINDOW_SECONDS = 3600


def _over_budget(request: Request, *, prefix: str) -> bool:
    ip = client_ip(request.headers)
    if not ip:
        return False
    count = incr_with_expiry(f"{prefix}{ip}", window_seconds=_WINDOW_SECONDS)
    if count is None:
        return False
    return count > settings.x402_grading_rate_limit_per_hour


def grading_summary_rate_limited(request: Request) -> bool:
    """Return True when this IP has exceeded the hourly per-URL summary budget.

    Its own counter (own key prefix) so summary polling cannot lock an IP out
    of the index, but the SAME hourly budget setting: adding a setting is
    outside this change's scope (config.py). Fails open like the index's.
    """
    return _over_budget(request, prefix=_SUMMARY_KEY_PREFIX)


def grading_index_rate_limited(request: Request) -> bool:
    """Return True when this IP has exceeded the hourly graded-index budget.

    Fails OPEN (a Redis failure -- incr_with_expiry returning None -- reads as
    "not limited"): a Redis hiccup must not take the free index offline. The
    two paid routes are unaffected either way -- they are gated by payment, not
    by this.

    An unattributable request (no X-Real-IP and no X-Forwarded-For, i.e. local
    dev) is not limited: with no key to bucket on, every such caller would
    share one counter and starve each other.

    Its own key prefix and its own setting, not the directory's, the board's or
    the feature board's: these are separate products whose budgets should be
    tunable apart, and sharing a counter would let index reads exhaust a
    caller's search budget.
    """
    return _over_budget(request, prefix=_KEY_PREFIX)

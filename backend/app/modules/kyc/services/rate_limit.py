"""Per-IP and per-wallet rate limits for the two free KYC endpoints.

Same shape and same primitive as the other x402 modules' rate limiters
(app/core/rate_limit.py's incr_with_expiry), under this module's own key
prefix so a KYC flood cannot exhaust a caller's directory/board/search
budget and vice versa.

Enroll carries a second, wallet-keyed limit that the other modules have no
equivalent of, because its cost driver is different: every enrollment fires
two outbound requests to a public indexer plus a Cassandra write, and wallet
addresses are free to generate, so per-IP alone bounds the wrong quantity.

Every limiter here fails OPEN (incr_with_expiry returns None on a Redis
error, which reads as "not limited") -- a Redis hiccup must not take free
self-service enrollment offline. CLAUDE.md section 2.9.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.http import Request
from app.core.rate_limit import incr_with_expiry
from app.core.request_headers import client_ip

_CONSENT_KEY_PREFIX = "algorand:kyc:consent_rl:"
_ENROLL_IP_KEY_PREFIX = "algorand:kyc:enroll_ip_rl:"
_ENROLL_WALLET_KEY_PREFIX = "algorand:kyc:enroll_wallet_rl:"

_HOUR_SECONDS = 3600
_DAY_SECONDS = 86400


def _ip_limited(request: Request, *, prefix: str, window_seconds: int, limit: int) -> bool:
    """Whether this request's client IP has exceeded `limit` hits in the window.

    An unattributable request (no X-Real-IP and no X-Forwarded-For, i.e. local
    dev) is not limited: with no key to bucket on, every such caller would
    share one counter and starve each other.
    """
    ip = client_ip(request.headers)
    if not ip:
        return False
    count = incr_with_expiry(f"{prefix}{ip}", window_seconds=window_seconds)
    if count is None:
        return False
    return count > limit


def consent_message_rate_limited(request: Request) -> bool:
    """Return True when this IP has exceeded the hourly consent-message budget."""
    return _ip_limited(
        request,
        prefix=_CONSENT_KEY_PREFIX,
        window_seconds=_HOUR_SECONDS,
        limit=settings.kyc_consent_rate_limit_per_hour,
    )


def enroll_ip_rate_limited(request: Request) -> bool:
    """Return True when this IP has exceeded the hourly enrollment budget."""
    return _ip_limited(
        request,
        prefix=_ENROLL_IP_KEY_PREFIX,
        window_seconds=_HOUR_SECONDS,
        limit=settings.kyc_enroll_rate_limit_per_hour,
    )


def enroll_wallet_rate_limited(wallet_address: str) -> bool:
    """Return True when this wallet has exceeded its daily enrollment budget.

    Keyed on the wallet address itself, which is a bounded 58-character string
    by the time it gets here (EnrollRequest's schema pins the length, and this
    is called after the body has decoded). Daily rather than hourly: after the
    first enrollment every further one only refreshes the same row's signals,
    so there is no legitimate reason to do it often.
    """
    wallet = wallet_address.strip()
    if not wallet:
        return False
    count = incr_with_expiry(f"{_ENROLL_WALLET_KEY_PREFIX}{wallet}", window_seconds=_DAY_SECONDS)
    if count is None:
        return False
    return count > settings.kyc_enroll_wallet_rate_limit_per_day

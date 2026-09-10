"""Per-IP + per-wallet Redis rate limits for x402 storage's free routes.

CLAUDE.md section 9: rate limit every free endpoint per wallet AND per IP.

On every wallet-signature-authenticated route here (list, detail,
delete), the `wallet` query param is an UNAUTHENTICATED CLAIM until
services/auth_service.verify_challenge_signature actually returns True for
it -- so it must never be used to key a rate limit BEFORE that verification
succeeds. Keying on the unproven claim would let an attacker who does not
hold a wallet's key burn that wallet's shared budget with zero proof of key
possession, 429ing the real owner out for free. So: every free route here
(challenge issuance included, where `wallet` is equally unproven) is limited
per-IP unconditionally, and the per-wallet counter is only ever touched
AFTER a caller has actually proven control of that wallet in this same
request.

Every gate fails OPEN on a Redis error: a Redis blip must not take a free
read/auth path offline.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.http import Request
from app.core.rate_limit import incr_with_expiry
from app.core.request_headers import client_ip

_IP_PREFIX = "algorand:x402storage:rl_ip:"
_WALLET_PREFIX = "algorand:x402storage:rl_wallet:"
_WINDOW_SECONDS = 3600


def ip_rate_limited(request: Request) -> bool:
    """True when this IP has exceeded the hourly free-route budget.

    Covers every free route (challenge issuance, list, detail, delete) --
    checked BEFORE anything else on each of them. An unattributable request
    (no X-Real-IP / X-Forwarded-For, i.e. local dev) is not limited: with no
    key to bucket on, every such caller would share one counter and starve
    each other.
    """
    ip = client_ip(request.headers)
    if not ip:
        return False
    count = incr_with_expiry(f"{_IP_PREFIX}{ip}", window_seconds=_WINDOW_SECONDS)
    if count is None:
        return False
    return count > settings.x402_storage_rate_limit_per_hour


def wallet_rate_limited(wallet: str) -> bool:
    """True when this wallet has exceeded the hourly free-authenticated-route budget.

    Call ONLY after verify_challenge_signature has already returned True for
    this wallet in this same request -- see the module docstring for why an
    unproven wallet claim must never reach this counter.
    """
    if not wallet:
        return False
    count = incr_with_expiry(f"{_WALLET_PREFIX}{wallet}", window_seconds=_WINDOW_SECONDS)
    if count is None:
        return False
    return count > settings.x402_storage_rate_limit_per_hour

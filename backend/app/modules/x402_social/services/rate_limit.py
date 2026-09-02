"""Per-IP + per-wallet Redis rate limits for x402 social's free routes (Phase S0).

CLAUDE.md section 9: rate limit every free endpoint per wallet AND per IP.
Every gate here fails OPEN on a Redis error, same convention as
x402_directory's search_rate_limited (app/core/rate_limit.py's own
docstring: fail-open vs fail-closed is a deliberate per-caller choice, not a
blanket policy) -- for the two session-issuance routes specifically this is
also the design doc's own explicit call (section 4.2: "the gate it protects
is itself signature-checked, so open-fail is safe").
"""

from __future__ import annotations

from app.core.config import settings
from app.core.http import Request
from app.core.rate_limit import incr_with_expiry
from app.core.request_headers import client_ip

_READ_IP_PREFIX = "algorand:x402social:read_rl_ip:"
_SESSION_IP_PREFIX = "algorand:x402social:session_rl_ip:"
_SESSION_WALLET_PREFIX = "algorand:x402social:session_rl_wallet:"
_FREE_WRITE_WALLET_PREFIX = "algorand:x402social:free_write_rl_wallet:"
_WINDOW_SECONDS = 3600


def read_rate_limited(request: Request) -> bool:
    """True when this IP has exceeded the hourly free-read budget (GET /agents, GET /agents/{wallet}).

    Per-IP only: a free read has no caller wallet to key on (an
    unattributable request -- no X-Real-IP/X-Forwarded-For, i.e. local dev
    -- is not limited, same reasoning as x402_directory's search_rate_limited:
    with no key to bucket on, every such caller would share one counter and
    starve each other).
    """
    ip = client_ip(request.headers)
    if not ip:
        return False
    count = incr_with_expiry(f"{_READ_IP_PREFIX}{ip}", window_seconds=_WINDOW_SECONDS)
    if count is None:
        return False
    return count > settings.x402_social_read_rate_limit_per_hour


def session_rate_limited(request: Request, *, wallet: str) -> bool:
    """True when this IP OR this wallet has exceeded the hourly session-issuance budget.

    Shared by POST /auth/challenge and POST /auth/session (design doc
    section 4.2 treats them as one issuance flow). Checked on both axes
    independently -- either one tripping is enough to refuse -- so a wallet
    cannot dodge its own per-wallet budget by rotating IPs, nor can one IP
    dodge the per-IP budget by rotating claimed wallets.
    """
    limit = settings.x402_social_session_rate_limit_per_hour
    ip = client_ip(request.headers)
    if ip:
        count = incr_with_expiry(f"{_SESSION_IP_PREFIX}{ip}", window_seconds=_WINDOW_SECONDS)
        if count is not None and count > limit:
            return True
    if wallet:
        count = incr_with_expiry(
            f"{_SESSION_WALLET_PREFIX}{wallet}", window_seconds=_WINDOW_SECONDS
        )
        if count is not None and count > limit:
            return True
    return False


def free_write_rate_limited(*, wallet: str) -> bool:
    """True when this wallet has exceeded the hourly free-authenticated-write budget.

    PATCH /profile in Phase S0; unfollow/leave/group-mod actions join this
    same budget in later phases (design doc section 1). Keyed by wallet, not
    IP: the caller is already session-authenticated (a valid bearer token)
    by the time this runs, so the wallet is the identity that matters --
    IP-bounding on top would only add false sharing across agents behind the
    same egress IP. Takes no `request`, unlike the other two gates in this
    module, because it has nothing to key an IP off of by design.
    """
    if not wallet:
        return False
    count = incr_with_expiry(f"{_FREE_WRITE_WALLET_PREFIX}{wallet}", window_seconds=_WINDOW_SECONDS)
    if count is None:
        return False
    return count > settings.x402_social_free_write_rate_limit_per_hour

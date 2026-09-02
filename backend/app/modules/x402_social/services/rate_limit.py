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
_SESSION_WALLET_FAIL_PREFIX = "algorand:x402social:session_rl_wallet_fail:"
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


def session_rate_limited(request: Request) -> bool:
    """True when this IP has exceeded the hourly session-issuance budget.

    Shared by POST /auth/challenge and POST /auth/session (design doc
    section 4.2 treats them as one issuance flow). Per-IP ONLY (finding 2,
    2026-security-audit removed the per-wallet axis that used to be checked
    here): the `wallet` in either route's request body is an UNAUTHENTICATED
    claim at this point -- nothing has proven the caller holds that wallet's
    key yet -- so keying a rate limit on it let anyone burn an arbitrary
    victim wallet's shared budget with zero proof of key possession, 429ing
    the real wallet owner out of both routes. See
    session_verification_failed for the replacement per-wallet guard, which
    only counts PROVEN-bad POST /auth/session attempts, checked strictly
    after signature verification.
    """
    ip = client_ip(request.headers)
    if not ip:
        return False
    count = incr_with_expiry(f"{_SESSION_IP_PREFIX}{ip}", window_seconds=_WINDOW_SECONDS)
    if count is None:
        return False
    return count > settings.x402_social_session_rate_limit_per_hour


def session_verification_failed(*, wallet: str) -> bool:
    """Record one FAILED POST /auth/session signature verification for `wallet`; True if this wallet has now exceeded its hourly failed-verification budget.

    Finding 2, 2026-security-audit: replaces the old blanket per-wallet
    counter that incremented on EVERY attempt regardless of whether the
    caller proved key possession. Call this ONLY after
    verify_challenge_signature has already returned False for this request
    -- a wallet's own successful logins, however frequent, never touch this
    counter (there is nothing to rate-limit about a correctly-proven login),
    and an attacker who does not hold the wallet's key can only ever cause
    FAILURES here, so this counter genuinely bounds repeated bad attempts
    against one wallet name (e.g. signature brute-forcing) without giving
    an attacker any way to consume the real owner's budget through failures
    the owner never made. Combined with session_service's (wallet,
    nonce)-keyed challenge, an attacker's garbage attempts can no longer
    invalidate the owner's real pending challenge either -- this is what
    remains to bound volume.
    """
    if not wallet:
        return False
    count = incr_with_expiry(
        f"{_SESSION_WALLET_FAIL_PREFIX}{wallet}", window_seconds=_WINDOW_SECONDS
    )
    if count is None:
        return False
    return count > settings.x402_social_session_rate_limit_per_hour


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

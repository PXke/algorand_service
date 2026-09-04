"""Two independent rate-limit dimensions for the uptime check: per caller IP and per target.

The owner named both explicitly ("a rate limiting for a given IP/domain is
important"): per-caller-IP alone does not stop a determined caller from
using many IPs/wallets to still flood one victim, and per-target alone does
not stop one IP from being a general nuisance across many low-traffic
targets. See docs/x402-uptime-check-design.md's Rate limiting section for
the full reasoning, including why the two limiters fail differently.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from app.core.config import settings
from app.core.http import Request
from app.core.rate_limit import incr_with_expiry
from app.core.request_headers import client_ip

logger = logging.getLogger(__name__)

_IP_KEY_PREFIX = "algorand:x402:uptime_rl_ip:"
_TARGET_KEY_PREFIX = "algorand:x402:uptime_rl_target:"
_WINDOW_SECONDS = 3600


def target_key_for(normalized_url: str) -> str:
    """The `host[:port]` a normalized URL's real-fetch budget is tracked under."""
    return urlsplit(normalized_url).netloc


def ip_rate_limited(request: Request) -> bool:
    """True when this caller IP is over its hourly budget for uptime-check requests (paid or not).

    Covers the pre-payment surface too (URL validation, a 402 offer lookup)
    — CLAUDE.md section 9: rate limit every free endpoint per IP. Fails
    OPEN on a Redis hiccup (CLAUDE.md invariant 9's default): this limiter
    protects US from one noisy caller, not third parties, so a Redis blip
    should not take the product offline.
    """
    ip = client_ip(request.headers)
    if not ip:
        return False
    count = incr_with_expiry(f"{_IP_KEY_PREFIX}{ip}", window_seconds=_WINDOW_SECONDS)
    if count is None:
        return False
    return count > settings.x402_uptime_rate_limit_per_hour


def target_over_budget(target_key: str) -> bool:
    """True when the real-fetch budget for this target is exhausted this hour, or Redis is unreachable.

    Counts only REAL fetches — a fresh cache hit never calls this (see
    api/routes.py's own product-write flow), so a popular, stable target
    converges to "almost every check is a cache hit" and this limiter only
    bites during genuine flapping/instability, or a burst of first-time
    callers against a brand-new target.

    Fails CLOSED, unlike ip_rate_limited above: this counter IS the DDoS
    defense named by the owner (bounds real outbound traffic to one target
    regardless of how many different callers/wallets pay for a check), so a
    Redis outage must not silently remove the only cap — the same
    fail-closed reasoning x402_scan's own concurrency limiter documents
    (concurrency.py: "the failure mode of failing open is 'allow unlimited
    concurrent 1.5GB containers on a shared prod host' — exactly the DoS
    this module exists to prevent").
    """
    count = incr_with_expiry(f"{_TARGET_KEY_PREFIX}{target_key}", window_seconds=_WINDOW_SECONDS)
    if count is None:
        logger.error(
            "x402 uptime target rate limiter: Redis unreachable, treating target=%s as over "
            "budget (fail closed — this counter is the DDoS defense)",
            target_key,
        )
        return True
    return count > settings.x402_uptime_target_rate_limit_per_hour


__all__ = ["ip_rate_limited", "target_key_for", "target_over_budget"]

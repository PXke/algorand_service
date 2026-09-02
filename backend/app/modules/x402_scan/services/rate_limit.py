"""Per-IP rate limit for the free-ish surface of the scan endpoint.

The scan route itself is paid (payment is its own abuse gate), but CLAUDE.md
section 9 requires every free endpoint rate limited per wallet and per IP --
this covers the pre-payment surface: a caller can trigger a 402 offer lookup
and a URL-validation 400 without ever paying, so those still need a per-IP
cap to stop someone from using this as a free SSRF-probe oracle against
arbitrary hosts.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.http import Request
from app.core.rate_limit import incr_with_expiry
from app.core.request_headers import client_ip

_KEY_PREFIX = "algorand:x402:scan_rl:"
_WINDOW_SECONDS = 3600


def scan_rate_limited(request: Request) -> bool:
    """True when this IP is over the hourly budget for scan requests (paid or not).

    Fails OPEN on a Redis hiccup (CLAUDE.md invariant 9); unattributable
    requests (no IP header, i.e. local dev) are never limited.
    """
    ip = client_ip(request.headers)
    if not ip:
        return False
    count = incr_with_expiry(f"{_KEY_PREFIX}{ip}", window_seconds=_WINDOW_SECONDS)
    if count is None:
        return False
    return count > settings.x402_scan_rate_limit_per_hour

"""Submit-time and re-check liveness, reusing x402_uptime's SSRF-guarded reachability checker.

CLAUDE.md section 3: no new copy of existing logic -- `check_target`
(SSRF-guarded DNS resolution, redirect-limited, body never read) already
does exactly the "cheap liveness probe" design doc section 3.1 asks for,
one module over. Nothing here re-derives the SSRF policy or the HTTP
fetch.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.modules.x402_uptime.services.checker import check_target

# Same bar the crawler's own ecosystem_sync._reachable uses, cited by the
# design doc section 3.1: "< 500 counts as alive". check_target()'s own
# `reachable` flag means only "got a transport-level HTTP response at all"
# (any status, including 5xx) -- this module's ALIVE_STATUS_CEILING applies
# the additional status-code bar on top of that.
ALIVE_STATUS_CEILING = 500


@dataclass(frozen=True)
class LivenessCheck:
    """One liveness check's outcome, always returned, never raised."""

    reachable: bool
    http_status: int


def check_liveness(url: str, *, max_redirects: int = 3) -> LivenessCheck:
    """SSRF-guarded reachability check of `url`. Never raises for a target-side failure."""
    result = check_target(
        url,
        timeout_s=settings.ecosystem_liveness_timeout_seconds,
        max_redirects=max_redirects,
    )
    alive = result.reachable and (
        result.http_status == 0 or result.http_status < ALIVE_STATUS_CEILING
    )
    return LivenessCheck(reachable=alive, http_status=result.http_status)


__all__ = ["ALIVE_STATUS_CEILING", "LivenessCheck", "check_liveness"]

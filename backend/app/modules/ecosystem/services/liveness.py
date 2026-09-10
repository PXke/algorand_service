"""Submit-time and re-check liveness, built on the SSRF-guarded reachability checker.

CLAUDE.md section 3: no new copy of existing logic -- `check_target`
(SSRF-guarded DNS resolution, redirect-limited, body never read) already
does exactly the "cheap liveness probe" design doc section 3.1 asks for,
one module over. Nothing here re-derives the SSRF policy or the HTTP
fetch.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.modules.ecosystem.services.checker import check_target

# check_target()'s own `reachable` flag means only "got a transport-level
# HTTP response at all" -- any status, including 4xx/5xx, counts. That was
# also this module's own bar until 2026-09-08 ("< 500 counts as alive",
# design doc section 3.1) -- too loose in practice: a 404 (page gone), 401/
# 403 (gated), or any other 4xx genuinely means "not a working listing,"
# not "basically fine because it's under 500." Only a real 2xx success
# counts as alive now. check_target already follows redirects internally
# (up to max_redirects) before returning, so the status seen here is
# already the terminal one -- a 3xx surviving to this point means the
# redirect chain never resolved, which is correctly NOT alive either.
ALIVE_STATUS_LOW = 200
ALIVE_STATUS_HIGH = 300  # exclusive upper bound


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
    alive = result.reachable and ALIVE_STATUS_LOW <= result.http_status < ALIVE_STATUS_HIGH
    return LivenessCheck(reachable=alive, http_status=result.http_status)


__all__ = ["ALIVE_STATUS_HIGH", "ALIVE_STATUS_LOW", "LivenessCheck", "check_liveness"]

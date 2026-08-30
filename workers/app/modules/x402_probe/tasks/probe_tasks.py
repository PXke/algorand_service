"""Beat task: probe every live x402 directory listing, unpaid, and refresh the verified badge."""

from __future__ import annotations

from app.celery_app import celery_app
from app.core.config import X402_PROBE_ENABLED
from app.core.redis_lock import single_flight
from app.modules.x402_probe.service import run_probe_sweep


@celery_app.task(name="app.tasks.x402_probe.probe_listed_endpoints")
# One sweep can outrun a 30-minute tick (up to X402_PROBE_MAX_LISTINGS
# sequential 5 s fetches), so overlapping ticks must no-op rather than probe
# the same endpoints twice. Lock TTL pinned to the celery-wide hard time
# limit (>= the soft limit, CLAUDE.md invariant 5); the beat entry sets
# `expires=` to the interval so a stale tick is dropped, not run late.
@single_flight(lambda *_a, **_kw: "x402_probe:sweep", ttl=celery_app.conf.task_time_limit)
def probe_listed_endpoints() -> dict[str, object]:
    """Run one probe sweep. Re-checks X402_PROBE_ENABLED so a manual trigger honours the flag too."""
    if not X402_PROBE_ENABLED:
        return {"status": "skipped", "reason": "x402_probe_disabled"}
    return run_probe_sweep()

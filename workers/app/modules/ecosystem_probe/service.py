"""The registry liveness sweep: read pending+approved entries, probe each, store the result.

Reuses `app.core.net_guard.guarded_get` -- the SAME SSRF-guarded fetch
helper `app.modules.crawler.ecosystem_sync._reachable` already uses for
exactly this "cheap liveness probe" purpose (CLAUDE.md section 3: no new
copy of existing logic). A per-URL failure never aborts the sweep; only
Celery's SoftTimeLimitExceeded propagates (CLAUDE.md invariant 6).

`check_reachable` also runs `is_source_parked_or_expired` (2026-09-08 Fable
review, the "downbad" problem: a wound-down project can still answer a
normal 200 with no content-level check ever catching it) -- reused, not
copied, from `app.modules.newspaper.source_liveness`, which already exists
in this same service/venv for the identical "a normal 200 can still be a
dead domain" shape (root-caused 2026-08-27 on arima.io). Same fail-open
posture: a parking-marker match only ever narrows an already-"alive" result
to "not really," it can never turn a genuine failure into a false positive.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from celery.exceptions import SoftTimeLimitExceeded

from app.core.cassandra import get_cassandra_session
from app.core.config import (
    ECOSYSTEM_PROBE_MAX_BODY_BYTES,
    ECOSYSTEM_PROBE_MAX_ENTRIES,
    ECOSYSTEM_PROBE_TIMEOUT_SECONDS,
)
from app.core.net_guard import guarded_get
from app.core.statements import EcosystemStmts
from app.modules.newspaper.source_liveness import is_source_parked_or_expired

logger = logging.getLogger(__name__)

# Mirrors backend's app.modules.ecosystem.models.domain status/liveness
# constants, which workers cannot import (separate service/venv, CLAUDE.md
# section 0) -- same precedented split as x402_probe's own DEFAULT_CATEGORY
# mirror of backend's x402_directory enum.
_PENDING = "pending"
_APPROVED = "approved"
_RECHECK_STATUSES = (_PENDING, _APPROVED)
# Mirrors backend's ecosystem/services/liveness.py's identical 2026-09-08
# tightening: only a real 2xx counts as alive now, not "anything under
# 500" -- a 404/401/403/etc. genuinely means "not a working listing."
_ALIVE_STATUS_LOW = 200
_ALIVE_STATUS_HIGH = 300  # exclusive upper bound


@dataclass(frozen=True)
class RegistryEntry:
    """The slice of a registry row the sweep needs."""

    slug: str
    url: str
    status: str


class RegistryRepository(Protocol):
    """Storage seam for the sweep; the Cassandra implementation is below, tests fake it."""

    def list_recheckable(self, *, limit: int) -> list[RegistryEntry]:
        """Return pending/approved entries, at most `limit`."""
        ...

    def record_liveness(
        self, slug: str, *, reachable: bool, http_status: int, at: datetime
    ) -> None:
        """Write one liveness result onto the canonical row."""
        ...


class CassandraRegistryRepository:
    """Cassandra-backed RegistryRepository over the shared EcosystemStmts."""

    def list_recheckable(self, *, limit: int) -> list[RegistryEntry]:
        """Bounded scan of ecosystem_projects (small, fully-enumerable table), filtered to pending/approved in Python -- there is no by-status-across-statuses index worth building for a beat that already reads the whole table each pass."""
        session = get_cassandra_session()
        rows = session.execute(EcosystemStmts.LIST_ALL)
        entries = [
            RegistryEntry(slug=row.slug, url=row.url or "", status=row.status or _PENDING)
            for row in rows
            if (row.status or _PENDING) in _RECHECK_STATUSES and row.url
        ]
        return entries[:limit]

    def record_liveness(
        self, slug: str, *, reachable: bool, http_status: int, at: datetime
    ) -> None:
        """Update only the canonical row's liveness fields (never denormalized into projections, see the table's own migration comment)."""
        session = get_cassandra_session()
        session.execute(EcosystemStmts.SET_LIVENESS, (at, reachable, http_status or None, slug))


def check_reachable(url: str) -> tuple[bool, int]:
    """SSRF-guarded reachability check; never raises except SoftTimeLimitExceeded. Only a real 2xx counts as alive (2026-09-08 tightening -- see module docstring and backend's identical liveness.py change).

    SoftTimeLimitExceeded is caught and re-raised explicitly, ahead of the
    generic except below, so a soft-limit interrupt mid-fetch propagates to
    run_liveness_sweep's own handler (module docstring's "only
    SoftTimeLimitExceeded propagates", CLAUDE.md invariant 6) instead of
    being swallowed here as a false "site down" result -- a bare
    `except Exception` catches it too (found 2026-09-07: the sweep's own
    soft-limit interrupt was silently read as every in-flight URL failing,
    so the sweep kept looping on a growing "unreachable" set until hard-
    killed, and the misleading result got written to real registry rows).
    """
    try:
        resp = guarded_get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; pxke-registry-probe)"},
            timeout=ECOSYSTEM_PROBE_TIMEOUT_SECONDS,
            max_bytes=ECOSYSTEM_PROBE_MAX_BODY_BYTES,
        )
        alive = _ALIVE_STATUS_LOW <= resp.status_code < _ALIVE_STATUS_HIGH
        # Only worth the second fetch when the first one already looked
        # alive -- an already-failing/4xx/5xx entry doesn't need a
        # parking-page check on top.
        if alive and is_source_parked_or_expired(url):
            alive = False
        return alive, resp.status_code
    except SoftTimeLimitExceeded:
        raise
    except Exception:
        return False, 0


def run_liveness_sweep(
    *, repo: RegistryRepository | None = None, limit: int = ECOSYSTEM_PROBE_MAX_ENTRIES
) -> dict[str, object]:
    """Re-check every pending/approved registry entry once; return counts. Never pays, never aborts on one URL."""
    repo = repo or CassandraRegistryRepository()
    moment = datetime.now(tz=UTC)
    entries = repo.list_recheckable(limit=limit)
    checked = reachable_count = failed = 0
    for entry in entries:
        try:
            reachable, http_status = check_reachable(entry.url)
            checked += 1
            reachable_count += int(reachable)
            repo.record_liveness(
                entry.slug, reachable=reachable, http_status=http_status, at=moment
            )
        except SoftTimeLimitExceeded:
            logger.warning(
                "ecosystem probe sweep hit the soft time limit after %d/%d entries",
                checked,
                len(entries),
            )
            raise
        except Exception:
            failed += 1
            logger.warning(
                "ecosystem probe failed for slug=%s (continuing sweep)", entry.slug, exc_info=True
            )
    return {
        "status": "ok",
        "entries": len(entries),
        "checked": checked,
        "reachable": reachable_count,
        "failed": failed,
    }


__all__ = [
    "CassandraRegistryRepository",
    "RegistryEntry",
    "RegistryRepository",
    "check_reachable",
    "run_liveness_sweep",
]

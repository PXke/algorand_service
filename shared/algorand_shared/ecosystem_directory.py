"""Read-only lookup of ecosystem-directory-listed domains -- the backend-reachable half of `app.modules.crawler.ecosystem_sync`.

That module (workers-only) owns the SYNC side: fetching awesome-algorand /
algorand.co/case-studies / DefiLlama / Pera-verified listings and writing
`domain_tracking.metadata["ecosystem_listed"] = "true"` for each one, plus
approving/registering the domain into the crawl frontier. None of that write
path belongs here or in backend.

This module owns only the READ side of the SAME `domain_tracking` table both
services already reach via their own `app.core.cassandra` --
`ecosystem_listed_domains()` below is a byte-identical port of
`ecosystem_sync.ecosystem_listed_domains()`'s query, cache shape, and TTL
(same `DOMAIN_TRACKING_LIST` prepared statement, via
`algorand_shared.platform_statements`, that workers' own `DomainTrackingStmts.
LIST` now also resolves to -- see that module's comment). It exists so
`algorand_shared.artifact_priority.ecosystem_listed_score` can compute the
SAME ecosystem-listed bonus when run from backend's process, where
`app.modules.crawler.ecosystem_sync` doesn't exist at all -- added 2026-08-26
alongside the rest of that gap-closing session (see artifact_priority.py's
own module docstring).

Workers keeps calling `ecosystem_sync.ecosystem_listed_domains()` directly
(unchanged, still patchable by its own existing tests) -- `artifact_priority`
only reaches for THIS module as a fallback when that workers-only import
fails.
"""

from __future__ import annotations

import time
from typing import Any

from algorand_shared.platform_statements import DOMAIN_TRACKING_LIST

# Same cache shape/TTL as ecosystem_sync._ecosystem_cache -- a few hundred
# rows, best-effort, never allowed to raise (scoring must survive a
# Cassandra-unreachable process).
_cache: dict[str, Any] = {"at": 0.0, "domains": frozenset()}
_CACHE_TTL_SECONDS = 3600.0


def ecosystem_listed_domains(*, limit: int = 5000) -> frozenset[str]:
    """Directory-listed domains straight from `domain_tracking`, refreshed hourly. Best-effort: a Cassandra failure keeps whatever was cached (or an empty set on first call) and retries after TTL, exactly like `ecosystem_sync`'s own cache -- this lookup must never raise into a scoring call."""
    now = time.time()
    if now - float(_cache["at"]) < _CACHE_TTL_SECONDS:
        return _cache["domains"]
    try:
        from app.core.cassandra import get_cassandra_session, prepare_cached

        # DOMAIN_TRACKING_LIST is a bare module-level `_Stmt` (see
        # platform_statements.py's own docstring): accessed this way, plain
        # attribute lookup skips the descriptor protocol entirely, so it
        # must be prepared explicitly here rather than handed straight to
        # `execute` -- unlike `DomainTrackingStmts.LIST` (workers), which
        # resolves it as a CLASS attribute and gets `__get__`'s lazy
        # preparation for free.
        stmt = prepare_cached(DOMAIN_TRACKING_LIST.cql)
        rows = get_cassandra_session().execute(stmt, (limit,))
        listed = frozenset(
            row.domain
            for row in rows
            if (row.metadata or {}).get("ecosystem_listed") == "true"
            and row.is_relevant is not False
        )
        _cache["domains"] = listed
        _cache["at"] = now
    except Exception:
        _cache["at"] = now - _CACHE_TTL_SECONDS + 300.0
    return _cache["domains"]

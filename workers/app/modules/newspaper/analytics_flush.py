"""Worker-side flusher for the six deferred (write-behind) pageview-analytics dimensions.

backend's app/modules/seo/analytics_store.py no longer writes geo_country_daily,
campaign_daily, pageview_hour_daily, pageview_language_daily,
pageview_referrer_path_daily or pageview_referrer_url_daily directly on every
confirmed-human pageview (2026-08-25) -- it INCRs a per-(dimension, day, key)
pending counter in Redis instead (see that module's note above
_write_pageview_counters for exactly why these six and not the others: the
UA-repeat-offender clawback, _purge_direct_sample_ua, retroactively decrements
a different, disjoint set of counters and needs those to stay synchronous).
flush_pending_analytics drains those pending keys into the real Cassandra
counters on a Celery beat schedule (see celery_app.py's
"flush-pending-analytics" entry), the same shape as flush_pending_views for
article_view_counts.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from app.core.statements import AnalyticsFlushStmts

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)

# Must match backend's app/modules/seo/analytics_store._ANALYTICS_PENDING_PREFIX.
PENDING_PREFIX = "news:analytics:pending:"
_PENDING_MATCH = PENDING_PREFIX + "*"

# dim -> (AnalyticsFlushStmts attribute NAME, number of key parts after `day`,
# whether the first part after `day` needs an int cast -- only
# pageview_hour_daily's `hour` column is an int, everything else here is
# text). Deliberately holds the attribute name, not the resolved statement:
# _Stmt.__get__ calls prepare_cached, which opens a real Cassandra session --
# resolving that at IMPORT time (a module-level dict built eagerly) would
# make merely importing this module dial Cassandra, unlike every other
# statement registry access in this codebase (always inside a function, at
# call time). getattr(AnalyticsFlushStmts, name) below defers that exactly
# the same way.
_DIM_SPECS: dict[str, tuple[str, int, bool]] = {
    "geo": ("GEO_BUMP", 2, False),
    "campaign": ("CAMPAIGN_BUMP", 2, False),
    "hour": ("HOUR_BUMP", 2, True),
    "language": ("LANGUAGE_BUMP", 2, False),
    "referrer_path": ("REFERRER_PATH_BUMP", 3, False),
    "referrer_url": ("REFERRER_URL_BUMP", 2, False),
}


def _redis_client() -> redis.Redis:
    import redis

    from app.core.config import REDIS_URL

    return redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)


def _decode_key(key: str) -> tuple[str, list[str]] | None:
    """Reverse backend's _pending_analytics_key: `news:analytics:pending:{dim}:{hex:hex:...}` -> (dim, [parts]).

    Returns None for anything that doesn't parse cleanly (wrong arity for its
    dim, bad hex, unknown dim) -- the caller treats that as a malformed key to
    drop rather than retry forever.
    """
    if not key.startswith(PENDING_PREFIX):
        return None
    rest = key[len(PENDING_PREFIX) :]
    dim, _, encoded = rest.partition(":")
    spec = _DIM_SPECS.get(dim)
    if spec is None or not encoded:
        return None
    hex_parts = encoded.split(":")
    if len(hex_parts) != spec[1]:
        return None
    try:
        parts = [bytes.fromhex(p).decode() for p in hex_parts]
    except (ValueError, UnicodeDecodeError):
        return None
    return dim, parts


def flush_pending_analytics() -> dict[str, int]:
    """Drain Redis's pending deferred-dimension pageview deltas into their Cassandra counters.

    Runs on a Celery beat schedule (ANALYTICS_FLUSH_SECONDS, default matching
    flush_pending_views' 10 minutes) -- these six dimensions feed breakdown
    charts on an admin-only dashboard read at most a few times a day, so a
    short, self-correcting lag is unobservable. A worker crash mid-window
    loses at most one window's worth of increments for a non-critical
    breakdown metric, same accepted tradeoff as flush_pending_views.

    Best-effort throughout, matching every other Redis touch in this
    codebase: a Redis outage flushes nothing this cycle (increments stay
    parked for the next run); a per-key Cassandra failure skips that one key
    without losing ones already applied in the same pass.
    """
    try:
        client = _redis_client()
        keys = list(client.scan_iter(match=_PENDING_MATCH, count=200))
    except Exception:
        logger.warning(
            "flush_pending_analytics: Redis unavailable, nothing flushed", exc_info=True
        )
        return {"applied": 0, "skipped": 0}

    if not keys:
        return {"applied": 0, "skipped": 0}

    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    applied = 0
    skipped = 0
    for key in keys:
        decoded = _decode_key(key)
        if decoded is None:
            # Malformed key somehow got in -- drop it so it doesn't jam every
            # future cycle re-scanning something it can never apply.
            with contextlib.suppress(Exception):
                client.delete(key)
            skipped += 1
            continue
        dim, parts = decoded
        stmt_name, _arity, hour_cast = _DIM_SPECS[dim]

        # GETDEL: atomic read+clear of this one key. A view landing between
        # the GETDEL and the Cassandra write below just creates a fresh key
        # for the next cycle to pick up -- the same accepted race
        # flush_pending_views documents for article_view_counts.
        try:
            delta = client.getdel(key)
        except Exception:
            logger.warning(
                "flush_pending_analytics: failed to read/clear %s", key, exc_info=True
            )
            skipped += 1
            continue
        if not delta:
            continue
        try:
            stmt = getattr(AnalyticsFlushStmts, stmt_name)
            day = parts[0]
            rest = [int(p) if hour_cast and i == 0 else p for i, p in enumerate(parts[1:])]
            session.execute(stmt, (int(delta), day, *rest))
            applied += 1
        except Exception:
            logger.warning(
                "flush_pending_analytics: Cassandra bump failed for %s", key, exc_info=True
            )
            skipped += 1

    return {"applied": applied, "skipped": skipped}

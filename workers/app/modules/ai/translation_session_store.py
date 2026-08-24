"""Per-language translation lifecycle tracking (translation_sessions).

A local-translate batch can run for hours (one language at a time, each
loading/unloading its own model) with no per-language record anywhere until
that language finishes — a crash mid-language (worker OOM, SIGKILL, a hung
model call) left nothing to distinguish "still working" from "silently
dead". local_translate_lock's own reclaim mechanism only protects the LOCK,
not visibility into what it's protecting. This module is the
compose_sessions-style fix: a row per language, written 'running' at start
and 'ok'/'error' at finish, reaped to 'stale' if it outlives
TRANSLATION_SESSION_STALE_MINUTES still 'running' (see
reap_stale_compose_sessions in tool_insights_store for the pattern this
mirrors).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

logger = logging.getLogger(__name__)

# Real month buckets (see algorand_shared.feed_bucket) -- this table previously
# used a single hardcoded bucket="all" partition, only safe because a 7-day
# TTL kept it bounded; removing the TTL without fixing this trades a
# tombstone-stream problem for an unbounded-single-partition one (2026-08-24,
# same fix as tool_insights_store's compose_sessions/tool_suggestions/
# compose_feedback). Rows written before this cutover stay in the old "all"
# partition forever -- the backend admin list view still scans it too.
_NON_TERMINAL_STATUSES = ("running",)
# reap_stale_translation_sessions only looks for a row still 'running' --
# 2 months covers any session straddling a month boundary without scanning
# history.
_RECENT_MONTHS = 2

SessionRef = tuple[UUID, datetime]


def start_translation_session(article_id: str, lang: str) -> SessionRef | None:
    """Record the start of one language's translation. Returns a ref to pass to finish_translation_session, or None on failure (best-effort, never raises)."""
    try:
        from algorand_shared.feed_bucket import feed_month
        from cassandra.util import uuid_from_time

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import TranslationSessionStmts

        session = get_cassandra_session()
        now = datetime.now(tz=UTC)
        session_id = uuid_from_time(now)
        session.execute(
            TranslationSessionStmts.INSERT,
            (
                feed_month(now),
                now,
                session_id,
                (article_id or "")[:64],
                (lang or "")[:8],
                "running",
                0,
                "",
            ),
        )
        return session_id, now
    except Exception:
        logger.warning(
            "failed to start translation session for %s/%s", article_id, lang, exc_info=True
        )
        return None


def finish_translation_session(ref: SessionRef | None, *, status: str, error: str = "") -> bool:
    """Mark a translation session row 'ok' or 'error'. No-op if ref is None (the start write itself failed, so there's no row to update)."""
    if ref is None:
        return False
    session_id, started_at = ref
    try:
        from algorand_shared.feed_bucket import feed_month

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import TranslationSessionStmts

        session = get_cassandra_session()
        duration_ms = int((datetime.now(tz=UTC) - started_at).total_seconds() * 1000)
        # bucket must match what start_translation_session wrote this row
        # under -- derived from the SAME started_at, not a fresh now().
        session.execute(
            TranslationSessionStmts.MARK_DONE,
            (status[:16], duration_ms, error[:500], feed_month(started_at), started_at, session_id),
        )
        return True
    except Exception:
        logger.warning("failed to finish translation session", exc_info=True)
        return False


def reap_stale_translation_sessions(*, stale_minutes: int | None = None) -> dict[str, int]:
    """Mark any translation_sessions row still 'running' past the staleness window as 'stale'. Mirrors reap_stale_compose_sessions: a crash mid-language (OOM, SIGKILL, a hung model call) that skips the on_language_done/on_language_error callback otherwise leaves the row looking perpetually in-progress until the table's 7-day TTL quietly drops it. Best-effort, never raises."""
    from datetime import timedelta

    from app.core.config import TRANSLATION_SESSION_STALE_MINUTES

    threshold_minutes = (
        stale_minutes if stale_minutes is not None else TRANSLATION_SESSION_STALE_MINUTES
    )
    cutoff = datetime.now(tz=UTC) - timedelta(minutes=threshold_minutes)

    try:
        from algorand_shared.feed_bucket import months_back

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import TranslationSessionStmts

        session = get_cassandra_session()
        checked = 0
        reaped = 0
        for bucket in months_back(datetime.now(tz=UTC), _RECENT_MONTHS):
            rows = session.execute(TranslationSessionStmts.LIST_ALL_SUMMARY, (bucket,))
            for row in rows:
                checked += 1
                if row.status not in _NON_TERMINAL_STATUSES:
                    continue
                started_at = row.started_at
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=UTC)
                if started_at >= cutoff:
                    continue
                session.execute(
                    TranslationSessionStmts.MARK_STALE,
                    ("stale", bucket, row.started_at, row.session_id),
                )
                reaped += 1
        return {"checked": checked, "reaped": reaped}
    except Exception:
        logger.warning("failed to reap stale translation sessions", exc_info=True)
        return {"checked": 0, "reaped": 0}

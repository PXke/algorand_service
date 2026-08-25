"""Cassandra storage for the weekly X (Twitter) search sweep.

One row per tracked service (x_search_weekly, migration 074), overwritten by
each sweep run -- a rolling cache of "this week's posts about this service",
not a history. Written by ``x_search_sweep.py`` (the weekly Celery beat
task), read by ``research_tools.py``'s ``_tool_search_x`` (the writer tool,
at compose time).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class XSearchSnapshot:
    """One service's stored weekly-sweep result."""

    service_id: str
    display_name: str
    query: str
    posts: tuple[dict[str, Any], ...]
    swept_at: datetime | None
    error: str


def save_snapshot(
    *,
    service_id: str,
    display_name: str,
    query: str,
    posts: list[dict[str, Any]],
    error: str = "",
) -> None:
    """Overwrite this service's stored snapshot with this week's sweep result."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import XSearchWeeklyStmts

    session = get_cassandra_session()
    session.execute(
        XSearchWeeklyStmts.UPSERT,
        (
            service_id,
            display_name or "",
            query or "",
            json.dumps(posts, separators=(",", ":")),
            len(posts),
            datetime.now(tz=UTC),
            (error or "")[:200],
        ),
    )


def list_snapshots() -> list[XSearchSnapshot]:
    """Every stored per-service snapshot.

    A small, sweep-bounded table (see config.X_SEARCH_WEEKLY_SWEEP_MAX_SERVICES),
    safe to scan in full rather than needing a cache: the search_x tool matches a
    free-text query against ALL tracked services' names, not just one it already
    knows the id for.
    """
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import XSearchWeeklyStmts

    session = get_cassandra_session()
    rows = session.execute(XSearchWeeklyStmts.LIST_ALL)
    out: list[XSearchSnapshot] = []
    for row in rows:
        try:
            posts = json.loads(row.posts_json) if row.posts_json else []
        except (TypeError, ValueError):
            posts = []
        out.append(
            XSearchSnapshot(
                service_id=row.service_id,
                display_name=row.display_name or "",
                query=row.query or "",
                posts=tuple(posts) if isinstance(posts, list) else (),
                swept_at=row.swept_at,
                error=row.error or "",
            )
        )
    return out

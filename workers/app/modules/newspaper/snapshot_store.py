"""Store and fetch the latest content snapshot for a service source."""

from __future__ import annotations

from datetime import UTC, datetime


def source_id_for_service(service_id: str) -> str:
    """Build the snapshot source_id key for a service."""
    return f"svc:{service_id}"


def get_latest_snapshot(source_id: str) -> tuple[str, str, str] | None:
    """Return (content_hash, title, body) for latest snapshot or None."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import SnapshotStmts

    session = get_cassandra_session()
    row = session.execute(SnapshotStmts.GET_LATEST, (source_id,)).one()
    if row is None:
        return None
    return row.content_hash, row.title or "", row.body or ""


def insert_snapshot(
    *,
    source_id: str,
    service_id: str,
    url: str,
    content_hash: str,
    title: str,
    body: str,
) -> None:
    """Store a new content snapshot and update the source's latest-snapshot pointer."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import SnapshotStmts

    session = get_cassandra_session()
    now = datetime.now(tz=UTC)
    session.execute(
        SnapshotStmts.INSERT,
        (source_id, now, content_hash, title, body),
    )
    session.execute(
        SnapshotStmts.INSERT_SOURCE,
        (source_id, service_id, url, True, now),
    )

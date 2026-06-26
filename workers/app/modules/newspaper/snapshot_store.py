from __future__ import annotations

from datetime import UTC, datetime


def source_id_for_service(service_id: str) -> str:
    return f"svc:{service_id}"


def get_latest_snapshot(source_id: str) -> tuple[str, str, str] | None:
    """Return (content_hash, title, body) for latest snapshot or None."""
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    row = session.execute(
        """
        SELECT content_hash, title, body
        FROM page_snapshots
        WHERE source_id = %s
        LIMIT 1
        """,
        (source_id,),
    ).one()
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
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    now = datetime.now(tz=UTC)
    session.execute(
        """
        INSERT INTO page_snapshots (source_id, captured_at, content_hash, title, body)
        VALUES (%s, %s, %s, %s, %s) USING TTL 3888000
        """,
        (source_id, now, content_hash, title, body),
    )
    session.execute(
        """
        INSERT INTO page_sources (source_id, service_id, url, enabled, updated_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (source_id, service_id, url, True, now),
    )

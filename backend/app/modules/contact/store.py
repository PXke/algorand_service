"""Cassandra store for reader contact messages (public /contact form).

Messages land in month buckets ("2026-07") so the admin inbox is a couple of
bounded partition reads, never a table scan.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.schemas import ContactMessageItem


def _bucket(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def insert_message(*, name: str, email: str, message: str) -> str:
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ContactStmts

    now = datetime.now(tz=UTC)
    message_id = uuid4()
    get_cassandra_session().execute(
        ContactStmts.INSERT,
        (_bucket(now), now, message_id, name, email, message),
    )
    return str(message_id)


def list_recent(limit: int = 200) -> list[ContactMessageItem]:
    """Inbox: current + previous month, newest first."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ContactStmts

    session = get_cassandra_session()
    now = datetime.now(tz=UTC)
    buckets = [_bucket(now), _bucket(now.replace(day=1) - timedelta(days=1))]
    items: list[ContactMessageItem] = []
    for bucket in buckets:
        for row in session.execute(ContactStmts.LIST_BUCKET, (bucket,)):
            items.append(
                ContactMessageItem(
                    message_id=str(row.message_id),
                    name=row.name or "",
                    email=row.email or "",
                    message=row.message or "",
                    created_at_epoch=int(row.created_at.timestamp()) if row.created_at else 0,
                )
            )
    items.sort(key=lambda item: item.created_at_epoch, reverse=True)
    return items[:limit]

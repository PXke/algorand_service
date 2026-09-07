"""Cassandra-backed x402 uptime-check history storage."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.cassandra import get_cassandra_session
from app.core.statements import X402UptimeHistoryStmts
from app.modules.x402_uptime.models.domain import StoredUptimeCheck


def _dt(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


def _epoch(value: datetime | None) -> int:
    """UTC epoch seconds from a stored timestamp.

    The Cassandra driver returns timezone-naive datetimes that are already
    UTC wall-clock values -- calling .timestamp() directly would make Python
    assume the server's LOCAL zone and silently shift the result (the same
    bug class x402_storage/stores/cassandra.py's own `_epoch` guards
    against, root-caused 2026-09-03 elsewhere in this codebase).
    """
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp())


def _row_to_check(row: object) -> StoredUptimeCheck:
    return StoredUptimeCheck(
        url_hash=row.url_hash,
        url=row.url or "",
        checked_at_epoch=_epoch(row.checked_at),
        reachable=bool(row.reachable),
        http_status=int(row.http_status or 0),
        response_time_ms=int(row.response_time_ms or 0),
        error=row.error or "",
    )


class CassandraUptimeHistoryStore:
    """Cassandra-backed uptime-check history: one partition per url_hash, newest first.

    The one owner-facing access pattern (read a url's own history) is
    single-partition and always LIMITed -- see X402UptimeHistoryStmts.LIST_SINCE.
    """

    def record(self, item: StoredUptimeCheck) -> None:
        """Append one real-check row."""
        session = get_cassandra_session()
        session.execute(
            X402UptimeHistoryStmts.INSERT,
            (
                item.url_hash,
                _dt(item.checked_at_epoch),
                item.url,
                item.reachable,
                item.http_status,
                item.response_time_ms,
                item.error,
            ),
        )

    def list_since(self, url_hash: str, *, since_epoch: int, limit: int) -> list[StoredUptimeCheck]:
        """Up to `limit` rows for this url_hash with checked_at >= since_epoch, newest first."""
        session = get_cassandra_session()
        rows = session.execute(
            X402UptimeHistoryStmts.LIST_SINCE, (url_hash, _dt(since_epoch), limit)
        )
        return [_row_to_check(row) for row in rows]

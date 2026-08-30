"""Cassandra-backed x402 visibility-board placement storage."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.cassandra import get_cassandra_session
from app.core.statements import X402BoardStmts
from app.modules.x402_board.models.domain import BOARD_PARTITION, StoredPlacement


def _dt(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


def _epoch(value: datetime | None) -> int:
    return int(value.timestamp()) if value else 0


def _row_to_placement(row: object) -> StoredPlacement:
    return StoredPlacement(
        entry_id=row.entry_id,
        link=row.link or "",
        name=row.name or "",
        pitch=row.pitch or "",
        payer=row.payer or "",
        settlement_tx_id=row.settlement_tx_id or "",
        term_end_epoch=_epoch(row.term_end),
        created_at_epoch=_epoch(row.created_at),
    )


class CassandraPlacementStore:
    """Cassandra-backed x402 visibility-board placement storage."""

    def upsert(self, item: StoredPlacement) -> None:
        """Create or replace one placement, recency projection included.

        Writes the canonical x402_board_entries row before touching the
        recency projection (store before mark): if the projection write then
        fails, the placement exists and is missing only from the public feed,
        which a re-placement repairs. The reverse order could leave a feed
        entry pointing at a placement that was never durably stored.

        The superseded projection row is deleted BEFORE the new one is
        inserted. A crash in that window drops the placement from the feed
        until it is renewed; the opposite order would leave a permanent
        duplicate feed row advertising the previous, already-expired term.
        """
        session = get_cassandra_session()
        previous = self.get(item.entry_id)
        session.execute(
            X402BoardStmts.UPSERT_PLACEMENT,
            (
                item.entry_id,
                item.link,
                item.name,
                item.pitch,
                item.payer,
                _dt(item.term_end_epoch),
                item.settlement_tx_id,
                _dt(item.created_at_epoch),
            ),
        )
        if previous is not None and previous.created_at_epoch != item.created_at_epoch:
            session.execute(
                X402BoardStmts.DELETE_RECENCY,
                (BOARD_PARTITION, _dt(previous.created_at_epoch), item.entry_id),
            )
        session.execute(
            X402BoardStmts.INSERT_RECENCY,
            (
                BOARD_PARTITION,
                _dt(item.created_at_epoch),
                item.entry_id,
                item.link,
                item.name,
                item.pitch,
                item.payer,
                _dt(item.term_end_epoch),
                item.settlement_tx_id,
            ),
        )

    def get(self, entry_id: str) -> StoredPlacement | None:
        """Return the placement for an entry id, or None if there is none."""
        session = get_cassandra_session()
        row = session.execute(X402BoardStmts.GET_PLACEMENT, (entry_id,)).one()
        return None if row is None else _row_to_placement(row)

    def list_recent(self, *, limit: int) -> list[StoredPlacement]:
        """Return placements newest-first, at most `limit` of them."""
        session = get_cassandra_session()
        rows = session.execute(X402BoardStmts.LIST_RECENT, (BOARD_PARTITION, limit))
        return [_row_to_placement(row) for row in rows]

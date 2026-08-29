"""Cassandra-backed x402 directory listing storage."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.cassandra import get_cassandra_session
from app.core.statements import X402DirectoryStmts
from app.modules.x402_directory.models.domain import DIRECTORY_PARTITION, StoredListing


def _dt(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


def _epoch(value: datetime | None) -> int:
    return int(value.timestamp()) if value else 0


def _row_to_listing(row: object) -> StoredListing:
    return StoredListing(
        url_hash=row.url_hash,
        url=row.url or "",
        price=row.price or "",
        description=row.description or "",
        schema_json=row.schema_json or "",
        settlement_tx_id=row.settlement_tx_id or "",
        term_end_epoch=_epoch(row.term_end),
        created_at_epoch=_epoch(row.created_at),
        assets=sorted(row.assets or []),
        tags=sorted(row.tags or []),
    )


class CassandraListingStore:
    """Cassandra-backed x402 directory listing storage."""

    def upsert(self, item: StoredListing) -> None:
        """Create or replace the listing for one endpoint URL, feed projection included.

        Writes the canonical x402_listings row before touching the recency
        projection (store before mark): if the projection write then fails, the
        listing exists and is missing only from the feed, which a re-list
        repairs. The reverse order could leave a feed entry pointing at a
        listing that was never durably stored.

        The superseded projection row is deleted BEFORE the new one is inserted.
        A crash in that window drops the endpoint from the feed until it is
        re-listed; the opposite order would leave a permanent duplicate feed row
        advertising the previous, already-expired term.
        """
        session = get_cassandra_session()
        previous = self.get(item.url_hash)
        session.execute(
            X402DirectoryStmts.UPSERT_LISTING,
            (
                item.url_hash,
                item.url,
                item.price,
                set(item.assets),
                item.description,
                item.schema_json,
                set(item.tags),
                _dt(item.term_end_epoch),
                item.settlement_tx_id,
                _dt(item.created_at_epoch),
            ),
        )
        if previous is not None and previous.created_at_epoch != item.created_at_epoch:
            session.execute(
                X402DirectoryStmts.DELETE_RECENCY,
                (DIRECTORY_PARTITION, _dt(previous.created_at_epoch), item.url_hash),
            )
        session.execute(
            X402DirectoryStmts.INSERT_RECENCY,
            (
                DIRECTORY_PARTITION,
                _dt(item.created_at_epoch),
                item.url_hash,
                item.url,
                item.price,
                set(item.assets),
                item.description,
                item.schema_json,
                set(item.tags),
                _dt(item.term_end_epoch),
                item.settlement_tx_id,
            ),
        )

    def get(self, url_hash: str) -> StoredListing | None:
        """Return the current listing for a URL hash, or None if not listed."""
        session = get_cassandra_session()
        row = session.execute(X402DirectoryStmts.GET_LISTING, (url_hash,)).one()
        return None if row is None else _row_to_listing(row)

    def list_recent(self, *, limit: int) -> list[StoredListing]:
        """Return listings newest-first, at most `limit` of them."""
        session = get_cassandra_session()
        rows = session.execute(X402DirectoryStmts.LIST_RECENT, (DIRECTORY_PARTITION, limit))
        return [_row_to_listing(row) for row in rows]

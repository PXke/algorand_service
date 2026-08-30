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
        payer=getattr(row, "payer", None) or "",
    )


def _canonical_params(item: StoredListing) -> tuple:
    """Bind params shared by UPSERT_LISTING and INSERT_LISTING_IF_ABSENT."""
    return (
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
        item.payer,
    )


def _projection_params(partition: str, item: StoredListing) -> tuple:
    """Bind params shared by INSERT_RECENCY and INSERT_BY_TAG (same column order after the partition key)."""
    return (
        partition,
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
        item.payer,
    )


class CassandraListingStore:
    """Cassandra-backed x402 directory listing storage.

    Three tables: the canonical x402_listings row, the newest-first recency
    projection (090) and the per-tag projection (096). Every write path goes
    canonical row first, projections second (store before mark): if a
    projection write then fails, the listing exists and is missing only from a
    feed, which a re-list repairs. The reverse order could leave a feed entry
    pointing at a listing that was never durably stored.
    """

    def insert_if_absent(self, item: StoredListing) -> bool:
        """Store the listing only if no x402_listings row exists for its url_hash.

        A lightweight transaction (INSERT ... IF NOT EXISTS), so two
        concurrent first-time listers of the same url cannot both succeed:
        exactly one INSERT is applied and the other returns False having
        written nothing, including no projection rows. Projections are
        written only on the applied path, and with no previous listing to
        supersede there is nothing to delete first.
        """
        session = get_cassandra_session()
        result = session.execute(
            X402DirectoryStmts.INSERT_LISTING_IF_ABSENT, _canonical_params(item)
        )
        if not result.was_applied:
            return False
        self._write_projections(item, previous=None)
        return True

    def upsert(self, item: StoredListing) -> None:
        """Create or replace the listing for one endpoint URL, projections included.

        A superseded projection row is deleted BEFORE the new one is inserted.
        A crash in that window drops the endpoint from that feed until it is
        re-listed; the opposite order would leave a permanent duplicate feed
        row advertising the previous, already-expired term.
        """
        session = get_cassandra_session()
        previous = self.get(item.url_hash)
        session.execute(X402DirectoryStmts.UPSERT_LISTING, _canonical_params(item))
        self._write_projections(item, previous=previous)

    def _write_projections(self, item: StoredListing, *, previous: StoredListing | None) -> None:
        """Refresh the recency and by-tag projections for `item`, superseding `previous`'s rows.

        A previous row is deleted only when the new INSERT will not overwrite
        it in place: for the recency feed that is when created_at moved; for
        the tag feed it is when created_at moved OR the tag was dropped from
        the listing. Deleting a row the INSERT is about to rewrite at the
        same key would be a tombstone racing its own replacement.
        """
        session = get_cassandra_session()
        moved = previous is not None and previous.created_at_epoch != item.created_at_epoch
        if previous is not None and moved:
            session.execute(
                X402DirectoryStmts.DELETE_RECENCY,
                (DIRECTORY_PARTITION, _dt(previous.created_at_epoch), item.url_hash),
            )
        session.execute(
            X402DirectoryStmts.INSERT_RECENCY, _projection_params(DIRECTORY_PARTITION, item)
        )
        if previous is not None:
            new_tags = set(item.tags)
            for tag in previous.tags:
                if moved or tag not in new_tags:
                    session.execute(
                        X402DirectoryStmts.DELETE_BY_TAG,
                        (tag, _dt(previous.created_at_epoch), item.url_hash),
                    )
        for tag in item.tags:
            session.execute(X402DirectoryStmts.INSERT_BY_TAG, _projection_params(tag, item))

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

    def list_by_tag(self, tag: str, *, limit: int) -> list[StoredListing]:
        """Return listings carrying the (already normalized) tag, newest-first, at most `limit`."""
        session = get_cassandra_session()
        rows = session.execute(X402DirectoryStmts.LIST_BY_TAG, (tag, limit))
        return [_row_to_listing(row) for row in rows]

    def delete(self, url_hash: str) -> bool:
        """Remove the listing for a URL hash, projections included. False if it did not exist.

        Both projections are keyed on (partition, created_at, url_hash), so
        deleting them needs the exact created_at and tag set of the row being
        removed, read from x402_listings first (same reason upsert() reads
        `previous` before writing). The canonical x402_listings row is
        deleted last -- if a crash lands between the deletes, the listing is
        gone from the free feeds but the canonical row (and its owner-still-
        set payer) still exists, which is the safer of the half-done states
        for an admin removal.
        """
        session = get_cassandra_session()
        existing = self.get(url_hash)
        if existing is None:
            return False
        created_at = _dt(existing.created_at_epoch)
        session.execute(
            X402DirectoryStmts.DELETE_RECENCY, (DIRECTORY_PARTITION, created_at, url_hash)
        )
        for tag in existing.tags:
            session.execute(X402DirectoryStmts.DELETE_BY_TAG, (tag, created_at, url_hash))
        session.execute(X402DirectoryStmts.DELETE_LISTING, (url_hash,))
        return True

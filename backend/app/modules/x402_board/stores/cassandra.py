"""Cassandra-backed x402 visibility-board placement storage."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from app.core.cassandra import execute_parallel_with_args, get_cassandra_session
from app.core.statements import X402BoardStmts
from app.modules.x402_board.models.domain import (
    BOARD_PARTITION,
    DEFAULT_BOARD_CATEGORY,
    StoredClickEvent,
    StoredPlacement,
)


def _dt(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


def _epoch(value: datetime | None) -> int:
    """UTC epoch seconds from a stored timestamp.

    The Cassandra driver returns timezone-NAIVE datetimes that are already UTC wall-clock values;
    calling .timestamp() directly makes Python assume the server's LOCAL zone and silently shift
    the result (same bug class fixed in news/stores/cassandra.py and x402_social/stores/cassandra.py
    -- that fix never got propagated here).
    """
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp())


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
        # Pre-114 rows read back null: "not boosted," exactly right since
        # boosting did not exist before that migration.
        boosted_until_epoch=_epoch(getattr(row, "boosted_until", None)),
        # Pre-120 rows (and x402_board_by_category rows, which never carry
        # the column -- see LIST_BY_CATEGORY) read back as
        # DEFAULT_BOARD_CATEGORY via getattr's default.
        category=getattr(row, "category", None) or DEFAULT_BOARD_CATEGORY,
    )


def _click_event(row: object) -> StoredClickEvent:
    return StoredClickEvent(
        entry_id=row.entry_id,
        clicked_at_epoch=_epoch(row.clicked_at),
        referrer=row.referrer or "",
    )


class CassandraPlacementStore:
    """Cassandra-backed x402 visibility-board placement storage."""

    def upsert(self, item: StoredPlacement) -> None:
        """Create or replace one placement, recency and category projections included.

        Writes the canonical x402_board_entries row before touching either
        projection (store before mark): if a projection write then fails, the
        placement exists and is missing only from the affected free feed,
        which a re-placement repairs. The reverse order could leave a feed
        entry pointing at a placement that was never durably stored.

        Each projection's superseded row is deleted BEFORE its replacement is
        inserted. A crash in that window drops the placement from that one
        feed until it is renewed; the opposite order would leave a permanent
        duplicate feed row advertising the previous, already-expired term.

        The category projection's old row is deleted whenever created_at
        moved OR the category itself changed (a relist can do either, or
        both) -- unlike the directory's multi-tag projection, a placement
        carries exactly one category, so there is never more than one old
        row to clean up here.
        """
        session = get_cassandra_session()
        previous = self.get(item.entry_id)
        boosted_until = _dt(item.boosted_until_epoch) if item.boosted_until_epoch else None
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
                boosted_until,
                item.category,
            ),
        )
        moved = previous is not None and previous.created_at_epoch != item.created_at_epoch
        if previous is not None and moved:
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
                boosted_until,
                item.category,
            ),
        )
        if previous is not None and (moved or previous.category != item.category):
            session.execute(
                X402BoardStmts.DELETE_BY_CATEGORY,
                (previous.category, _dt(previous.created_at_epoch), item.entry_id),
            )
        session.execute(
            X402BoardStmts.INSERT_BY_CATEGORY,
            (
                item.category,
                _dt(item.created_at_epoch),
                item.entry_id,
                item.link,
                item.name,
                item.pitch,
                item.payer,
                _dt(item.term_end_epoch),
                item.settlement_tx_id,
                boosted_until,
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

    def list_by_category(self, category: str, *, limit: int) -> list[StoredPlacement]:
        """Return placements in this category, newest-first, at most `limit`.

        x402_board_by_category does not itself carry a `category` column (its
        partition key already names it, same denormalization choice
        x402_listings_by_tag's own tag partition key makes) -- replace() sets
        it on each mapped row rather than relying on `_row_to_placement`'s
        getattr default, which would otherwise silently read back
        DEFAULT_BOARD_CATEGORY for every row here.
        """
        session = get_cassandra_session()
        rows = session.execute(X402BoardStmts.LIST_BY_CATEGORY, (category, limit))
        return [replace(_row_to_placement(row), category=category) for row in rows]

    def increment_clicks(self, entry_id: str) -> None:
        """Add one to a placement's click-through total, atomically.

        A Cassandra counter column (migration 098), a true atomic add-one at
        the replica. Counter updates are not idempotent under a client-side
        retry, so this is issued exactly once and never wrapped in a retry --
        same contract as the feature board's vote counter.
        """
        session = get_cassandra_session()
        session.execute(X402BoardStmts.INCREMENT_CLICKS, (entry_id,))

    def get_click_counts(self, entry_ids: list[str]) -> dict[str, int]:
        """Return click totals for many placements at once, keyed by entry id.

        Concurrent point reads via execute_parallel_with_args rather than one
        `WHERE entry_id IN ?`: each id is its own partition, so an IN would
        make one coordinator fan out and wait on every replica serially.
        Results come back in input order, so they zip against the ids.
        """
        if not entry_ids:
            return {}
        results = execute_parallel_with_args(
            X402BoardStmts.GET_CLICKS, [(eid,) for eid in entry_ids]
        )
        counts: dict[str, int] = {}
        for entry_id, (_success, result) in zip(entry_ids, results, strict=True):
            row = result.one()
            if row is not None:
                counts[entry_id] = int(row.clicks or 0)
        return counts

    def delete(self, entry_id: str) -> bool:
        """Remove one placement, recency and category projections included. False if it did not exist.

        Same shape as the directory's admin delist: each projection row is
        keyed partly on the canonical row's own created_at/category, so the
        canonical row is read first, both projection rows are deleted, and
        the canonical row last -- a crash in that window leaves the tile gone
        from the affected feed(s) but still resolvable by id, the safer
        half-done state. The x402_board_clicks counter and the
        x402_board_click_events log are left untouched (see the Protocol).
        """
        session = get_cassandra_session()
        existing = self.get(entry_id)
        if existing is None:
            return False
        session.execute(
            X402BoardStmts.DELETE_RECENCY,
            (BOARD_PARTITION, _dt(existing.created_at_epoch), entry_id),
        )
        session.execute(
            X402BoardStmts.DELETE_BY_CATEGORY,
            (existing.category, _dt(existing.created_at_epoch), entry_id),
        )
        session.execute(X402BoardStmts.DELETE_PLACEMENT, (entry_id,))
        return True

    def record_click_event(self, entry_id: str, *, clicked_at_epoch: int, referrer: str) -> None:
        """Append one click event for the owner-only click-analytics read."""
        session = get_cassandra_session()
        session.execute(
            X402BoardStmts.INSERT_CLICK_EVENT,
            (entry_id, _dt(clicked_at_epoch), referrer),
        )

    def list_click_events(
        self, entry_id: str, *, since_epoch: int, limit: int
    ) -> list[StoredClickEvent]:
        """Up to `limit` click events for this entry_id with clicked_at >= since_epoch, newest first."""
        session = get_cassandra_session()
        rows = session.execute(
            X402BoardStmts.LIST_CLICK_EVENTS, (entry_id, _dt(since_epoch), limit)
        )
        return [_click_event(row) for row in rows]

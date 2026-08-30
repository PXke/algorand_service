"""Cassandra-backed x402 endpoint-grade storage."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.cassandra import get_cassandra_session
from app.core.statements import X402GradingStmts
from app.modules.x402_grading.models.domain import (
    GRADING_PARTITION,
    GradedEndpoint,
    StoredGrade,
)


def _dt(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


def _epoch(value: datetime | None) -> int:
    return int(value.timestamp()) if value else 0


def _row_to_grade(row: object) -> StoredGrade:
    return StoredGrade(
        url_hash=row.url_hash,
        url=row.url or "",
        grader=row.grader,
        score=int(row.score or 0),
        comment=row.comment or "",
        settlement_tx_id=row.settlement_tx_id or "",
        created_at_epoch=_epoch(row.created_at),
    )


def _row_to_indexed(row: object) -> GradedEndpoint:
    return GradedEndpoint(
        url_hash=row.url_hash,
        url=row.url or "",
        last_graded_at_epoch=_epoch(row.last_graded_at),
    )


class CassandraGradeStore:
    """Cassandra-backed x402 endpoint-grade storage."""

    def upsert(self, item: StoredGrade) -> None:
        """Create or replace one grader's grade of one URL, index projection included.

        Writes the canonical x402_grades row before touching the
        x402_graded_endpoints projection (store before mark, CLAUDE.md section
        2 invariant 2): if the projection write then fails, the paid grade is
        durably stored and the URL is merely missing from the FREE index, which
        the next grade of that URL repairs. The reverse order could advertise a
        URL as graded when the grade the payer bought was never stored.

        The projection row is read before it is rewritten so last_graded_at
        only moves forward. A full INSERT of every column, never a partial
        UPDATE: a partial write would upsert a row whose unwritten columns read
        back as null, the phantom-row class articles_feed hit (CLAUDE.md
        section 3). Unlike the directory's and the board's recency projections
        there is no delete-then-insert dance here, because url_hash is the
        projection's clustering key and does not change -- one INSERT addresses
        and replaces the same row.
        """
        session = get_cassandra_session()
        session.execute(
            X402GradingStmts.UPSERT_GRADE,
            (
                item.url_hash,
                item.grader,
                item.url,
                item.score,
                item.comment,
                item.settlement_tx_id,
                _dt(item.created_at_epoch),
            ),
        )
        previous = session.execute(
            X402GradingStmts.GET_GRADED_ENDPOINT, (GRADING_PARTITION, item.url_hash)
        ).one()
        last_graded = max(item.created_at_epoch, _epoch(previous.last_graded_at) if previous else 0)
        session.execute(
            X402GradingStmts.INSERT_GRADED_ENDPOINT,
            (GRADING_PARTITION, item.url_hash, item.url, _dt(last_graded)),
        )

    def get(self, url_hash: str, grader: str) -> StoredGrade | None:
        """Return this grader's grade of this URL, or None if they have not graded it."""
        session = get_cassandra_session()
        row = session.execute(X402GradingStmts.GET_GRADE, (url_hash, grader)).one()
        return None if row is None else _row_to_grade(row)

    def list_for_url(self, url_hash: str, *, limit: int) -> list[StoredGrade]:
        """Return grades of one URL, at most `limit` of them."""
        session = get_cassandra_session()
        rows = session.execute(X402GradingStmts.LIST_GRADES, (url_hash, limit))
        return [_row_to_grade(row) for row in rows]

    def get_graded_endpoint(self, url_hash: str) -> GradedEndpoint | None:
        """Return the index entry for one URL, or None if nobody has graded it."""
        session = get_cassandra_session()
        row = session.execute(
            X402GradingStmts.GET_GRADED_ENDPOINT, (GRADING_PARTITION, url_hash)
        ).one()
        return None if row is None else _row_to_indexed(row)

    def list_graded_endpoints(self, *, limit: int) -> list[GradedEndpoint]:
        """Return URLs that have at least one grade, at most `limit` of them."""
        session = get_cassandra_session()
        rows = session.execute(X402GradingStmts.LIST_GRADED_ENDPOINTS, (GRADING_PARTITION, limit))
        return [_row_to_indexed(row) for row in rows]

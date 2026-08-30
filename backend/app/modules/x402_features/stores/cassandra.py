"""Cassandra-backed x402 feature-request storage."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.cassandra import execute_parallel_with_args, get_cassandra_session
from app.core.statements import X402FeaturesStmts
from app.modules.x402_features.models.domain import (
    CLAIMS_SCAN_LIMIT,
    FEATURES_PARTITION,
    ClaimSummary,
    StoredClaim,
    StoredFeatureRequest,
    StoredVote,
)


def _dt(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


def _epoch(value: datetime | None) -> int:
    return int(value.timestamp()) if value else 0


def _row_to_request(row: object) -> StoredFeatureRequest:
    return StoredFeatureRequest(
        request_id=row.request_id,
        title=row.title or "",
        description=row.description or "",
        submitter=row.submitter or "",
        settlement_tx_id=row.settlement_tx_id or "",
        created_at_epoch=_epoch(row.created_at),
    )


class CassandraFeatureStore:
    """Cassandra-backed x402 feature-request storage."""

    def insert(self, item: StoredFeatureRequest) -> None:
        """Store one new feature request, recency projection included.

        Writes the canonical x402_feature_requests row before the recency
        projection (store before mark): if the projection write then fails, the
        request exists and is votable by id, missing only from the browse feed.
        The reverse order could put a row in the public feed pointing at a
        request that was never durably stored.

        No delete-then-insert dance on the projection, unlike the board's
        upsert: a feature request is created once and never re-stamped, so
        there is never a superseded projection row to clean up.
        """
        session = get_cassandra_session()
        session.execute(
            X402FeaturesStmts.INSERT_REQUEST,
            (
                item.request_id,
                item.title,
                item.description,
                item.submitter,
                item.settlement_tx_id,
                _dt(item.created_at_epoch),
            ),
        )
        session.execute(
            X402FeaturesStmts.INSERT_RECENCY,
            (
                FEATURES_PARTITION,
                _dt(item.created_at_epoch),
                item.request_id,
                item.title,
                item.description,
                item.submitter,
                item.settlement_tx_id,
            ),
        )

    def get(self, request_id: str) -> StoredFeatureRequest | None:
        """Return the request for an id, or None if there is none."""
        session = get_cassandra_session()
        row = session.execute(X402FeaturesStmts.GET_REQUEST, (request_id,)).one()
        return None if row is None else _row_to_request(row)

    def list_recent(self, *, limit: int) -> list[StoredFeatureRequest]:
        """Return requests newest-first, at most `limit` of them."""
        session = get_cassandra_session()
        rows = session.execute(X402FeaturesStmts.LIST_RECENT, (FEATURES_PARTITION, limit))
        return [_row_to_request(row) for row in rows]

    def increment_vote_total(self, request_id: str) -> None:
        """Add one to a request's demand total, atomically.

        A Cassandra counter column, which is a true atomic add-one at the
        replica -- two concurrent votes both land, where a read-modify-write
        (or an LWT retry loop) would either lose one or need Paxos on every
        vote. Counter updates are not idempotent under a client-side retry, so
        this is issued exactly once and never wrapped in a retry: a vote that
        fails here surfaces as an error rather than risking a double count.
        """
        session = get_cassandra_session()
        session.execute(X402FeaturesStmts.INCREMENT_VOTE_TOTAL, (request_id,))

    def get_vote_total(self, request_id: str) -> int:
        """Return a request's current demand total, 0 if it has never been voted on."""
        session = get_cassandra_session()
        row = session.execute(X402FeaturesStmts.GET_VOTE_TOTAL, (request_id,)).one()
        return int(row.vote_total or 0) if row is not None else 0

    def get_vote_totals(self, request_ids: list[str]) -> dict[str, int]:
        """Return demand totals for many requests at once, keyed by request id.

        Concurrent point reads via the shared execute_parallel_with_args helper
        rather than one `WHERE request_id IN ?`: each id is its own partition,
        so an IN would make a single coordinator fan out and wait on every
        replica serially -- the well-known multi-partition IN anti-pattern.
        Results come back in input order, so they zip against the ids.

        raise_on_error is left at its default: a demand read that silently
        dropped some totals would report a wrong ranking as if it were right,
        and the caller has paid for the ranking.
        """
        if not request_ids:
            return {}
        results = execute_parallel_with_args(
            X402FeaturesStmts.GET_VOTE_TOTAL, [(rid,) for rid in request_ids]
        )
        totals: dict[str, int] = {}
        for request_id, (_success, result) in zip(request_ids, results, strict=True):
            row = result.one()
            if row is not None:
                totals[request_id] = int(row.vote_total or 0)
        return totals

    def append_vote(self, vote: StoredVote) -> None:
        """Append one vote to a request's audit log."""
        session = get_cassandra_session()
        session.execute(
            X402FeaturesStmts.INSERT_VOTE,
            (
                vote.request_id,
                _dt(vote.voted_at_epoch),
                vote.settlement_tx_id,
                vote.voter,
            ),
        )

    def append_claim(self, claim: StoredClaim) -> None:
        """Append one paid build claim to a request (full INSERT of every column)."""
        session = get_cassandra_session()
        session.execute(
            X402FeaturesStmts.INSERT_CLAIM,
            (
                claim.request_id,
                _dt(claim.claimed_at_epoch),
                claim.claimer,
                claim.settlement_tx_id,
            ),
        )

    def get_claim_summaries(self, request_ids: list[str]) -> dict[str, ClaimSummary]:
        """Return (count, latest claimer) per request via concurrent bounded partition reads.

        Same shape as get_vote_totals: one partition per request, read
        concurrently rather than with a multi-partition IN, results zipped in
        input order. Each read is newest-first and LIMITed to
        CLAIMS_SCAN_LIMIT, so the first row is the latest claimer and the row
        count saturates at the bound.
        """
        if not request_ids:
            return {}
        results = execute_parallel_with_args(
            X402FeaturesStmts.LIST_CLAIMS, [(rid, CLAIMS_SCAN_LIMIT) for rid in request_ids]
        )
        summaries: dict[str, ClaimSummary] = {}
        for request_id, (_success, result) in zip(request_ids, results, strict=True):
            rows = list(result)
            if rows:
                summaries[request_id] = ClaimSummary(
                    count=len(rows), latest_claimer=rows[0].claimer or ""
                )
        return summaries

    def delete(self, request_id: str) -> bool:
        """Remove one request, its recency row and its claims. False if it did not exist.

        Point deletes only: the recency row needs the created_at read from the
        canonical row (it is a clustering column), the claims are one whole
        partition. Projection and claims go first, the canonical row last, so
        a crash mid-way leaves the request gone from the public feed but still
        resolvable by id -- the safer half-done state. The vote counter and
        audit log are kept (see the Protocol).
        """
        session = get_cassandra_session()
        existing = self.get(request_id)
        if existing is None:
            return False
        session.execute(
            X402FeaturesStmts.DELETE_RECENCY,
            (FEATURES_PARTITION, _dt(existing.created_at_epoch), request_id),
        )
        session.execute(X402FeaturesStmts.DELETE_CLAIMS, (request_id,))
        session.execute(X402FeaturesStmts.DELETE_REQUEST, (request_id,))
        return True

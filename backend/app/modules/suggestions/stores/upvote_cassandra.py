"""Cassandra-backed upvote storage."""

from __future__ import annotations

from datetime import UTC, datetime

from app.modules.suggestions.models.domain import UpvoteError


class CassandraUpvoteStore:
    """Cassandra-backed upvote storage."""
    def record_upvote(self, suggestion_id: str, wallet_address: str) -> int:
        """Record a wallet's upvote on a suggestion and return the new count; raises UpvoteError on a duplicate vote."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import UpvoteStmts

        session = get_cassandra_session()
        existing = session.execute(UpvoteStmts.GET, (suggestion_id, wallet_address)).one()
        if existing is not None:
            raise UpvoteError("duplicate_upvote", "Wallet already upvoted this suggestion")

        session.execute(
            UpvoteStmts.INSERT,
            (suggestion_id, wallet_address, datetime.now(tz=UTC)),
        )
        rows = session.execute(UpvoteStmts.COUNT, (suggestion_id,))
        row = rows.one()
        return int(row.count) if row is not None else 1

    def count(self, suggestion_id: str) -> int:
        """Return the current upvote count for a suggestion."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import UpvoteStmts

        session = get_cassandra_session()
        rows = session.execute(UpvoteStmts.COUNT, (suggestion_id,))
        row = rows.one()
        return int(row.count) if row is not None else 0

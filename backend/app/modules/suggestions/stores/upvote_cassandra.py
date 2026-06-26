from __future__ import annotations

from datetime import UTC, datetime

from app.modules.suggestions.models.domain import UpvoteError


class CassandraUpvoteStore:
    def record_upvote(self, suggestion_id: str, wallet_address: str) -> int:
        from app.core.cassandra import get_cassandra_session

        session = get_cassandra_session()
        existing = session.execute(
            """
            SELECT wallet_address FROM upvotes_by_suggestion
            WHERE suggestion_id = %s AND wallet_address = %s
            """,
            (suggestion_id, wallet_address),
        ).one()
        if existing is not None:
            raise UpvoteError("duplicate_upvote", "Wallet already upvoted this suggestion")

        session.execute(
            """
            INSERT INTO upvotes_by_suggestion (suggestion_id, wallet_address, created_at)
            VALUES (%s, %s, %s)
            """,
            (suggestion_id, wallet_address, datetime.now(tz=UTC)),
        )
        rows = session.execute(
            "SELECT COUNT(*) FROM upvotes_by_suggestion WHERE suggestion_id = %s",
            (suggestion_id,),
        )
        row = rows.one()
        return int(row.count) if row is not None else 1

    def count(self, suggestion_id: str) -> int:
        from app.core.cassandra import get_cassandra_session

        session = get_cassandra_session()
        rows = session.execute(
            "SELECT COUNT(*) FROM upvotes_by_suggestion WHERE suggestion_id = %s",
            (suggestion_id,),
        )
        row = rows.one()
        return int(row.count) if row is not None else 0

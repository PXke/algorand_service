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
        return self.count_many([suggestion_id]).get(suggestion_id, 0)

    def count_many(self, suggestion_ids: list[str]) -> dict[str, int]:
        """Fan-out COUNT queries for many suggestions; results keyed by id."""
        from app.core.cassandra import execute_parallel_with_args
        from app.core.statements import UpvoteStmts

        ids = [sid for sid in suggestion_ids if sid]
        if not ids:
            return {}
        out: dict[str, int] = dict.fromkeys(ids, 0)
        for sid, (ok, result) in zip(
            ids,
            execute_parallel_with_args(
                UpvoteStmts.COUNT, [(sid,) for sid in ids], raise_on_error=False
            ),
            strict=True,
        ):
            if not ok:
                continue
            row = result.one() if hasattr(result, "one") else None
            if row is not None and getattr(row, "count", None) is not None:
                out[sid] = int(row.count)
        return out

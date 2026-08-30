"""Storage interface for feature requests and the paid votes cast on them."""

from __future__ import annotations

from typing import Protocol

from app.modules.x402_features.models.domain import StoredFeatureRequest, StoredVote


class FeatureStore(Protocol):
    """Storage interface for the x402 feature-request board."""

    def insert(self, item: StoredFeatureRequest) -> None:
        """Store one new feature request, recency projection included."""
        ...

    def get(self, request_id: str) -> StoredFeatureRequest | None:
        """Return the request for an id, or None if there is none."""
        ...

    def list_recent(self, *, limit: int) -> list[StoredFeatureRequest]:
        """Return requests newest-first, at most `limit` of them."""
        ...

    def increment_vote_total(self, request_id: str) -> None:
        """Add one to a request's demand total, atomically.

        Must never lose a concurrent increment. Two votes settling at the same
        instant are two payments, and a demand board that silently merges them
        into one is under-reporting paid signal -- so this is a real atomic
        add-one, not a read-modify-write. See the Cassandra backend (a counter
        column) and the memory backend (a lock) for how each keeps that
        promise.
        """
        ...

    def get_vote_total(self, request_id: str) -> int:
        """Return a request's current demand total, 0 if it has never been voted on."""
        ...

    def get_vote_totals(self, request_ids: list[str]) -> dict[str, int]:
        """Return demand totals for many requests at once, keyed by request id.

        Its own method rather than a loop over get_vote_total at the call site:
        the demand ranking needs every candidate's total, and issuing those as
        sequential round trips would make the paid read's latency scale with
        the size of the board. Ids with no votes may be omitted; the caller
        treats a missing id as 0.
        """
        ...

    def append_vote(self, vote: StoredVote) -> None:
        """Append one vote to a request's audit log.

        Separate from increment_vote_total so the service, not each backend,
        owns the order the two happen in and what a failure of each means.
        """
        ...

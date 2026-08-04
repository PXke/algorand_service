"""In-memory upvote store for tests."""

from __future__ import annotations

from app.modules.suggestions.models.domain import UpvoteError


class InMemoryUpvoteStore:
    """In-memory upvote store for tests."""

    def __init__(self) -> None:
        """Start with an empty in-process vote set and per-suggestion counts."""
        self._votes: set[tuple[str, str]] = set()
        self._counts: dict[str, int] = {}

    def record_upvote(self, suggestion_id: str, wallet_address: str) -> int:
        """Record a wallet's upvote on a suggestion and return the new count; raises UpvoteError on a duplicate vote."""
        key = (suggestion_id, wallet_address)
        if key in self._votes:
            raise UpvoteError("duplicate_upvote", "Wallet already upvoted this suggestion")
        self._votes.add(key)
        self._counts[suggestion_id] = self._counts.get(suggestion_id, 0) + 1
        return self._counts[suggestion_id]

    def count(self, suggestion_id: str) -> int:
        """Return the current upvote count for a suggestion."""
        return self._counts.get(suggestion_id, 0)

    def count_many(self, suggestion_ids: list[str]) -> dict[str, int]:
        """Return upvote counts for many suggestions."""
        return {sid: self.count(sid) for sid in suggestion_ids}

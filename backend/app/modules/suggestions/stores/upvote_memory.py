from __future__ import annotations

from app.modules.suggestions.models.domain import UpvoteError


class InMemoryUpvoteStore:
    def __init__(self) -> None:
        self._votes: set[tuple[str, str]] = set()
        self._counts: dict[str, int] = {}

    def record_upvote(self, suggestion_id: str, wallet_address: str) -> int:
        key = (suggestion_id, wallet_address)
        if key in self._votes:
            raise UpvoteError("duplicate_upvote", "Wallet already upvoted this suggestion")
        self._votes.add(key)
        self._counts[suggestion_id] = self._counts.get(suggestion_id, 0) + 1
        return self._counts[suggestion_id]

    def count(self, suggestion_id: str) -> int:
        return self._counts.get(suggestion_id, 0)

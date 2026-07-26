"""Upvote-store singleton wiring, swappable for tests."""

from __future__ import annotations

from typing import Protocol

from app.core.config import settings
from app.modules.suggestions.stores.upvote_cassandra import CassandraUpvoteStore
from app.modules.suggestions.stores.upvote_memory import InMemoryUpvoteStore


class UpvoteStore(Protocol):
    """Storage interface for upvotes."""
    def record_upvote(self, suggestion_id: str, wallet_address: str) -> int:
        """Record a wallet's upvote on a suggestion and return the new count."""
        ...

    def count(self, suggestion_id: str) -> int:
        """Return the current upvote count for a suggestion."""
        ...


_upvote_store: UpvoteStore | None = None


def get_upvote_store() -> UpvoteStore:
    """Return the process-wide upvote store, creating it from settings on first use."""
    global _upvote_store
    if _upvote_store is None:
        backend = settings.upvote_store.strip().lower()
        _upvote_store = CassandraUpvoteStore() if backend == "cassandra" else InMemoryUpvoteStore()
    return _upvote_store



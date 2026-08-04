"""Upvote-store singleton wiring, swappable for tests."""

from __future__ import annotations

from typing import Protocol

from app.core.config import settings
from app.core.store_factory import StoreFactory
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

    def count_many(self, suggestion_ids: list[str]) -> dict[str, int]:
        """Return upvote counts for many suggestions (prefer a fan-out)."""
        ...


_factory: StoreFactory[UpvoteStore] = StoreFactory(
    backend_name=lambda: settings.upvote_store,
    cassandra=CassandraUpvoteStore,
    memory=InMemoryUpvoteStore,
)


def get_upvote_store() -> UpvoteStore:
    """Return the process-wide upvote store, built from settings on first use."""
    return _factory.get()

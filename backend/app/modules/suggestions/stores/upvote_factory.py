from __future__ import annotations

from typing import Protocol

from app.core.config import settings
from app.modules.suggestions.stores.upvote_cassandra import CassandraUpvoteStore
from app.modules.suggestions.stores.upvote_memory import InMemoryUpvoteStore


class UpvoteStore(Protocol):
    def record_upvote(self, suggestion_id: str, wallet_address: str) -> int: ...

    def count(self, suggestion_id: str) -> int: ...


_upvote_store: UpvoteStore | None = None


def get_upvote_store() -> UpvoteStore:
    global _upvote_store
    if _upvote_store is None:
        backend = settings.upvote_store.strip().lower()
        _upvote_store = CassandraUpvoteStore() if backend == "cassandra" else InMemoryUpvoteStore()
    return _upvote_store


def set_upvote_store(store: UpvoteStore | None) -> None:
    global _upvote_store
    _upvote_store = store

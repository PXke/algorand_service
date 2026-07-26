"""Suggestion-store singleton wiring, swappable for tests."""

from __future__ import annotations

from app.core.config import settings
from app.core.store_factory import StoreFactory
from app.modules.suggestions.stores.base import SuggestionStore
from app.modules.suggestions.stores.cassandra import CassandraSuggestionStore
from app.modules.suggestions.stores.memory import InMemorySuggestionStore

_factory: StoreFactory[SuggestionStore] = StoreFactory(
    backend_name=lambda: settings.suggestion_store,
    cassandra=CassandraSuggestionStore,
    memory=InMemorySuggestionStore,
)


def get_suggestion_store() -> SuggestionStore:
    """Return the process-wide suggestion store, built from settings on first use."""
    return _factory.get()

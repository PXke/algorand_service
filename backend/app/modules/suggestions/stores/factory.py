"""Suggestion-store singleton wiring, swappable for tests."""

from __future__ import annotations

from app.core.config import settings
from app.modules.suggestions.stores.base import SuggestionStore
from app.modules.suggestions.stores.cassandra import CassandraSuggestionStore
from app.modules.suggestions.stores.memory import InMemorySuggestionStore

_suggestion_store: SuggestionStore | None = None


def get_suggestion_store() -> SuggestionStore:
    """Return the process-wide suggestion store, lazily built from settings."""
    global _suggestion_store
    if _suggestion_store is None:
        backend = settings.suggestion_store.strip().lower()
        if backend == "cassandra":
            _suggestion_store = CassandraSuggestionStore()
        else:
            _suggestion_store = InMemorySuggestionStore()
    return _suggestion_store


def set_suggestion_store(store: SuggestionStore | None) -> None:
    """Override the process-wide suggestion store, e.g. for tests."""
    global _suggestion_store
    _suggestion_store = store

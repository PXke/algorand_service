"""Storage interface for suggestions."""

from __future__ import annotations

from typing import Protocol

from app.modules.suggestions.models.domain import StoredSuggestion


class SuggestionStore(Protocol):
    """Storage interface for suggestions."""

    def insert(self, item: StoredSuggestion) -> None:
        """Insert a new suggestion."""
        ...

    def list_open(self) -> list[StoredSuggestion]:
        """List open suggestions."""
        ...

    def get(self, suggestion_id: str) -> StoredSuggestion | None:
        """Fetch one suggestion by id, or None if it does not exist."""
        ...

    def has_submission_txid(self, submission_txid: str) -> bool:
        """Check whether a submission txid has already been used."""
        ...

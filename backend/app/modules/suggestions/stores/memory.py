"""In-memory suggestion store for tests."""

from __future__ import annotations

from app.modules.suggestions.models.domain import StoredSuggestion, SuggestionError


class InMemorySuggestionStore:
    """In-memory suggestion store for tests."""

    def __init__(self) -> None:
        """Start with an empty in-process suggestion table and txid index."""
        self._items: dict[str, StoredSuggestion] = {}
        self._txid_index: dict[str, str] = {}

    def insert(self, item: StoredSuggestion) -> None:
        """Insert a new suggestion; raises SuggestionError if the txid was already used."""
        if item.submission_txid in self._txid_index:
            msg = "submission_txid already used"
            raise SuggestionError("duplicate_txid", msg)
        self._items[item.suggestion_id] = item
        self._txid_index[item.submission_txid] = item.suggestion_id

    def list_open(self) -> list[StoredSuggestion]:
        """List open suggestions."""
        return [item for item in self._items.values() if item.status == "open"]

    def get(self, suggestion_id: str) -> StoredSuggestion | None:
        """Fetch one suggestion by id, or None if it does not exist."""
        return self._items.get(suggestion_id)

    def has_submission_txid(self, submission_txid: str) -> bool:
        """Check whether a submission txid has already been used."""
        return submission_txid in self._txid_index

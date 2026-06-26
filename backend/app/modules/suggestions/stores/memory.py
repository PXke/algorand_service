from __future__ import annotations

from app.modules.suggestions.models.domain import StoredSuggestion, SuggestionError


class InMemorySuggestionStore:
    def __init__(self) -> None:
        self._items: dict[str, StoredSuggestion] = {}
        self._txid_index: dict[str, str] = {}

    def insert(self, item: StoredSuggestion) -> None:
        if item.submission_txid in self._txid_index:
            msg = "submission_txid already used"
            raise SuggestionError("duplicate_txid", msg)
        self._items[item.suggestion_id] = item
        self._txid_index[item.submission_txid] = item.suggestion_id

    def list_open(self) -> list[StoredSuggestion]:
        return [item for item in self._items.values() if item.status == "open"]

    def get(self, suggestion_id: str) -> StoredSuggestion | None:
        return self._items.get(suggestion_id)

    def has_submission_txid(self, submission_txid: str) -> bool:
        return submission_txid in self._txid_index

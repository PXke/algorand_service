from __future__ import annotations

from typing import Protocol

from app.modules.suggestions.models.domain import StoredSuggestion


class SuggestionStore(Protocol):
    def insert(self, item: StoredSuggestion) -> None: ...

    def list_open(self) -> list[StoredSuggestion]: ...

    def get(self, suggestion_id: str) -> StoredSuggestion | None: ...

    def has_submission_txid(self, submission_txid: str) -> bool: ...

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import PlatformError, http_status_for_code


class SuggestionError(PlatformError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, http_status=http_status_for_code(code))


class UpvoteError(PlatformError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, http_status=http_status_for_code(code))


@dataclass
class StoredSuggestion:
    suggestion_id: str
    wallet_address: str
    title: str
    body: str
    submission_txid: str
    status: str
    created_at_epoch: int

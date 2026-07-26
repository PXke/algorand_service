"""Domain types for suggestion/upvote errors and stored suggestions."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import PlatformError, http_status_for_code


class SuggestionError(PlatformError):
    """A suggestion-flow error mapped to an HTTP status."""

    def __init__(self, code: str, message: str) -> None:
        """Map a suggestion error code to its HTTP status via http_status_for_code."""
        super().__init__(code, message, http_status=http_status_for_code(code))


class UpvoteError(PlatformError):
    """An upvote-flow error mapped to an HTTP status."""

    def __init__(self, code: str, message: str) -> None:
        """Map an upvote error code to its HTTP status via http_status_for_code."""
        super().__init__(code, message, http_status=http_status_for_code(code))


@dataclass
class StoredSuggestion:
    """A stored service suggestion."""

    suggestion_id: str
    wallet_address: str
    title: str
    body: str
    submission_txid: str
    status: str
    created_at_epoch: int

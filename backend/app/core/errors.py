"""Structured application error base class mapping to an HTTP status."""

from __future__ import annotations


class PlatformError(Exception):
    """Structured application error with HTTP status and machine-readable code."""

    def __init__(self, code: str, message: str, *, http_status: int = 400) -> None:
        """Set the machine-readable code, message, and HTTP status this error maps to."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def http_status_for_code(code: str, *, default: int = 400) -> int:
    """Look up the HTTP status conventionally mapped to a machine-readable error code."""
    mapping = {
        "unauthorized": 401,
        "invalid_signature_or_nonce": 401,
        "missing_session_token": 401,
        "invalid_or_expired_session": 401,
        "not_found": 404,
        "duplicate_txid": 409,
        "duplicate_upvote": 409,
        "listing_owned_by_another_payer": 403,
        "rate_limited": 429,
        "treasury_not_configured": 503,
    }
    return mapping.get(code, default)

from __future__ import annotations


class PlatformError(Exception):
    """Structured application error with HTTP status and machine-readable code."""

    def __init__(self, code: str, message: str, *, http_status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def http_status_for_code(code: str, *, default: int = 400) -> int:
    mapping = {
        "unauthorized": 401,
        "invalid_signature_or_nonce": 401,
        "missing_session_token": 401,
        "invalid_or_expired_session": 401,
        "not_found": 404,
        "duplicate_txid": 409,
        "duplicate_upvote": 409,
        "rate_limited": 429,
        "treasury_not_configured": 503,
    }
    return mapping.get(code, default)

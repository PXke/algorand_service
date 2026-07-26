"""Domain types for KYC errors and stored enrollments."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import PlatformError, http_status_for_code


class KycError(PlatformError):
    """A KYC-flow error mapped to an HTTP status."""

    def __init__(self, code: str, message: str) -> None:
        """Map a KYC error code to its HTTP status via http_status_for_code."""
        super().__init__(code, message, http_status=http_status_for_code(code))


@dataclass
class StoredEnrollment:
    """A wallet's stored KYC enrollment record."""

    wallet_address: str
    enrolled_at_epoch: int
    updated_at_epoch: int
    consent_signature_b64: str
    wallet_age_round: int | None
    recent_tx_count: int
    kyc_level: str

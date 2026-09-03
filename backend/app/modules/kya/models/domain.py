"""Domain types for KYA errors and stored enrollments."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import PlatformError, http_status_for_code


class KycError(PlatformError):
    """A KYA-flow error mapped to an HTTP status."""

    def __init__(self, code: str, message: str) -> None:
        """Map a KYA error code to its HTTP status via http_status_for_code."""
        super().__init__(code, message, http_status=http_status_for_code(code))


@dataclass
class StoredEnrollment:
    """A wallet's stored KYA enrollment record."""

    wallet_address: str
    enrolled_at_epoch: int
    updated_at_epoch: int
    consent_signature_b64: str
    wallet_age_round: int | None
    recent_tx_count: int
    kyc_level: str


@dataclass
class StoredLookupEvent:
    """One recorded paid KYA lookup and its payout outcome (kyc_lookup_events).

    `created_at` is the row's clustering-key value (a Cassandra timeuuid, or
    an opaque per-process surrogate for the in-memory store) — callers never
    interpret it, only pass it back to address the same row for an update.
    """

    wallet_address: str
    created_at: object
    payer_address: str
    payment_txid: str
    found: bool
    payout_status: str
    payout_txid: str | None
    payout_error: str | None

"""Storage interface for KYC enrollments and lookup events."""

from __future__ import annotations

from typing import Protocol

from app.modules.kyc.models.domain import StoredEnrollment


class EnrollmentStore(Protocol):
    """Storage interface for KYC enrollments."""
    def upsert(self, item: StoredEnrollment) -> None:
        """Insert or update a KYC enrollment record."""
        ...

    def get(self, wallet_address: str) -> StoredEnrollment | None:
        """Look up a wallet's stored enrollment, or None if not enrolled."""
        ...

    def record_lookup_event(
        self,
        *,
        wallet_address: str,
        payer_address: str,
        payment_txid: str,
        found: bool,
        payout_status: str,
        payout_txid: str | None = None,
        payout_error: str | None = None,
    ) -> None:
        """Record the outcome of one paid lookup, for audit and payout retry."""
        ...

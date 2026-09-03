"""Storage interface for KYA enrollments and lookup events."""

from __future__ import annotations

from typing import Protocol

from app.modules.kya.models.domain import StoredEnrollment, StoredLookupEvent


class EnrollmentStore(Protocol):
    """Storage interface for KYA enrollments."""

    def upsert(self, item: StoredEnrollment) -> None:
        """Insert or update a KYA enrollment record."""
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

    def find_lookup_event(
        self, *, wallet_address: str, payment_txid: str
    ) -> StoredLookupEvent | None:
        """The recorded lookup event for this wallet+payment_txid, or None if there isn't one.

        The source of truth a payout retry reconciles against — never the
        retry request's own caller-supplied fields.
        """
        ...

    def update_lookup_event_payout(
        self,
        *,
        wallet_address: str,
        created_at: object,
        payout_status: str,
        payout_txid: str | None,
        payout_error: str | None,
    ) -> None:
        """Update one lookup event's payout outcome in place (after a payout retry)."""
        ...

"""In-memory KYC enrollment store for tests."""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.kyc.models.domain import StoredEnrollment


@dataclass
class _LookupEvent:
    wallet_address: str
    payer_address: str
    payment_txid: str
    found: bool
    payout_status: str
    payout_txid: str | None
    payout_error: str | None


class InMemoryEnrollmentStore:
    """In-memory KYC enrollment store for tests."""

    def __init__(self) -> None:
        """Start with an empty in-process enrollment table and lookup-event log."""
        self._items: dict[str, StoredEnrollment] = {}
        self._lookup_events: list[_LookupEvent] = []

    def upsert(self, item: StoredEnrollment) -> None:
        """Insert or update a KYC enrollment record."""
        self._items[item.wallet_address] = item

    def get(self, wallet_address: str) -> StoredEnrollment | None:
        """Look up a wallet's stored enrollment, or None if not enrolled."""
        return self._items.get(wallet_address)

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
        self._lookup_events.append(
            _LookupEvent(
                wallet_address=wallet_address,
                payer_address=payer_address,
                payment_txid=payment_txid,
                found=found,
                payout_status=payout_status,
                payout_txid=payout_txid,
                payout_error=payout_error,
            )
        )

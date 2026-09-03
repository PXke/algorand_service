"""In-memory KYA enrollment store for tests."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from app.modules.kya.models.domain import StoredEnrollment, StoredLookupEvent

_created_at_counter = itertools.count()


@dataclass
class _LookupEvent:
    wallet_address: str
    payer_address: str
    payment_txid: str
    found: bool
    payout_status: str
    payout_txid: str | None
    payout_error: str | None
    # Opaque per-process surrogate for Cassandra's server-generated timeuuid
    # clustering key -- monotonically increasing is all a caller ever needs
    # from it (identify-then-update the same row).
    created_at: int = field(default_factory=lambda: next(_created_at_counter))


class InMemoryEnrollmentStore:
    """In-memory KYA enrollment store for tests."""

    def __init__(self) -> None:
        """Start with an empty in-process enrollment table and lookup-event log."""
        self._items: dict[str, StoredEnrollment] = {}
        self._lookup_events: list[_LookupEvent] = []

    def upsert(self, item: StoredEnrollment) -> None:
        """Insert or update a KYA enrollment record."""
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

    def find_lookup_event(
        self, *, wallet_address: str, payment_txid: str
    ) -> StoredLookupEvent | None:
        """The recorded lookup event for this wallet+payment_txid, or None if there isn't one."""
        for event in self._lookup_events:
            if event.wallet_address == wallet_address and event.payment_txid == payment_txid:
                return StoredLookupEvent(
                    wallet_address=event.wallet_address,
                    created_at=event.created_at,
                    payer_address=event.payer_address,
                    payment_txid=event.payment_txid,
                    found=event.found,
                    payout_status=event.payout_status,
                    payout_txid=event.payout_txid,
                    payout_error=event.payout_error,
                )
        return None

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
        for event in self._lookup_events:
            if event.wallet_address == wallet_address and event.created_at == created_at:
                event.payout_status = payout_status
                event.payout_txid = payout_txid
                event.payout_error = payout_error
                return

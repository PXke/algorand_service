"""Storage interface for directory listings and settlement records."""

from __future__ import annotations

from typing import Protocol

from app.modules.x402_directory.models.domain import SettlementRecord, StoredListing


class ListingStore(Protocol):
    """Storage interface for x402 directory listings and the settlement ledger."""

    def upsert(self, item: StoredListing) -> None:
        """Create or replace the listing for one endpoint URL, feed projection included."""
        ...

    def get(self, url_hash: str) -> StoredListing | None:
        """Return the current listing for a URL hash, or None if not listed."""
        ...

    def list_recent(self, *, limit: int) -> list[StoredListing]:
        """Return listings newest-first, at most `limit` of them."""
        ...

    def record_settlement(self, item: SettlementRecord) -> None:
        """Append one settled payment to the bookkeeping ledger."""
        ...

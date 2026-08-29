"""Storage interface for directory listings.

The settlement ledger has its own SettlementStore Protocol in
modules/x402/settlement.py -- see that module's docstring for why it moved
out of here.
"""

from __future__ import annotations

from typing import Protocol

from app.modules.x402_directory.models.domain import StoredListing


class ListingStore(Protocol):
    """Storage interface for x402 directory listings."""

    def upsert(self, item: StoredListing) -> None:
        """Create or replace the listing for one endpoint URL, feed projection included."""
        ...

    def get(self, url_hash: str) -> StoredListing | None:
        """Return the current listing for a URL hash, or None if not listed."""
        ...

    def list_recent(self, *, limit: int) -> list[StoredListing]:
        """Return listings newest-first, at most `limit` of them."""
        ...

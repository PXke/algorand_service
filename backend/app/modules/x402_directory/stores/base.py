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

    def insert_if_absent(self, item: StoredListing) -> bool:
        """Store the listing only if no listing exists for its url_hash, projections included.

        Returns True if this call created the listing, False if a listing for
        that url_hash already existed (nothing is written in that case). The
        check-and-write is atomic per store, so two concurrent callers for
        the same url_hash cannot both get True.
        """
        ...

    def upsert(self, item: StoredListing) -> None:
        """Create or replace the listing for one endpoint URL, projections included."""
        ...

    def get(self, url_hash: str) -> StoredListing | None:
        """Return the current listing for a URL hash, or None if not listed."""
        ...

    def list_recent(self, *, limit: int) -> list[StoredListing]:
        """Return listings newest-first, at most `limit` of them."""
        ...

    def list_by_tag(self, tag: str, *, limit: int) -> list[StoredListing]:
        """Return listings carrying the (already normalized) tag, newest-first, at most `limit`."""
        ...

    def delete(self, url_hash: str) -> bool:
        """Remove the listing for a URL hash, projections included. False if it did not exist."""
        ...

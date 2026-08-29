"""In-memory x402 directory store for dev and tests."""

from __future__ import annotations

from app.modules.x402_directory.models.domain import SettlementRecord, StoredListing


class InMemoryListingStore:
    """In-memory x402 directory listing and settlement storage."""

    def __init__(self) -> None:
        """Start with an empty listing table and settlement ledger."""
        self._items: dict[str, StoredListing] = {}
        self.settlements: list[SettlementRecord] = []

    def upsert(self, item: StoredListing) -> None:
        """Create or replace the listing for one endpoint URL."""
        self._items[item.url_hash] = item

    def get(self, url_hash: str) -> StoredListing | None:
        """Return the current listing for a URL hash, or None if not listed."""
        return self._items.get(url_hash)

    def list_recent(self, *, limit: int) -> list[StoredListing]:
        """Return listings newest-first, at most `limit` of them.

        Ties on created_at break by url_hash ascending, matching the Cassandra
        table's (created_at DESC, url_hash ASC) clustering order so tests see
        the same ordering as production.
        """
        ordered = sorted(self._items.values(), key=lambda i: (-i.created_at_epoch, i.url_hash))
        return ordered[: max(0, limit)]

    def record_settlement(self, item: SettlementRecord) -> None:
        """Append one settled payment to the bookkeeping ledger."""
        self.settlements.append(item)

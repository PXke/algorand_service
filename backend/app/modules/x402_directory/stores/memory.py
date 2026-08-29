"""In-memory x402 directory store for dev and tests."""

from __future__ import annotations

from app.modules.x402_directory.models.domain import StoredListing


class InMemoryListingStore:
    """In-memory x402 directory listing storage."""

    def __init__(self) -> None:
        """Start with an empty listing table."""
        self._items: dict[str, StoredListing] = {}

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

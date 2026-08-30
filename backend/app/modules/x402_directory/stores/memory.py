"""In-memory x402 directory store for dev and tests."""

from __future__ import annotations

from app.modules.x402_directory.models.domain import StoredListing, StoredProbe


class InMemoryListingStore:
    """In-memory x402 directory listing storage.

    Keeps an explicit by-tag projection (`_by_tag`) rather than filtering
    `_items` on read, so it mirrors the Cassandra store's two-table shape and
    tests can observe that a delete really removes the projection rows.
    """

    def __init__(self) -> None:
        """Start with an empty listing table and an empty tag projection."""
        self._items: dict[str, StoredListing] = {}
        self._by_tag: dict[str, dict[str, StoredListing]] = {}
        self._probes: dict[str, StoredProbe] = {}

    def insert_if_absent(self, item: StoredListing) -> bool:
        """Store the listing only if its url_hash is not yet present. True if it was stored."""
        if item.url_hash in self._items:
            return False
        self._put(item, previous=None)
        return True

    def upsert(self, item: StoredListing) -> None:
        """Create or replace the listing for one endpoint URL, tag projection included."""
        self._put(item, previous=self._items.get(item.url_hash))

    def _put(self, item: StoredListing, *, previous: StoredListing | None) -> None:
        if previous is not None:
            for tag in previous.projection_tags():
                self._by_tag.get(tag, {}).pop(item.url_hash, None)
        self._items[item.url_hash] = item
        for tag in item.projection_tags():
            self._by_tag.setdefault(tag, {})[item.url_hash] = item

    def get(self, url_hash: str) -> StoredListing | None:
        """Return the current listing for a URL hash, or None if not listed."""
        return self._items.get(url_hash)

    @staticmethod
    def _newest_first(items: list[StoredListing], limit: int) -> list[StoredListing]:
        # Ties on created_at break by url_hash ascending, matching both
        # Cassandra projections' (created_at DESC, url_hash ASC) clustering
        # order so tests see the same ordering as production.
        ordered = sorted(items, key=lambda i: (-i.created_at_epoch, i.url_hash))
        return ordered[: max(0, limit)]

    def list_recent(self, *, limit: int) -> list[StoredListing]:
        """Return listings newest-first, at most `limit` of them."""
        return self._newest_first(list(self._items.values()), limit)

    def list_by_tag(self, tag: str, *, limit: int) -> list[StoredListing]:
        """Return listings carrying the tag, newest-first, at most `limit` of them."""
        return self._newest_first(list(self._by_tag.get(tag, {}).values()), limit)

    def tag_rows(self, tag: str) -> list[str]:
        """Test hook: the url_hashes currently held in the tag projection for `tag`."""
        return sorted(self._by_tag.get(tag, {}))

    def delete(self, url_hash: str) -> bool:
        """Remove the listing for a URL hash, tag projection included. False if it did not exist."""
        existing = self._items.pop(url_hash, None)
        if existing is None:
            return False
        for tag in existing.projection_tags():
            self._by_tag.get(tag, {}).pop(url_hash, None)
        return True

    def latest_probe(self, url_hash: str) -> StoredProbe | None:
        """Return the newest probe for a URL hash, or None if never probed."""
        return self._probes.get(url_hash)

    def record_probe(self, probe: StoredProbe) -> None:
        """Test hook standing in for the workers probe beat: store the latest probe row."""
        self._probes[probe.url_hash] = probe

"""In-memory x402 uptime-check history store for dev and tests."""

from __future__ import annotations

from app.modules.x402_uptime.models.domain import StoredUptimeCheck


class InMemoryUptimeHistoryStore:
    """In-memory uptime-check history, keyed the same way the Cassandra table is: url_hash."""

    def __init__(self) -> None:
        """Start with no recorded checks: {url_hash: [StoredUptimeCheck, ...]}."""
        self._items: dict[str, list[StoredUptimeCheck]] = {}

    def record(self, item: StoredUptimeCheck) -> None:
        """Append one real-check row."""
        self._items.setdefault(item.url_hash, []).append(item)

    def list_since(self, url_hash: str, *, since_epoch: int, limit: int) -> list[StoredUptimeCheck]:
        """Up to `limit` rows for this url_hash with checked_at_epoch >= since_epoch, newest first."""
        items = [c for c in self._items.get(url_hash, []) if c.checked_at_epoch >= since_epoch]
        ordered = sorted(items, key=lambda c: c.checked_at_epoch, reverse=True)
        return ordered[: max(0, limit)]

"""In-memory backup metadata store for dev and tests."""

from __future__ import annotations

from app.modules.x402_storage.models.domain import StoredBackup


class InMemoryBackupStore:
    """In-memory x402 storage backup metadata, keyed the same way the Cassandra table is: (wallet, backup_id)."""

    def __init__(self) -> None:
        """Start with no backups: {wallet: {backup_id: StoredBackup}}."""
        self._items: dict[str, dict[str, StoredBackup]] = {}

    def upsert(self, item: StoredBackup) -> None:
        """Create or replace one backup row."""
        self._items.setdefault(item.wallet, {})[item.backup_id] = item

    def get(self, wallet: str, backup_id: str) -> StoredBackup | None:
        """Return one backup row by (wallet, backup_id), or None if it does not exist."""
        return self._items.get(wallet, {}).get(backup_id)

    def list_recent(self, wallet: str, *, limit: int) -> list[StoredBackup]:
        """Return up to `limit` of this wallet's rows, newest first (by created_at, ties broken by backup_id descending -- mirrors Cassandra's CLUSTERING ORDER BY (backup_id DESC)).

        Two stable sorts (backup_id descending, then created_at descending)
        rather than one combined key: backup_id is a string (a timeuuid's
        text form), which cannot be numerically negated to reverse it within
        a single sort key the way created_at_epoch can.
        """
        items = list(self._items.get(wallet, {}).values())
        by_id = sorted(items, key=lambda i: i.backup_id, reverse=True)
        ordered = sorted(by_id, key=lambda i: i.created_at_epoch, reverse=True)
        return ordered[: max(0, limit)]

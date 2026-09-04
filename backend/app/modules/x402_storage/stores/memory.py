"""In-memory backup metadata store for dev and tests."""

from __future__ import annotations

from datetime import date

from app.modules.x402_storage.models.domain import (
    STATUS_ACTIVE,
    ExpiryIndexRow,
    StoredBackup,
    expiry_day_utc,
)


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

    def list_by_expiry_day(self, expiry_day: date, *, limit: int) -> list[ExpiryIndexRow]:
        """Scan in-memory rows whose expires_at UTC date matches `expiry_day`.

        No real projection table here -- the memory backend is tests/dev
        only, and the full set of rows is already in process. Filters to
        status=active the same way the Cassandra projection only holds
        currently-active keys.
        """
        found: list[ExpiryIndexRow] = []
        for items in self._items.values():
            for item in items.values():
                if item.status != STATUS_ACTIVE:
                    continue
                if expiry_day_utc(item.expires_at_epoch) != expiry_day:
                    continue
                found.append(
                    ExpiryIndexRow(
                        wallet=item.wallet,
                        backup_id=item.backup_id,
                        expires_at_epoch=item.expires_at_epoch,
                    )
                )
        found.sort(key=lambda r: (r.expires_at_epoch, r.wallet, r.backup_id))
        return found[: max(0, limit)]

    def delete_expiry_index(self, row: ExpiryIndexRow) -> None:
        """No-op: memory listing is derived from canonical rows, not a separate index."""
        _ = row

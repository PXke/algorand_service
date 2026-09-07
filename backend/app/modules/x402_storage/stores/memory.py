"""In-memory backup metadata store for dev and tests."""

from __future__ import annotations

from datetime import date

from app.modules.x402_storage.models.domain import (
    STATUS_ACTIVE,
    ExpiryIndexRow,
    StoredBackup,
    StoredBackupVersion,
    VersionExpiryIndexRow,
    expiry_day_utc,
)


class InMemoryBackupStore:
    """In-memory x402 storage backup metadata, keyed the same way the Cassandra table is: (wallet, backup_id)."""

    def __init__(self) -> None:
        """Start with no backups: {wallet: {backup_id: StoredBackup}}, {(wallet, backup_id): {version: StoredBackupVersion}}.

        `_version_projection` is a SEPARATE tracked epoch per (wallet,
        backup_id, version), decoupled from the canonical version row's own
        (frozen) `expires_at_epoch` field -- mirrors the real Cassandra
        table's ability to be re-keyed independent of the canonical row (see
        `resync_current_version_projection`). Without this separate map, the
        memory store could never reproduce finding #3 (2026-09-06): it would
        derive every day-bucket straight from the canonical, never-moving
        field, masking the exact reachability bug the real projection table
        has.
        """
        self._items: dict[str, dict[str, StoredBackup]] = {}
        self._versions: dict[tuple[str, str], dict[int, StoredBackupVersion]] = {}
        self._version_projection: dict[tuple[str, str, int], int] = {}

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

    def upsert_version(self, item: StoredBackupVersion) -> None:
        """Create or replace one version row, keeping its own tracked projection epoch in sync (set on active, cleared on a status flip to deleted)."""
        self._versions.setdefault((item.wallet, item.backup_id), {})[item.version] = item
        key = (item.wallet, item.backup_id, item.version)
        if item.status == STATUS_ACTIVE:
            self._version_projection[key] = item.expires_at_epoch
        else:
            self._version_projection.pop(key, None)

    def insert_new_version_if_absent(self, item: StoredBackupVersion) -> bool:
        """Claim (wallet, backup_id, item.version) only if no row already occupies it -- see BackupStore's own docstring for why this must never silently overwrite."""
        existing = self._versions.setdefault((item.wallet, item.backup_id), {})
        if item.version in existing:
            return False
        existing[item.version] = item
        if item.status == STATUS_ACTIVE:
            self._version_projection[(item.wallet, item.backup_id, item.version)] = (
                item.expires_at_epoch
            )
        return True

    def get_version(self, wallet: str, backup_id: str, version: int) -> StoredBackupVersion | None:
        """Return one version row by (wallet, backup_id, version), or None if it does not exist."""
        return self._versions.get((wallet, backup_id), {}).get(version)

    def list_versions(
        self, wallet: str, backup_id: str, *, limit: int
    ) -> list[StoredBackupVersion]:
        """Return up to `limit` of one backup's version rows, newest-version-first."""
        items = list(self._versions.get((wallet, backup_id), {}).values())
        ordered = sorted(items, key=lambda i: i.version, reverse=True)
        return ordered[: max(0, limit)]

    def list_versions_by_expiry_day(
        self, expiry_day: date, *, limit: int
    ) -> list[VersionExpiryIndexRow]:
        """Scan the tracked projection epochs (NOT the canonical rows' own frozen field -- see `_version_projection`'s own docstring) whose UTC date matches `expiry_day`.

        Cross-checks the canonical row is still active, same defensive
        double-check the real Cassandra projection gets for free (a deleted
        row's projection key is removed by the same `upsert_version` call
        that flips its status).
        """
        found: list[VersionExpiryIndexRow] = []
        for (wallet, backup_id, version), tracked_epoch in self._version_projection.items():
            if expiry_day_utc(tracked_epoch) != expiry_day:
                continue
            item = self._versions.get((wallet, backup_id), {}).get(version)
            if item is None or item.status != STATUS_ACTIVE:
                continue
            found.append(
                VersionExpiryIndexRow(
                    wallet=wallet,
                    backup_id=backup_id,
                    version=version,
                    expires_at_epoch=tracked_epoch,
                )
            )
        found.sort(key=lambda r: (r.expires_at_epoch, r.wallet, r.backup_id, r.version))
        return found[: max(0, limit)]

    def delete_version_expiry_index(self, row: VersionExpiryIndexRow) -> None:
        """Drop the tracked projection entry only if it still matches `row.expires_at_epoch` exactly -- mirrors Cassandra's DELETE, which is keyed on the full (expiry_day, expires_at, ...) primary key and is a no-op against a since-moved key."""
        key = (row.wallet, row.backup_id, row.version)
        if self._version_projection.get(key) == row.expires_at_epoch:
            del self._version_projection[key]

    def resync_current_version_projection(
        self, *, wallet: str, backup_id: str, version: int, previous_epoch: int, new_epoch: int
    ) -> None:
        """Re-key the tracked projection epoch for (wallet, backup_id, version), independent of the canonical row's own frozen `expires_at_epoch`. See BackupStore's own docstring for why/when this is called."""
        _ = previous_epoch  # only meaningful for Cassandra's explicit old-key DELETE; a dict assignment already replaces any prior value.
        if previous_epoch == new_epoch:
            return
        self._version_projection[(wallet, backup_id, version)] = new_epoch

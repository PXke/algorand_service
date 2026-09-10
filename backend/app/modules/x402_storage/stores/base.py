"""Storage interface for backup metadata rows."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from app.modules.x402_storage.models.domain import (
    ExpiryIndexRow,
    StoredBackup,
    StoredBackupVersion,
    VersionExpiryIndexRow,
)


class BackupStore(Protocol):
    """Storage interface for x402 agent backup metadata (x402_storage_backups + x402_storage_backup_versions)."""

    def upsert(self, item: StoredBackup) -> None:
        """Create or replace one backup row, full INSERT (never a partial UPDATE).

        Also keeps the expiry projection in sync for active rows (insert new
        key, delete the previous key when expires_at moved or the row is no
        longer active).
        """
        ...

    def get(self, wallet: str, backup_id: str) -> StoredBackup | None:
        """Return one backup row by (wallet, backup_id), whatever its status, or None if it does not exist."""
        ...

    def list_recent(self, wallet: str, *, limit: int) -> list[StoredBackup]:
        """Return up to `limit` of this wallet's backup rows, newest first, whatever their status.

        The caller filters to active/unexpired -- this is a raw, LIMITed read
        over the (wallet) partition's clustering order: a "filter after the
        LIMITed read" shape, so the read is always bounded even though the
        page can come back short.
        """
        ...

    def list_by_expiry_day(self, expiry_day: date, *, limit: int) -> list[ExpiryIndexRow]:
        """Up to `limit` projection rows whose expires_at falls on `expiry_day` (UTC)."""
        ...

    def delete_expiry_index(self, row: ExpiryIndexRow) -> None:
        """Remove one expiry-projection row. No-op if it is already gone."""
        ...

    def upsert_version(self, item: StoredBackupVersion) -> None:
        """Create one version row (full INSERT), or overwrite it with a status flip (delete time) -- never any other mutation.

        Also keeps the per-version expiry projection in sync for active
        rows, mirroring `upsert`'s own treatment of x402_storage_by_expiry.

        Only ever called for a row with no real collision risk: create()'s
        version 1 (a fresh random backup_id) or a status flip to deleted on
        an already-known row. `add_version()`'s own NEW version number is
        NOT collision-free (see `insert_new_version_if_absent` below) and
        must never go through this method.
        """
        ...

    def insert_new_version_if_absent(self, item: StoredBackupVersion) -> bool:
        """Claim a NEW version's (wallet, backup_id, version) slot only if it does not already exist. True if this call created it.

        A lightweight transaction, used ONLY by BackupService.add_version():
        the version number it computes (current_version + 1) can collide
        with a concurrent add_version reading the same stale current_version
        -- unlike upsert_version's other callers, which never contend for
        the same key. Exactly one caller's INSERT is applied; the other
        writes nothing (including no expiry projection) and must treat its
        already-uploaded bytes as needing cleanup (finding #5, 2026-09-06).
        """
        ...

    def get_version(self, wallet: str, backup_id: str, version: int) -> StoredBackupVersion | None:
        """Return one version row by (wallet, backup_id, version), whatever its status, or None if it does not exist."""
        ...

    def list_versions(
        self, wallet: str, backup_id: str, *, limit: int
    ) -> list[StoredBackupVersion]:
        """Return up to `limit` of one backup's version rows, newest-version-first, whatever their status."""
        ...

    def list_versions_by_expiry_day(
        self, expiry_day: date, *, limit: int
    ) -> list[VersionExpiryIndexRow]:
        """Up to `limit` version-expiry-projection rows whose expires_at falls on `expiry_day` (UTC)."""
        ...

    def delete_version_expiry_index(self, row: VersionExpiryIndexRow) -> None:
        """Remove one version-expiry-projection row. No-op if it is already gone."""
        ...

    def resync_current_version_projection(
        self, *, wallet: str, backup_id: str, version: int, previous_epoch: int, new_epoch: int
    ) -> None:
        """Re-key one version's x402_storage_version_by_expiry row from `previous_epoch` to `new_epoch`, WITHOUT touching the version's own canonical row.

        Used by BackupService.renew()/add_version() whenever the HEAD's
        expiry moves (or is first established) while `version` is the
        backup's CURRENT content (StoredBackup.current_version) -- so this
        version's reachability for the reaper's periodic day-partition scan
        tracks forward with the head instead of permanently falling outside
        x402_storage_reaper_lookback_days once the head has been renewed
        enough times while this version stayed current (finding #3,
        2026-09-06; see reaper.py's `_reap_one_version`). Deliberately never
        touches the version's own canonical `expires_at_epoch` -- that stays
        frozen at its own independent schedule for the day it stops being
        current (see StoredBackupVersion's own docstring). A no-op when
        `previous_epoch == new_epoch`.
        """
        ...

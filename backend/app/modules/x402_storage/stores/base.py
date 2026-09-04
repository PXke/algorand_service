"""Storage interface for backup metadata rows."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from app.modules.x402_storage.models.domain import ExpiryIndexRow, StoredBackup


class BackupStore(Protocol):
    """Storage interface for x402 agent backup metadata (x402_storage_backups)."""

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
        over the (wallet) partition's clustering order, same "filter after
        the LIMITed read" shape as x402_directory.search() and
        x402_board.list_active().
        """
        ...

    def list_by_expiry_day(self, expiry_day: date, *, limit: int) -> list[ExpiryIndexRow]:
        """Up to `limit` projection rows whose expires_at falls on `expiry_day` (UTC)."""
        ...

    def delete_expiry_index(self, row: ExpiryIndexRow) -> None:
        """Remove one expiry-projection row. No-op if it is already gone."""
        ...

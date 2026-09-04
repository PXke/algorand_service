"""Storage interface for backup metadata rows."""

from __future__ import annotations

from typing import Protocol

from app.modules.x402_storage.models.domain import StoredBackup


class BackupStore(Protocol):
    """Storage interface for x402 agent backup metadata (x402_storage_backups)."""

    def upsert(self, item: StoredBackup) -> None:
        """Create or replace one backup row, full INSERT (never a partial UPDATE)."""
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

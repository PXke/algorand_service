"""Backup-metadata-store singleton wiring, swappable for tests."""

from __future__ import annotations

from app.core.config import settings
from app.core.store_factory import StoreFactory
from app.modules.x402_storage.stores.base import BackupStore
from app.modules.x402_storage.stores.cassandra import CassandraBackupStore
from app.modules.x402_storage.stores.memory import InMemoryBackupStore

_factory: StoreFactory[BackupStore] = StoreFactory(
    backend_name=lambda: settings.x402_storage_meta_store,
    cassandra=CassandraBackupStore,
    memory=InMemoryBackupStore,
)


def get_backup_store() -> BackupStore:
    """Return the process-wide backup metadata store, built from settings on first use."""
    return _factory.get()


def set_backup_store(store: BackupStore | None) -> None:
    """Override the process-wide backup metadata store (test seam); None restores lazy build."""
    _factory.set(store)

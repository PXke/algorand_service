"""Connector-name -> StorageBackend resolution.

Every stored backup row carries the name of the connector that wrote it
(`connector` column), so a read/delete always resolves the SAME connector
implementation that wrote it, regardless of what settings.x402_storage_backend
currently says for NEW uploads. Only "local" resolves today -- "wasabi" is a
documented future addition (CLAUDE.md section 9 roadmap item 12), not built.
"""

from __future__ import annotations

from app.core.config import settings
from app.modules.x402_storage.backends.base import StorageBackend
from app.modules.x402_storage.backends.local import LocalDiskStorageBackend

_local_instance: LocalDiskStorageBackend | None = None
_local_instance_root: str | None = None


def _local_backend() -> LocalDiskStorageBackend | None:
    """Lazily build the process-wide local-disk backend, or None if no root is configured.

    Rebuilt whenever the configured root string changes (tests monkeypatch
    settings.x402_storage_local_root between cases and expect a fresh
    instance pointed at the new root, not a stale cached one).
    """
    global _local_instance, _local_instance_root
    root = settings.x402_storage_local_root.strip()
    if not root:
        return None
    if _local_instance is None or _local_instance_root != root:
        _local_instance = LocalDiskStorageBackend(root)
        _local_instance_root = root
    return _local_instance


def get_storage_backend(connector: str) -> StorageBackend | None:
    """Return the backend implementation for `connector`, or None if it does not resolve.

    None covers both an unknown/unimplemented connector name (e.g. "wasabi")
    and "local" with no root configured -- callers treat either the same way:
    the requested connector is currently unusable.
    """
    if connector == "local":
        return _local_backend()
    return None


def reset_local_backend_cache() -> None:
    """Test seam: drop the cached local backend instance so a test's own tmp_path root is honored."""
    global _local_instance, _local_instance_root
    _local_instance = None
    _local_instance_root = None

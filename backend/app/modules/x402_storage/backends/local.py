"""Local-disk storage connector: the only connector wired up today (see backends/factory.py)."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


class LocalDiskPathError(Exception):
    """A connector_params dict resolved outside the configured root -- never expected in practice (params are server-generated), belt-and-suspenders only."""


class LocalDiskStorageBackend:
    """Stores each backup as one file under a configured root, sharded by the first two hex characters of a fresh uuid4.

    `root` must already be a non-empty, existing-or-creatable directory --
    callers only construct this once settings.x402_storage_local_root is
    non-empty (see backends/factory.py's "empty = disabled" convention).
    """

    def __init__(self, root: str) -> None:
        """Take the configured root directory; created eagerly so a later put() never fails on a missing root."""
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, connector_params: dict[str, str]) -> Path:
        """Turn a stored `{"path": "<shard>/<uuid>.bin"}` back into a real filesystem path, refusing anything outside the root.

        The params dict is server-generated (see put()) and should never be
        able to name a path outside `root` -- but a filesystem path read
        back out of a dict is exactly the kind of thing that deserves a
        belt-and-suspenders check rather than trusting it blindly. Raises
        LocalDiskPathError if the resolved path is not inside the root.
        """
        raw = connector_params.get("path", "")
        if not raw:
            raise LocalDiskPathError("connector_params carries no path")
        candidate = (self._root / raw).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise LocalDiskPathError(
                f"resolved path {candidate} escapes storage root {self._root}"
            ) from exc
        return candidate

    def put(self, data: bytes) -> dict[str, str]:
        """Write `data` to a fresh, sharded path under the root and return the relative path to retrieve it by."""
        backup_uuid = uuid.uuid4().hex
        shard = backup_uuid[:2]
        rel_path = f"{shard}/{backup_uuid}.bin"
        full_path = self._root / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(data)
        return {"path": rel_path}

    def get(self, connector_params: dict[str, str]) -> bytes | None:
        """Return the stored bytes, or None if the path is missing/unreadable/escapes the root."""
        try:
            path = self._resolve(connector_params)
        except LocalDiskPathError:
            logger.error(
                "x402 storage: local backend refused a connector_params path escape on read",
                exc_info=True,
            )
            return None
        if not path.is_file():
            return None
        try:
            return path.read_bytes()
        except OSError:
            logger.warning("x402 storage: local backend failed to read %s", path, exc_info=True)
            return None

    def delete(self, connector_params: dict[str, str]) -> None:
        """Remove the stored file. Never raises if it is already gone or the path escapes the root (logged, not fatal)."""
        try:
            path = self._resolve(connector_params)
        except LocalDiskPathError:
            logger.error(
                "x402 storage: local backend refused a connector_params path escape on delete",
                exc_info=True,
            )
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("x402 storage: local backend failed to delete %s", path, exc_info=True)

    def usage_bytes(self) -> int:
        """Sum of file sizes under the root, walked fresh on every call.

        Fine for now given local-only storage and expected small scale (a
        prototype single-host connector, not a CDN-scale store). A real walk
        on every check-at-write-time call is a known future cost if this
        connector's row count grows large -- a cached/incremental counter
        would be the fix, not built here since that scale does not exist yet.
        """
        total = 0
        for dirpath, _dirnames, filenames in os.walk(self._root):
            for name in filenames:
                try:
                    total += (Path(dirpath) / name).stat().st_size
                except OSError:
                    continue
        return total

"""Storage connector abstraction: today only backends/local.py implements this."""

from __future__ import annotations

from typing import Protocol


class StorageBackend(Protocol):
    """One place bytes can physically live. A metadata row records which connector wrote it and that connector's own `connector_params`, so a future second connector (Wasabi) can coexist with rows already written by this one."""

    def put(self, data: bytes) -> dict[str, str]:
        """Durably store `data` and return the connector_params needed to retrieve it later."""
        ...

    def get(self, connector_params: dict[str, str]) -> bytes | None:
        """Return the stored bytes for `connector_params`, or None if they cannot be found."""
        ...

    def delete(self, connector_params: dict[str, str]) -> None:
        """Remove the stored bytes for `connector_params`. Never raises if they are already gone."""
        ...

    def usage_bytes(self) -> int:
        """Total bytes currently stored by this connector, for the disk-usage ceiling."""
        ...

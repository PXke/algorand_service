"""Storage interface for x402 uptime-check history rows."""

from __future__ import annotations

from typing import Protocol

from app.modules.x402_uptime.models.domain import StoredUptimeCheck


class UptimeHistoryStore(Protocol):
    """Storage interface for x402_uptime_checks_by_url (migration 117)."""

    def record(self, item: StoredUptimeCheck) -> None:
        """Append one real-check row. Write-only on the check route's hot path."""
        ...

    def list_since(self, url_hash: str, *, since_epoch: int, limit: int) -> list[StoredUptimeCheck]:
        """Up to `limit` rows for this url_hash with checked_at >= since_epoch, newest first."""
        ...

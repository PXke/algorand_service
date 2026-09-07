"""Uptime-check-history-store singleton wiring, swappable for tests."""

from __future__ import annotations

from app.core.config import settings
from app.core.store_factory import StoreFactory
from app.modules.x402_uptime.stores.base import UptimeHistoryStore
from app.modules.x402_uptime.stores.cassandra import CassandraUptimeHistoryStore
from app.modules.x402_uptime.stores.memory import InMemoryUptimeHistoryStore

_factory: StoreFactory[UptimeHistoryStore] = StoreFactory(
    backend_name=lambda: settings.x402_uptime_history_store,
    cassandra=CassandraUptimeHistoryStore,
    memory=InMemoryUptimeHistoryStore,
)


def get_uptime_history_store() -> UptimeHistoryStore:
    """Return the process-wide uptime-check history store, built from settings on first use."""
    return _factory.get()


def set_uptime_history_store(store: UptimeHistoryStore | None) -> None:
    """Override the process-wide uptime-check history store (test seam); None restores lazy build."""
    _factory.set(store)

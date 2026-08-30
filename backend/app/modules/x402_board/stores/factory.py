"""Placement-store singleton wiring, swappable for tests."""

from __future__ import annotations

from app.core.config import settings
from app.core.store_factory import StoreFactory
from app.modules.x402_board.stores.base import PlacementStore
from app.modules.x402_board.stores.cassandra import CassandraPlacementStore
from app.modules.x402_board.stores.memory import InMemoryPlacementStore

_factory: StoreFactory[PlacementStore] = StoreFactory(
    backend_name=lambda: settings.x402_board_store,
    cassandra=CassandraPlacementStore,
    memory=InMemoryPlacementStore,
)


def get_placement_store() -> PlacementStore:
    """Return the process-wide placement store, built from settings on first use."""
    return _factory.get()


def set_placement_store(store: PlacementStore | None) -> None:
    """Override the process-wide placement store (test seam); None restores lazy build."""
    _factory.set(store)

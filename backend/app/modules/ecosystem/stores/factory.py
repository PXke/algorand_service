"""Registry-store singleton wiring, swappable for tests."""

from __future__ import annotations

from app.core.config import settings
from app.core.store_factory import StoreFactory
from app.modules.ecosystem.stores.base import ProjectStore, RequestStore
from app.modules.ecosystem.stores.cassandra import CassandraProjectStore, CassandraRequestStore
from app.modules.ecosystem.stores.memory import InMemoryProjectStore, InMemoryRequestStore

_factory: StoreFactory[ProjectStore] = StoreFactory(
    backend_name=lambda: settings.ecosystem_store,
    cassandra=CassandraProjectStore,
    memory=InMemoryProjectStore,
)

# Same backend switch as the project store (design doc's ecosystem_store
# setting governs the whole module, not one table at a time) -- a separate
# StoreFactory instance because it's a distinct Protocol/table pair, not a
# distinct config knob.
_request_factory: StoreFactory[RequestStore] = StoreFactory(
    backend_name=lambda: settings.ecosystem_store,
    cassandra=CassandraRequestStore,
    memory=InMemoryRequestStore,
)


def get_project_store() -> ProjectStore:
    """Return the process-wide registry store, built from settings on first use."""
    return _factory.get()


def set_project_store(store: ProjectStore | None) -> None:
    """Override the process-wide registry store (test seam); None restores lazy build."""
    _factory.set(store)


def get_request_store() -> RequestStore:
    """Return the process-wide "suggest a change" request store, built from settings on first use."""
    return _request_factory.get()


def set_request_store(store: RequestStore | None) -> None:
    """Override the process-wide request store (test seam); None restores lazy build."""
    _request_factory.set(store)

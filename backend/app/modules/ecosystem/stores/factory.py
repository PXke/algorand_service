"""Registry-store singleton wiring, swappable for tests."""

from __future__ import annotations

from app.core.config import settings
from app.core.store_factory import StoreFactory
from app.modules.ecosystem.stores.base import ProjectStore
from app.modules.ecosystem.stores.cassandra import CassandraProjectStore
from app.modules.ecosystem.stores.memory import InMemoryProjectStore

_factory: StoreFactory[ProjectStore] = StoreFactory(
    backend_name=lambda: settings.ecosystem_store,
    cassandra=CassandraProjectStore,
    memory=InMemoryProjectStore,
)


def get_project_store() -> ProjectStore:
    """Return the process-wide registry store, built from settings on first use."""
    return _factory.get()


def set_project_store(store: ProjectStore | None) -> None:
    """Override the process-wide registry store (test seam); None restores lazy build."""
    _factory.set(store)

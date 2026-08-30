"""Grade-store singleton wiring, swappable for tests."""

from __future__ import annotations

from app.core.config import settings
from app.core.store_factory import StoreFactory
from app.modules.x402_grading.stores.base import GradeStore
from app.modules.x402_grading.stores.cassandra import CassandraGradeStore
from app.modules.x402_grading.stores.memory import InMemoryGradeStore

_factory: StoreFactory[GradeStore] = StoreFactory(
    backend_name=lambda: settings.x402_grading_store,
    cassandra=CassandraGradeStore,
    memory=InMemoryGradeStore,
)


def get_grade_store() -> GradeStore:
    """Return the process-wide grade store, built from settings on first use."""
    return _factory.get()


def set_grade_store(store: GradeStore | None) -> None:
    """Override the process-wide grade store (test seam); None restores lazy build."""
    _factory.set(store)

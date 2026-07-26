"""Enrollment-store singleton wiring, swappable for tests."""

from __future__ import annotations

from app.core.config import settings
from app.core.store_factory import StoreFactory
from app.modules.kyc.stores.base import EnrollmentStore
from app.modules.kyc.stores.cassandra import CassandraEnrollmentStore
from app.modules.kyc.stores.memory import InMemoryEnrollmentStore

_factory: StoreFactory[EnrollmentStore] = StoreFactory(
    backend_name=lambda: settings.kyc_store,
    cassandra=CassandraEnrollmentStore,
    memory=InMemoryEnrollmentStore,
)


def get_enrollment_store() -> EnrollmentStore:
    """Return the process-wide enrollment store, built from settings on first use."""
    return _factory.get()

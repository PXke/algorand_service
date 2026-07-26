"""Enrollment-store singleton wiring, swappable for tests."""

from __future__ import annotations

from app.core.config import settings
from app.modules.kyc.stores.base import EnrollmentStore
from app.modules.kyc.stores.cassandra import CassandraEnrollmentStore
from app.modules.kyc.stores.memory import InMemoryEnrollmentStore

_enrollment_store: EnrollmentStore | None = None


def get_enrollment_store() -> EnrollmentStore:
    """Return the process-wide enrollment store, lazily built from settings."""
    global _enrollment_store
    if _enrollment_store is None:
        backend = settings.kyc_store.strip().lower()
        if backend == "cassandra":
            _enrollment_store = CassandraEnrollmentStore()
        else:
            _enrollment_store = InMemoryEnrollmentStore()
    return _enrollment_store



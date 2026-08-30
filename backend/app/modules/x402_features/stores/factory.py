"""Feature-store singleton wiring, swappable for tests."""

from __future__ import annotations

from app.core.config import settings
from app.core.store_factory import StoreFactory
from app.modules.x402_features.stores.base import FeatureStore
from app.modules.x402_features.stores.cassandra import CassandraFeatureStore
from app.modules.x402_features.stores.memory import InMemoryFeatureStore

_factory: StoreFactory[FeatureStore] = StoreFactory(
    backend_name=lambda: settings.x402_features_store,
    cassandra=CassandraFeatureStore,
    memory=InMemoryFeatureStore,
)


def get_feature_store() -> FeatureStore:
    """Return the process-wide feature store, built from settings on first use."""
    return _factory.get()


def set_feature_store(store: FeatureStore | None) -> None:
    """Override the process-wide feature store (test seam); None restores lazy build."""
    _factory.set(store)

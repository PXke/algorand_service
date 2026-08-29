"""Listing-store singleton wiring, swappable for tests."""

from __future__ import annotations

from app.core.config import settings
from app.core.store_factory import StoreFactory
from app.modules.x402_directory.stores.base import ListingStore
from app.modules.x402_directory.stores.cassandra import CassandraListingStore
from app.modules.x402_directory.stores.memory import InMemoryListingStore

_factory: StoreFactory[ListingStore] = StoreFactory(
    backend_name=lambda: settings.x402_directory_store,
    cassandra=CassandraListingStore,
    memory=InMemoryListingStore,
)


def get_listing_store() -> ListingStore:
    """Return the process-wide listing store, built from settings on first use."""
    return _factory.get()


def set_listing_store(store: ListingStore | None) -> None:
    """Override the process-wide listing store (test seam); None restores lazy build."""
    _factory.set(store)

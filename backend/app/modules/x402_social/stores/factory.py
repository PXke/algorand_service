"""Social-store singleton wiring, swappable for tests."""

from __future__ import annotations

from app.core.config import settings
from app.core.store_factory import StoreFactory
from app.modules.x402_social.stores.base import SocialStore
from app.modules.x402_social.stores.cassandra import CassandraSocialStore
from app.modules.x402_social.stores.memory import InMemorySocialStore

_factory: StoreFactory[SocialStore] = StoreFactory(
    backend_name=lambda: settings.x402_social_store,
    cassandra=CassandraSocialStore,
    memory=InMemorySocialStore,
)


def get_social_store() -> SocialStore:
    """Return the process-wide social store, built from settings on first use."""
    return _factory.get()


def set_social_store(store: SocialStore | None) -> None:
    """Override the process-wide social store (test seam); None restores lazy build."""
    _factory.set(store)

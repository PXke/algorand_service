"""Lazy per-process store singletons chosen by a settings value.

Every module store follows the same shape: build the Cassandra implementation
when its `*_store` setting says "cassandra", the in-memory one otherwise, keep
it for the life of the process, and let tests swap it. That was copy-pasted
four times, so a changed default or a new backend name had to be edited in
four places to stay consistent.

Construction stays lazy: importing a factory must not open a Cassandra
connection, or the API could not boot (or run its tests) without one.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class StoreFactory(Generic[T]):
    """Holds one lazily-built store instance and the rule for choosing it."""

    def __init__(
        self,
        *,
        backend_name: Callable[[], str],
        cassandra: Callable[[], T],
        memory: Callable[[], T],
    ) -> None:
        """Take callables, not instances, so nothing is constructed at import time.

        `backend_name` is read at first use rather than captured, so a test that
        overrides the setting still gets the backend it asked for.
        """
        self._backend_name = backend_name
        self._cassandra = cassandra
        self._memory = memory
        self._instance: T | None = None

    def get(self) -> T:
        """Return the process-wide store, building it from settings on first use."""
        if self._instance is None:
            name = (self._backend_name() or "").strip().lower()
            self._instance = self._cassandra() if name == "cassandra" else self._memory()
        return self._instance

    def set(self, store: T | None) -> None:
        """Override the process-wide store (test seam); None restores lazy build."""
        self._instance = store

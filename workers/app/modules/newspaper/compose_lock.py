"""Global mutex for the expensive Mistral writer loop (research + compose).

Only one agentic article composition may run at a time across all workers.
Queue drains, admin recompose, and in-place edits all funnel through the
writer entry points guarded by ``compose_lock()``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from app.core.redis_lock import acquire, release

COMPOSE_LOCK_KEY = "compose:article"
COMPOSE_LOCK_TTL = 1860


class ComposeBusyError(Exception):
    """Raised when another compose already holds the global writer lock."""

    def __init__(self, key: str = COMPOSE_LOCK_KEY) -> None:
        self.key = key
        super().__init__(key)


@contextlib.contextmanager
def compose_lock() -> Iterator[None]:
    """Hold the global compose lock for the duration of the block."""
    token = acquire(COMPOSE_LOCK_KEY, COMPOSE_LOCK_TTL)
    if token is None:
        raise ComposeBusyError()
    try:
        yield
    finally:
        release(COMPOSE_LOCK_KEY, token)

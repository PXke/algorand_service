"""Global mutex for local (on-box) translation inference.

Only one local-model translation may run at a time, across all workers.
Unlike ``compose_lock`` (the Mistral writer's equivalent), this guards CPU and
RAM, not an external API: SeamlessM4T and MiLMMT are loaded once per process
and run on shared CPU cores with no GPU, so two concurrent inferences would
both slow down, fight for the same threads, and roughly double peak memory
for no throughput gain.

Deliberately simpler than ``compose_lock`` for this first pass (2026-07-29,
dev-only proof of concept): no dead-holder reclaim via Celery inspect() yet.
Add that the same way compose_lock does it once this is actually wired into
Celery/prod — a worker killed mid-inference would otherwise leave the lock
held until TTL expiry, same failure mode compose_lock was built to survive.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from app.core.redis_lock import acquire, release

LOCAL_TRANSLATE_LOCK_KEY = "translate:local"
# Sized for a whole multi-language BATCH (translate_article_batch), not a
# single language -- this used to be 900s on the assumption a single article
# "should not approach" that. Measured 2026-07-30: one language, one
# longer-than-median article (41 of ~42 max blocks in the corpus), took 51
# minutes via MiLMMT on 8 CPU threads. A batch of up to 7 MiLMMT languages
# for a long article can run for hours. Matches the translate_article_batch
# Celery task's own time_limit (see publish_tasks.py) -- getting this wrong
# doesn't corrupt anything (best-effort/fail-open, see acquire() below), but
# too short a TTL lets a SECOND batch acquire the lock mid-first-batch and
# run concurrently, which is exactly the both-models-loaded scenario this
# lock exists to prevent.
LOCAL_TRANSLATE_LOCK_TTL = 21600


class LocalTranslateBusyError(Exception):
    """Raised when another local translation already holds the mutex."""


@contextlib.contextmanager
def local_translate_lock() -> Iterator[None]:
    """Hold the global local-inference mutex for the duration of the block. Raises LocalTranslateBusyError if already held — callers should let the task retry rather than block, so a queue of pending translations doesn't tie up Celery worker slots waiting."""
    token = acquire(LOCAL_TRANSLATE_LOCK_KEY, LOCAL_TRANSLATE_LOCK_TTL)
    if token is None:
        raise LocalTranslateBusyError
    try:
        yield
    finally:
        release(LOCAL_TRANSLATE_LOCK_KEY, token)


def get_local_translate_lock_status() -> int | None:
    """Remaining TTL (seconds) if a batch currently holds the lock, else None (not held, or Redis unreachable). Deliberately minimal — no held-by/started-at metadata like compose_lock's version, matching this module's "simpler for now" scope. Exists for deploy.sh's pre-restart warning (SIGQUIT on the translate worker silently kills an in-flight batch, same risk compose_lock's check surfaces for compose — see deploy.sh)."""
    try:
        from app.core.redis_lock import _client

        ttl = _client().ttl(f"lock:{LOCAL_TRANSLATE_LOCK_KEY}")
        return ttl if ttl is not None and ttl >= 0 else None
    except Exception:
        return None

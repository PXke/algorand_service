"""Global mutex for local (on-box) translation inference.

Only one local-model translation may run at a time, across all workers.
Unlike ``compose_lock`` (the Mistral writer's equivalent), this guards CPU and
RAM, not an external API: SeamlessM4T and MiLMMT are loaded once per process
and run on shared CPU cores with no GPU, so two concurrent inferences would
both slow down, fight for the same threads, and roughly double peak memory
for no throughput gain.

Dead-holder reclaim mirrors ``compose_lock``'s (ported 2026-08-08): a worker
killed mid-batch -- e.g. a deploy's `systemctl restart`, whose 30s
TimeoutStopSec can't wait out an hours-long translation batch (see the
algorand-platform-celery-translate systemd unit's own comment on this) --
used to leave the lock held for its full TTL, blocking every other queued
translation for up to that long. Hit live in prod 2026-08-08: a restart to
deploy an unrelated fix stranded the lock for ~5h37m of remaining TTL.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.redis_lock import acquire, release

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)

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
# lock exists to prevent. Now a backstop only, not the primary safety net --
# see the dead-holder reclaim below, same division of labor as compose_lock.
LOCAL_TRANSLATE_LOCK_TTL = 57600
_RAW_LOCK_KEY = f"lock:{LOCAL_TRANSLATE_LOCK_KEY}"
_META_KEY = f"lock:{LOCAL_TRANSLATE_LOCK_KEY}:meta"
# Never attempt to reclaim a lock younger than this -- only rules out
# reclaiming something acquired moments ago by a task that hasn't had a
# chance to show up in Celery's active() list yet.
_MIN_RECLAIM_AGE_SECONDS = 120


class LocalTranslateBusyError(Exception):
    """Raised when another local translation already holds the mutex."""


def _redis_client() -> redis.Redis:
    import redis

    from app.core.config import REDIS_URL

    return redis.from_url(REDIS_URL, decode_responses=True)


def _current_task_id() -> str:
    """Celery task id of the caller, if local_translate_lock() is being used from inside an actual task (not a one-off manual/SSH script invocation — those have no task context, so this returns "" and such runs simply can't be auto-reclaimed by a later caller if they stall)."""
    try:
        from celery import current_task

        if current_task and current_task.request and current_task.request.id:
            return str(current_task.request.id)
    except Exception:
        pass
    return ""


def _write_meta(task_id: str) -> None:
    meta = {"task_id": task_id, "started_at": datetime.now(tz=UTC).isoformat()}
    with contextlib.suppress(Exception):
        _redis_client().set(_META_KEY, json.dumps(meta), ex=LOCAL_TRANSLATE_LOCK_TTL)


def _holder_is_dead(meta: dict) -> bool:
    """True ONLY when we can positively confirm the recorded holder task is no longer running. Any uncertainty at all (no task_id recorded, still within the minimum age floor, Celery inspect() failing or timing out) returns False — this must never reclaim on a hunch, since two translation batches running concurrently is exactly what this lock exists to prevent."""
    task_id = meta.get("task_id")
    started_at = meta.get("started_at")
    if not task_id or not started_at:
        return False
    try:
        age = (datetime.now(tz=UTC) - datetime.fromisoformat(started_at)).total_seconds()
    except (ValueError, TypeError):
        return False
    if age < _MIN_RECLAIM_AGE_SECONDS:
        return False
    try:
        from app.celery_app import celery_app

        active = celery_app.control.inspect(timeout=5).active() or {}
    except Exception:
        return False  # inspect itself failed — do not guess, leave it alone
    for tasks in active.values():
        for t in tasks:
            if t.get("id") == task_id:
                return False  # confirmed still alive
    return True  # inspect succeeded and the task id is nowhere — genuinely dead


def _try_reclaim() -> bool:
    """Force-clear the lock iff its recorded holder is positively dead. Returns True if it reclaimed (caller should retry acquire())."""
    status = get_local_translate_lock_status(_include_meta=True)
    if not status or not _holder_is_dead(status):
        return False
    logger.warning(
        "local translate lock held by dead task %s (age~%ss) — reclaiming; "
        "likely a worker restart killed it mid-batch without releasing the "
        "lock (hit in prod 2026-08-08)",
        status.get("task_id"),
        status.get("ttl_seconds"),
    )
    with contextlib.suppress(Exception):
        client = _redis_client()
        client.delete(_RAW_LOCK_KEY)
        client.delete(_META_KEY)
    return True


@contextlib.contextmanager
def local_translate_lock() -> Iterator[None]:
    """Hold the global local-inference mutex for the duration of the block. Raises LocalTranslateBusyError if already held (and not reclaimable) — callers should let the task retry rather than block, so a queue of pending translations doesn't tie up Celery worker slots waiting."""
    token = acquire(LOCAL_TRANSLATE_LOCK_KEY, LOCAL_TRANSLATE_LOCK_TTL)
    if token is None and _try_reclaim():
        token = acquire(LOCAL_TRANSLATE_LOCK_KEY, LOCAL_TRANSLATE_LOCK_TTL)
    if token is None:
        raise LocalTranslateBusyError
    _write_meta(_current_task_id())
    try:
        yield
    finally:
        release(LOCAL_TRANSLATE_LOCK_KEY, token)
        with contextlib.suppress(Exception):
            _redis_client().delete(_META_KEY)


def get_local_translate_lock_status(*, _include_meta: bool = False) -> dict | int | None:
    """Remaining TTL (seconds) if a batch currently holds the lock, else None (not held, or Redis unreachable). Exists for deploy.sh's pre-restart warning (SIGQUIT on the translate worker silently kills an in-flight batch, same risk compose_lock's check surfaces for compose — see deploy.sh). Pass _include_meta=True (internal, for _try_reclaim) to get the full holder metadata dict instead of just the bare TTL int."""
    try:
        client = _redis_client()
        ttl = client.ttl(_RAW_LOCK_KEY)
        if ttl is None or ttl < 0:
            return None
        if not _include_meta:
            return ttl
        raw = client.get(_META_KEY)
    except Exception:
        return None
    meta: dict = {}
    if raw:
        with contextlib.suppress(ValueError, TypeError):
            meta = json.loads(raw)
    meta["ttl_seconds"] = ttl
    return meta

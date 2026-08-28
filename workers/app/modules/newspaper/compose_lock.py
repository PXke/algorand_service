"""Global mutex for the expensive Mistral writer loop (research + compose).

Only one agentic article composition may run at a time across all workers.
Queue drains, admin recompose, and in-place edits all funnel through the
writer entry points guarded by ``compose_lock()``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.redis_lock import acquire, release

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)

COMPOSE_LOCK_KEY = "compose:article"
# Backstop only, not the primary safety mechanism — a genuinely dead holder
# (crashed worker, killed task) is caught promptly by _try_reclaim() below,
# which checks Celery's live task list directly and doesn't depend on this
# TTL at all. Set generously above any real compose duration instead: the
# old 1860s (31min) value was already shorter than observed real runs
# (root-caused 2026-08-06 live — an ordinary, non-special-edition recompose
# was still actively calling the LLM past the 31-minute mark, meaning the
# Redis key had already silently expired while the compose was genuinely
# still in progress, which lets a brand-new, unrelated compose acquire the
# "held" lock immediately — no reclaim check ever runs in that path, since
# reclaim only triggers when acquire() finds the key still present. This is
# very likely the real explanation for the original, never-fully-explained
# "composition happened during the freeze window" mystery from earlier
# work on this platform).
COMPOSE_LOCK_TTL = 10800
_RAW_LOCK_KEY = f"lock:{COMPOSE_LOCK_KEY}"
_META_KEY = f"lock:{COMPOSE_LOCK_KEY}:meta"
# Never attempt to reclaim a lock younger than this. Real composes routinely
# run for many minutes; this floor only rules out reclaiming something that
# was acquired moments ago by a task that hasn't had a chance to run yet.
_MIN_RECLAIM_AGE_SECONDS = 120


class ComposeBusyError(Exception):
    """Raised when another compose already holds the global writer lock."""

    def __init__(self, key: str = COMPOSE_LOCK_KEY, status: dict | None = None) -> None:
        """Carry the lock key and, if known, the current holder's metadata."""
        self.key = key
        self.status = status  # holder metadata (label/started_at/task_id), if known
        super().__init__(key)


def _redis_client() -> redis.Redis:
    import redis

    from app.core.config import REDIS_URL

    return redis.from_url(REDIS_URL, decode_responses=True)


def _current_task_id() -> str:
    """Celery task id of the caller, if compose_lock() is being used from inside an actual task (not a one-off manual/SSH script invocation — those have no task context, so this returns "" and such runs simply can't be auto-reclaimed by a later caller if they stall)."""
    try:
        from celery import current_task

        if current_task and current_task.request and current_task.request.id:
            return str(current_task.request.id)
    except Exception:
        pass
    return ""


def _proc_start_ticks(pid: int) -> int | None:
    """This process's start time in clock ticks since boot, from /proc/<pid>/stat field 22. Returns None ONLY when the process is confirmed gone (a bare `FileNotFoundError` -- no such pid at all). Any other unreadable case (permission denied, a hardened /proc mount) RAISES instead of returning None: "gone" and "can't tell" mean very different things to a caller deciding whether it's safe to reclaim a lock, and collapsing them together silently is exactly how an invisible-but-alive process would get treated as dead. No psutil dependency, same convention as browser_reaper.py's ps-based process inspection -- Linux-only, which every other part of this codebase already assumes (ps-based reaper, journalctl, etc.)."""
    from pathlib import Path

    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return None
    try:
        # comm (field 2) is parenthesized and can itself contain spaces or
        # closing parens, so split on the LAST ")" to safely find the
        # fixed-width fields that follow it -- field 22 (starttime) is
        # then index 19 in that remainder (fields 3..22 zero-indexed).
        _, _, rest = raw.rpartition(")")
        return int(rest.split()[19])
    except (ValueError, IndexError):
        return None


def _pid_is_dead(pid: int, recorded_start_ticks: int | None) -> bool:
    """True only when positively confirmed: either no process is running at `pid` any more, or one is, but its start time doesn't match what was recorded when the lock was written -- meaning `pid` has since been reused by an unrelated process. Any uncertainty at all returns False, matching `_holder_is_dead`'s own conservative contract: can't read /proc for this pid (permission denied); no start time was ever recorded to compare against; or -- checked first, below -- can't even trust /proc to show us OTHER processes at all.

    The pid-1 tripwire: pid 1 always exists and is always readable on a
    normal /proc mount. If THIS process can't read pid 1's own stat file,
    the whole /proc view can't be trusted for anyone else's pid either --
    a hardened mount (`hidepid=2`, systemd's `ProtectProc=invisible`)
    reports a hidden-but-alive process as a bare ENOENT, byte-for-byte
    identical to "gone", so splitting exceptions in `_proc_start_ticks`
    alone cannot catch this case. Checked lazily on every call (not once at
    import time), so this stays correct if the mount options ever change
    without a restart.
    """
    try:
        if _proc_start_ticks(1) is None:
            return False
    except OSError:
        return False
    try:
        current = _proc_start_ticks(pid)
    except OSError:
        return False
    if current is None:
        return True
    if recorded_start_ticks is None:
        return False
    return current != recorded_start_ticks


def _write_meta(label: str, task_id: str) -> None:
    pid = os.getpid()
    meta = {
        "label": label,
        "task_id": task_id,
        "started_at": datetime.now(tz=UTC).isoformat(),
        # Recorded unconditionally (not just for bare invocations) so
        # _holder_is_dead has a same-host fallback ready the moment task_id
        # is empty -- a real Celery task also has a pid, this costs nothing
        # extra to capture for it. hostname matters because a pid is only
        # ever meaningful on the host that minted it -- without it, a
        # future multi-host worker fleet would evaluate a foreign pid
        # against ITS OWN /proc and almost always see "no such pid",
        # falsely concluding the holder is dead.
        "pid": pid,
        "pid_start_ticks": _proc_start_ticks(pid),
        "hostname": socket.gethostname(),
    }
    with contextlib.suppress(Exception):
        _redis_client().set(_META_KEY, json.dumps(meta), ex=COMPOSE_LOCK_TTL)


def get_compose_lock_status() -> dict | None:
    """Best-effort snapshot of who holds the compose lock and since when — for admin visibility, so "why isn't anything happening" is a one-line check instead of cross-referencing compose_sessions by hand (as this was diagnosed manually in prod on 2026-07-13). None if the lock isn't currently held or Redis is unreachable."""
    try:
        client = _redis_client()
        ttl = client.ttl(_RAW_LOCK_KEY)
        if ttl is None or ttl < 0:
            return None
        raw = client.get(_META_KEY)
    except Exception:
        return None
    meta: dict = {}
    if raw:
        with contextlib.suppress(ValueError, TypeError):
            meta = json.loads(raw)
    meta["ttl_seconds"] = ttl
    return meta


def _celery_task_is_dead(task_id: str) -> bool:
    """True only when Celery's control plane positively confirms `task_id` is not active anywhere. Any failure to ask (broker unreachable, timeout) returns False -- do not guess."""
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


def _bare_holder_is_dead(meta: dict) -> bool:
    """True only when a same-host PID-liveness check (see _pid_is_dead) positively confirms the recorded holder is gone. False whenever there's no pid to check, or it was recorded on a different host (a pid is only ever meaningful on the host that minted it)."""
    pid = meta.get("pid")
    if not isinstance(pid, int):
        return False  # no task_id AND no pid recorded — nothing to check
    if meta.get("hostname") != socket.gethostname():
        return False  # a different host's pid — can't evaluate locally, never reclaim
    return _pid_is_dead(pid, meta.get("pid_start_ticks"))


def _holder_is_dead(meta: dict) -> bool:
    """True ONLY when we can positively confirm the recorded holder is no longer running. Any uncertainty at all (still within the minimum age floor, Celery inspect() failing or timing out, /proc unreadable) returns False — this must never reclaim on a hunch, since two writer loops running concurrently is exactly what this lock exists to prevent.

    Two independent ways to confirm death, depending on how the lock was
    acquired:

      - Held by a real Celery task (``task_id`` set): ask Celery's control
        plane directly whether that task id is still active anywhere.

      - Held by a one-off manual/SSH script (``task_id`` empty -- see
        ``_current_task_id``'s own docstring for why that happens): Celery's
        control plane has nothing to check, but the holder ran on THIS SAME
        host, so a same-host PID-liveness check (with a start-time
        comparison to rule out PID reuse, see ``_pid_is_dead``) is a valid
        substitute under the identical conservative contract. Root-caused
        2026-08-27/28: a bare-invoked recompose hung for hours (a Chromium
        process reaped out from under it -- see browser_reaper.py -- turned
        a mid-call Playwright wait into a permanent block) and its
        empty-task_id lock could never be fast-reclaimed, only ever expiring
        on the full 3-hour TTL. The practical fix is "never bare-script a
        compose" (always ``send_task``), but this closes the gap for
        whenever that rule gets broken again by someone else, or by mistake.
    """
    started_at = meta.get("started_at")
    if not started_at:
        return False
    try:
        age = (datetime.now(tz=UTC) - datetime.fromisoformat(started_at)).total_seconds()
    except (ValueError, TypeError):
        return False
    if age < _MIN_RECLAIM_AGE_SECONDS:
        return False

    task_id = meta.get("task_id")
    if task_id:
        return _celery_task_is_dead(task_id)
    return _bare_holder_is_dead(meta)


def _try_reclaim() -> bool:
    """Force-clear the lock iff its recorded holder is positively dead.

    Returns True if it reclaimed (caller should retry acquire()).
    """
    status = get_compose_lock_status()
    if not status or not _holder_is_dead(status):
        return False
    logger.warning(
        "compose lock held by dead task %s (label=%r, age~%ss) — reclaiming; "
        "likely a worker restart (e.g. a deploy) killed it mid-compose without "
        "releasing the lock (hit in prod 2026-07-13)",
        status.get("task_id"),
        status.get("label"),
        status.get("ttl_seconds"),
    )
    with contextlib.suppress(Exception):
        client = _redis_client()
        client.delete(_RAW_LOCK_KEY)
        client.delete(_META_KEY)
    return True


@contextlib.contextmanager
def compose_lock(label: str = "") -> Iterator[None]:
    """Hold the global compose lock for the duration of the block.

    ``label`` (e.g. the source_url being composed) is stored as visibility
    metadata alongside the lock — see get_compose_lock_status(). If the lock
    is already held, makes ONE attempt to reclaim it, but only when the
    recorded holder is positively confirmed dead (see _holder_is_dead) —
    otherwise raises ComposeBusyError exactly as before, carrying the
    holder's status for the caller to log/report.
    """
    token = acquire(COMPOSE_LOCK_KEY, COMPOSE_LOCK_TTL)
    if token is None and _try_reclaim():
        token = acquire(COMPOSE_LOCK_KEY, COMPOSE_LOCK_TTL)
    if token is None:
        raise ComposeBusyError(status=get_compose_lock_status())
    _write_meta(label, _current_task_id())
    try:
        yield
    finally:
        release(COMPOSE_LOCK_KEY, token)
        with contextlib.suppress(Exception):
            _redis_client().delete(_META_KEY)

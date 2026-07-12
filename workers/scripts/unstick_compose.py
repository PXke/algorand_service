"""Safely clear a stuck article compose.

A compose holds a GLOBAL Redis lock (lock:compose:article) for its entire
multi-minute run, so only one runs at a time. If a worker is hard-killed
mid-compose (OOM / hard time limit), the lock lingers until its ~31-min TTL and
blocks ALL new composes, while the Sessions row stays frozen at
'researching'/'writing'.

Modes (read-only by default — only --clear/--force delete anything):
  (no flags)   diagnose: print lock state + newest session, recommend an action.
  --clear      delete the compose locks IF the newest compose looks dead (its
               session is non-terminal and older than --max-age, or already
               terminal while a lock is somehow still held).
  --force      clear regardless of the staleness check (use only once you've
               confirmed no worker is genuinely composing right now).
  --drain      after clearing, enqueue a standard drain so the pending row
               retries immediately instead of waiting for the safety-net beat.
  --max-age N  staleness threshold in seconds (default 1200 — comfortably past a
               normal compose but under the lock's 1860s self-expiry).

Run on a host with the workers env (same REDIS_URL the locks live in):
    cd workers && python -m scripts.unstick_compose [--clear] [--drain]
"""

from __future__ import annotations

import argparse
import contextlib
from datetime import UTC, datetime

_GLOBAL_LOCK = "lock:compose:article"
_LOCK_PATTERN = "lock:compose:*"
# Statuses that mean the compose is DONE (researching/writing are in-progress).
_TERMINAL = {"ok", "error", "fallback", "credit_insufficient"}


def _redis():
    # Reuse the lock module's client so we hit exactly the Redis/DB the locks use.
    from app.core.redis_lock import _client

    return _client()


def _latest_session():
    """(status, created_at, age_seconds) of the newest compose row, or None."""
    from app.core.cassandra import get_cassandra_session

    row = (
        get_cassandra_session()
        .execute(
            "SELECT status, created_at FROM compose_sessions WHERE bucket=%s LIMIT 1",
            ("all",),
        )
        .one()
    )
    if row is None:
        return None
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    age = (datetime.now(tz=UTC) - created).total_seconds()
    return str(row.status or ""), created, age


def _decode(key) -> str:
    return key.decode() if isinstance(key, (bytes, bytearray)) else str(key)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--clear", action="store_true", help="delete compose locks if stale")
    ap.add_argument("--force", action="store_true", help="clear regardless of staleness check")
    ap.add_argument("--drain", action="store_true", help="enqueue a standard drain after clearing")
    ap.add_argument(
        "--max-age",
        type=int,
        default=1200,
        help="staleness threshold seconds (default 1200)",
    )
    args = ap.parse_args()

    r = _redis()

    # --- lock state ---
    held = r.get(_GLOBAL_LOCK) is not None
    ttl = r.ttl(_GLOBAL_LOCK)
    lock_state = f"HELD (ttl {ttl}s)" if held else "free"
    print(f"global lock {_GLOBAL_LOCK}: {lock_state}")
    locks = list(r.scan_iter(match=_LOCK_PATTERN))
    for k in locks:
        print(f"  lock present: {_decode(k)}  ttl={r.ttl(k)}s")

    # --- newest session ---
    stale = False
    try:
        latest = _latest_session()
    except Exception as exc:
        latest = None
        print(f"(could not read compose_sessions: {exc})")
    if latest:
        status, _created, age = latest
        terminal = status in _TERMINAL
        print(
            f"newest session: status={status!r} age={int(age)}s "
            f"({'terminal' if terminal else 'IN-PROGRESS'})"
        )
        # Dead if it's still in-progress past the threshold, or already finished
        # yet a lock is somehow still held (orphaned — release never ran).
        stale = bool(held) and (terminal or age > args.max_age)
    else:
        print("newest session: none found")

    if not held and not locks:
        print(
            "\n=> Nothing is blocked. Any stuck row is just a stale display row "
            "(ages out via the 7-day compose_sessions TTL). No action needed."
        )
        return

    # --- diagnose-only (no action flag) ---
    if not args.clear and not args.force:
        if stale:
            print(
                "\n=> A compose lock is held and the newest compose looks DEAD. "
                "Re-run with --clear to release it (add --drain to retry the "
                "pending row now)."
            )
        else:
            print(
                f"\n=> A compose lock is held and the newest compose may still be "
                f"running (in-progress, age <= {args.max_age}s). Wait for it to finish "
                f"or the lock to expire (ttl {ttl}s); re-run with --force only if you "
                f"are certain no compose is running."
            )
        return

    # --- act ---
    if args.clear and not stale and not args.force:
        print(
            f"\n=> Refusing to clear: the newest compose does not look dead "
            f"(needs in-progress AND age > {args.max_age}s, or terminal-with-held-lock). "
            f"Use --force to override after confirming no worker is composing."
        )
        return

    deleted = 0
    for k in locks or [_GLOBAL_LOCK]:
        with contextlib.suppress(Exception):
            deleted += int(r.delete(k))
    print(f"\ncleared {deleted} compose lock(s).")

    if args.drain:
        try:
            from app.modules.newspaper.tasks.queue_drain_tasks import (
                drain_standard_publish_queue,
            )

            drain_standard_publish_queue.delay()
            print("enqueued drain_standard_publish_queue — workers will retry pending rows.")
        except Exception as exc:
            print(f"(could not enqueue drain: {exc}; the safety-net beat will still retry)")
    else:
        print("the pending row will retry on the next drain beat (add --drain to do it now).")


if __name__ == "__main__":
    main()

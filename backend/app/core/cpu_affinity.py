"""Pin the current OS thread to a single CPU core (Linux)."""

from __future__ import annotations

import itertools
import logging
import os
import threading

_log = logging.getLogger(__name__)
_core_counter = itertools.count()
_counter_lock = threading.Lock()


def cpu_count() -> int:
    return os.cpu_count() or 1


def pin_current_thread_to_core(core_id: int) -> None:
    """Bind the calling thread to ``core_id`` (modulo CPU count).

    On Linux, ``os.sched_setaffinity(0, …)`` applies to the *calling thread*.
    No-ops on platforms without affinity support.
    """
    n = cpu_count()
    core = int(core_id) % n
    try:
        os.sched_setaffinity(0, {core})
    except (AttributeError, OSError) as exc:
        _log.warning("cpu affinity unavailable (core=%s): %s", core, exc)
        return
    _log.info("thread %s pinned to core %s", threading.get_native_id(), core)


def pin_next_pool_thread() -> None:
    """ThreadPoolExecutor initializer: assign cores round-robin."""
    with _counter_lock:
        core = next(_core_counter)
    pin_current_thread_to_core(core)

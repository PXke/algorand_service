"""celery_app.py's worker_process_init signal handlers: prefork-safety resets for process-cached clients.

_reset_cassandra_session (pre-existing) has no dedicated test; this covers
its new sibling, _reset_http_client_cache, added alongside
app.core.http_client.get_http_client -- see that module's docstring for why
a forked Celery worker child must never inherit a client built (and
possibly already holding open sockets) in the parent.
"""

from __future__ import annotations

from app import celery_app as celery_app_module
from app.core.http_client import get_http_client


def test_reset_http_client_cache_is_registered_on_worker_process_init() -> None:
    """_reset_http_client_cache is actually wired to the worker_process_init signal, not just defined and forgotten."""
    from celery.signals import worker_process_init

    # celery.utils.dispatch.signal.Signal.receivers is a list of
    # ((lookup_key), weakref-to-callback) pairs; resolve each weakref to get
    # the live callback function back.
    receiver_funcs = {ref() for _key, ref in worker_process_init.receivers}
    assert celery_app_module._reset_http_client_cache in receiver_funcs


def test_reset_http_client_cache_clears_the_cache() -> None:
    """Calling the hook (what worker_process_init fires on every forked child) forces the next get_http_client() call to build a fresh client instead of reusing whatever was cached before the fork."""
    before = get_http_client(timeout=12.0)

    celery_app_module._reset_http_client_cache()

    after = get_http_client(timeout=12.0)
    assert before is not after

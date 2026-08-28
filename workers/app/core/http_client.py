"""Process-cached httpx.Client for workers/.

Before this module, workers/ built a fresh ``httpx.Client()`` for nearly
every outbound HTTP call -- a fresh scan (``grep -rn "httpx.Client(" workers/``)
found ~20 such construction sites across the codebase, from the shared
SSRF-guarded fetch helper (``net_guard.guarded_get``) to the LLM provider
POST loops to per-page frontier link previews. Each one pays a fresh TCP/TLS
handshake because httpx.Client's internal httpcore connection pool -- the
thing that actually keeps sockets warm between calls -- gets built and torn
down with the client object instead of being reused. The worst offender was
the crawler frontier: ``link_extractor.enqueue_page_links`` previews every
not-yet-known external link on a page via ``fetch_domain_preview`` ->
``guarded_get``, and a single page routinely has 60-200 links.

``get_http_client()`` caches one client per distinct (timeout,
follow_redirects, base_url) combination for the life of the process, the
same one-client-per-process idea as this package's ``get_redis()``. Callers
that need per-call state (auth headers, a User-Agent) pass it to the
request method (``client.get(url, headers=...)``) instead of baking it into
client construction; httpx merges per-request headers with any client-level
defaults, so this is behaviorally identical to constructing a fresh client
with those headers every time -- just without paying to reconstruct the
connection pool.

Thread-safety: httpx.Client is documented safe for concurrent use from
multiple threads within one process -- verified directly against this
venv's installed httpcore (1.0.9) rather than assumed: ``_sync/
connection_pool.py``'s ``ConnectionPool`` takes an ``_optional_thread_lock``
(a real ``threading.Lock``, see ``_synchronization.py``) around its own
pool bookkeeping, not just an async-mode no-op. A client shared across
threads in one process is safe.

Fork-safety is the real question, since Celery's worker pool here is
prefork (see ``celery_app.py`` / ``deploy/scripts/run_celery.sh``'s default
``celery worker`` invocation -- no ``--pool`` override, unlike the
dedicated translate worker, which explicitly opts into ``--pool=solo``
because of an unrelated free-threading/fork crash). Unlike the
cassandra-driver session this module's sibling (``get_cassandra_session``)
protects against, sync httpx.Client does not run a background IO thread --
each request executes synchronously on the calling thread/socket, so there
is no "thread reference broken by fork" failure mode. The risk that
genuinely exists is a client built AND USED (i.e. with real open sockets)
in the parent process before Celery forks its worker children: a forked
child would inherit those live socket file descriptors, and both processes
writing to the same TCP connection would corrupt each other's requests. In
this codebase ``get_http_client()`` is only ever called lazily from inside
task/tool code that runs after the fork, never at import time, so this
should not happen in practice -- but the cache is still cleared on
``worker_process_init`` (same signal, same handler grouping as
``_reset_cassandra_session`` in celery_app.py) as deliberate belt-and-braces
insurance: cheap to do, and it means a forked child always builds its own
client/connection pool on first use rather than ever being able to inherit
one from the parent, regardless of what future code ends up calling
``get_http_client()`` at import time.

Existing per-call ``httpx.Client(...)`` sites now delegate here; callers and
tests that already monkeypatch ``httpx.Client`` on whatever module/package
imports it are unaffected, because ``import httpx; httpx.Client(...)`` here
resolves the SAME shared ``httpx`` module object those monkeypatches
mutate. Tests that exercise this module directly (or trigger it
transitively) must clear the cache between cases -- see
``workers/tests/conftest.py``'s ``_clear_http_client_cache`` autouse
fixture, which does this for the whole suite automatically.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx


@cache
def get_http_client(
    *, timeout: float = 12.0, follow_redirects: bool = False, base_url: str = ""
) -> httpx.Client:
    """Return a process-cached httpx.Client for the given (timeout, follow_redirects, base_url).

    Cached per distinct combination, so callers needing different timeouts,
    redirect behavior, or a fixed base_url each get their own pooled client,
    reused across calls instead of paying a fresh TCP/TLS handshake every
    time. Callers needing per-call auth headers or a User-Agent should pass
    them to the request method (``client.get(url, headers=...)``) rather
    than baking them into client construction -- see the module docstring.
    Never call ``.close()`` or use this as a context manager (``with
    get_http_client() as client``): that tears down the shared connection
    pool out from under every other caller holding the same cached client.
    """
    import httpx

    return httpx.Client(timeout=timeout, follow_redirects=follow_redirects, base_url=base_url)

"""Shared Cassandra session for reading chain tables written by Conduit."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

from cassandra.auth import PlainTextAuthProvider
from cassandra.cluster import EXEC_PROFILE_DEFAULT, Cluster, ExecutionProfile, ResponseFuture
from cassandra.cluster import ResultSet as CassandraResultSet
from cassandra.cluster import Session as CassandraSession
from cassandra.concurrent import execute_concurrent, execute_concurrent_with_args
from cassandra.io.libevreactor import LibevConnection
from cassandra.policies import (
    DCAwareRoundRobinPolicy,
    ExponentialReconnectionPolicy,
    RetryPolicy,
    TokenAwarePolicy,
)
from cassandra.query import PreparedStatement

from app.core.config import settings


@lru_cache(maxsize=1)
def get_cassandra_session() -> CassandraSession:
    """Return the process-wide cached Cassandra session, connecting on first use."""
    hosts = [h.strip() for h in settings.cassandra_hosts.split(",") if h.strip()]
    profile = ExecutionProfile(
        # Token-aware: route each query straight to a replica owning the
        # partition (fewer coordinator hops), falling back to DC-aware RR.
        load_balancing_policy=TokenAwarePolicy(
            DCAwareRoundRobinPolicy(local_dc=settings.cassandra_local_dc)
        ),
        retry_policy=RetryPolicy(),
    )
    auth_provider = None
    if settings.cassandra_username:
        auth_provider = PlainTextAuthProvider(
            username=settings.cassandra_username,
            password=settings.cassandra_password,
        )
    cluster = Cluster(
        hosts,
        execution_profiles={EXEC_PROFILE_DEFAULT: profile},
        reconnection_policy=ExponentialReconnectionPolicy(base_delay=1.0, max_delay=30.0),
        auth_provider=auth_provider,
        # Pin the reactor. libev (a C event loop, shipped inside the driver's
        # manylinux wheel) is ALREADY what the driver would pick here, so this is
        # a guard rather than a change: the driver's default order is
        # gevent -> eventlet -> libev -> asyncore, so anything that drags gevent
        # or eventlet into the venv would silently win over libev and swap the
        # I/O model underneath us. `greenlet` is already a transitive dep, so
        # that is one hop away. Pinning also makes a missing libev fail loudly at
        # import instead of degrading quietly -- and on 3.14 there is no quiet
        # degradation available anyway: asyncore was removed in 3.12, so with
        # libev absent the driver raises DependencyException and nothing boots.
        connection_class=LibevConnection,
        # Cassandra 5 speaks native protocol v5; pinning it skips the driver's
        # trial-and-error downgrade (66 -> 65 -> 5) that warns on every boot.
        protocol_version=5,
    )
    return cluster.connect(settings.cassandra_keyspace)


@lru_cache(maxsize=512)
def prepare_cached(cql: str) -> PreparedStatement:
    """Prepare a statement once and reuse it (the driver caches the server-side plan and enables token-aware routing for it). Use `?` placeholders, not `%s`. Safe to call on hot paths — preparation happens only on the first call per unique CQL string. This is the mechanism behind the statement registry in `app.core.statements`; prefer the named registry entries at call sites."""
    return get_cassandra_session().prepare(cql)


def await_response_future(future: ResponseFuture) -> asyncio.Future:
    """Turn a driver ResponseFuture into something an event loop can await.

    `session.execute()` is literally `execute_async(...).result()`, and
    `ResponseFuture.result()` is `threading.Event.wait()` -- it parks the calling
    OS thread. Inside an `async def` handler that thread is the event loop, so a
    single query stalls every other in-flight request. Using `execute_async` and
    deferring `.result()` does not help; it only moves where the block happens.

    So never call `.result()`: ask the driver to notify us instead, and hand the
    outcome to the loop, which lets `await` yield rather than park.

    Three driver behaviours this has to respect (all documented on
    `ResponseFuture.add_callback`):

    - Callbacks run on the driver's I/O thread, and "no further IO will be
      processed until the callback returns" -- so the callback must do the
      minimum and get off. Scheduling onto the loop is exactly that.
    - If the result already arrived, the callback fires *synchronously, on this
      thread*, before add_callbacks returns. `call_soon_threadsafe` is correct
      either way, which is why it is used unconditionally.
    - Exceptions raised inside a callback "will be ignored". That makes a naive
      `set_result` a hang risk rather than an error: if the asyncio future was
      already resolved or cancelled, InvalidStateError would be swallowed and
      the `await` would never wake. Hence the `done()` guards.

    asyncio.Future is not thread-safe, so the transfer MUST go through
    `call_soon_threadsafe` -- touching it from the driver's thread is a race that
    works nearly always and occasionally drops a wakeup.
    """
    loop = asyncio.get_running_loop()
    awaitable = loop.create_future()

    def _resolve(rows: object) -> None:
        if not awaitable.done():  # cancelled/duplicate -- see docstring
            awaitable.set_result(rows)

    def _fail(exc: BaseException) -> None:
        if not awaitable.done():
            awaitable.set_exception(exc)

    future.add_callbacks(
        lambda rows: loop.call_soon_threadsafe(_resolve, rows),
        lambda exc: loop.call_soon_threadsafe(_fail, exc),
    )
    return awaitable


async def execute_await(
    statement: PreparedStatement | str, params: Sequence | None = None
) -> CassandraResultSet:
    """Drop-in `session.execute()` that yields to the event loop instead of blocking it.

    Returns a ResultSet built exactly as `execute()` builds it, so call sites
    that iterate rows or use `.one()` need no change.

    Caveat -- paging: the awaited result covers the FIRST page only (the driver's
    default fetch_size is 5000 rows). Iterating a ResultSet past that boundary
    calls `fetch_next_page()`, which blocks on `.result()` again, putting the
    stall straight back on the loop. Safe for the LIMIT-bounded queries in the
    statement registry; for an unbounded scan, keep it off the loop some other
    way instead of reaching for this.
    """
    future = get_cassandra_session().execute_async(statement, params)
    rows = await await_response_future(future)
    return CassandraResultSet(future, rows)


def execute_parallel(
    statements_and_params: Sequence[tuple[PreparedStatement | str, Sequence]],
    *,
    concurrency: int = 32,
    raise_on_error: bool = True,
) -> list[tuple[bool, Any]]:
    """Run heterogeneous (statement, params) pairs concurrently against the shared session; results come back in input order as a list of (success, result_or_exc) tuples. Use for independent queries that would otherwise run in a sequential loop (per-bucket / per-day fan-outs)."""
    return execute_concurrent(
        get_cassandra_session(),
        list(statements_and_params),
        concurrency=concurrency,
        raise_on_first_error=raise_on_error,
    )


def execute_parallel_with_args(
    statement: PreparedStatement,
    args_seq: Sequence[Sequence],
    *,
    concurrency: int = 32,
    raise_on_error: bool = True,
) -> list[tuple[bool, Any]]:
    """Run ONE statement concurrently over many parameter tuples; results in input order as (success, result_or_exc) tuples."""
    return execute_concurrent_with_args(
        get_cassandra_session(),
        statement,
        list(args_seq),
        concurrency=concurrency,
        raise_on_first_error=raise_on_error,
    )


def get_chain_head_round() -> int | None:
    """Latest round ingested by Conduit (`conduit_meta.last_ingested_round`)."""
    from app.modules.chain.repository import get_chain_repository

    return get_chain_repository().get_chain_head_round()

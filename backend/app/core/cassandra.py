"""Shared Cassandra session for reading chain tables written by Conduit."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from functools import lru_cache
from typing import Any

from cassandra.auth import PlainTextAuthProvider
from cassandra.cluster import EXEC_PROFILE_DEFAULT, Cluster, ExecutionProfile
from cassandra.cluster import ResponseFuture
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

logger = logging.getLogger(__name__)


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


def execute_async(
    statement: PreparedStatement | str,
    parameters: Sequence | None = None,
) -> ResponseFuture:
    """Start a query without waiting; call ``.result()`` when the rows are needed.

    Prefer this over sync ``session.execute`` when useful work can run before the
    response is required (header parsing, Redis, mapping other rows, etc.).
    """
    return get_cassandra_session().execute_async(statement, parameters)


def fire_and_forget(
    statement: PreparedStatement | str,
    parameters: Sequence | None = None,
    *,
    on_error: str = "cassandra fire-and-forget failed",
) -> None:
    """Enqueue a write and return immediately (analytics / best-effort counters).

    Errors are logged via the driver's errback; the request path is not blocked
    and does not see the failure.
    """
    future = execute_async(statement, parameters)

    def _log_err(exc: BaseException) -> None:
        logger.warning("%s: %s", on_error, exc)

    future.add_errback(_log_err)


def execute_then(
    statement: PreparedStatement | str,
    parameters: Sequence | None = None,
    *,
    overlap: Callable[[], Any] | None = None,
) -> Any:
    """Start ``statement``, run ``overlap()`` while in flight, then wait for rows.

    ``overlap`` should not depend on the query result. Returns the same ResultSet
    shape as ``session.execute``.
    """
    future = execute_async(statement, parameters)
    if overlap is not None:
        overlap()
    return future.result()


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

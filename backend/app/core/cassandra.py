"""Shared Cassandra session for reading chain tables written by Conduit."""

from __future__ import annotations

from functools import lru_cache

from cassandra.auth import PlainTextAuthProvider
from cassandra.cluster import EXEC_PROFILE_DEFAULT, Cluster, ExecutionProfile
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
        # libev: the fast C event loop for the driver's async I/O (vs the default
        # pure-python asyncore reactor). Every execute() rides this loop.
        connection_class=LibevConnection,
    )
    return cluster.connect(settings.cassandra_keyspace)


@lru_cache(maxsize=512)
def prepare_cached(cql: str) -> PreparedStatement:
    """Prepare a statement once and reuse it (the driver caches the server-side
    plan and enables token-aware routing for it). Use `?` placeholders, not `%s`.
    Safe to call on hot paths — preparation happens only on the first call per
    unique CQL string. This is the mechanism behind the statement registry in
    `app.core.statements`; prefer the named registry entries at call sites."""
    return get_cassandra_session().prepare(cql)


def execute_parallel(statements_and_params, *, concurrency: int = 32, raise_on_error: bool = True):
    """Run heterogeneous (statement, params) pairs concurrently against the shared
    session; results come back in input order as a list of (success, result_or_exc)
    tuples. Use for independent queries that would otherwise run in a sequential
    loop (per-bucket / per-day fan-outs)."""
    return execute_concurrent(
        get_cassandra_session(),
        list(statements_and_params),
        concurrency=concurrency,
        raise_on_first_error=raise_on_error,
    )


def execute_parallel_with_args(statement, args_seq, *, concurrency: int = 32, raise_on_error: bool = True):
    """Run ONE statement concurrently over many parameter tuples; results in input
    order as (success, result_or_exc) tuples."""
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

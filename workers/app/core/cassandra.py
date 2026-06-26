from __future__ import annotations

from functools import lru_cache

from app.core.config import (
    CASSANDRA_HOSTS,
    CASSANDRA_KEYSPACE,
    CASSANDRA_LOCAL_DC,
    CASSANDRA_PASSWORD,
    CASSANDRA_USERNAME,
)


@lru_cache(maxsize=1)
def get_cassandra_session():
    from cassandra.auth import PlainTextAuthProvider
    from cassandra.cluster import EXEC_PROFILE_DEFAULT, Cluster, ExecutionProfile
    from cassandra.policies import (
        DCAwareRoundRobinPolicy,
        ExponentialReconnectionPolicy,
        RetryPolicy,
    )

    hosts = [host.strip() for host in CASSANDRA_HOSTS.split(",") if host.strip()]
    profile = ExecutionProfile(
        load_balancing_policy=DCAwareRoundRobinPolicy(local_dc=CASSANDRA_LOCAL_DC),
        retry_policy=RetryPolicy(),
    )
    auth_provider = None
    if CASSANDRA_USERNAME:
        auth_provider = PlainTextAuthProvider(
            username=CASSANDRA_USERNAME, password=CASSANDRA_PASSWORD
        )
    cluster = Cluster(
        hosts,
        execution_profiles={EXEC_PROFILE_DEFAULT: profile},
        reconnection_policy=ExponentialReconnectionPolicy(base_delay=1.0, max_delay=30.0),
        auth_provider=auth_provider,
    )
    return cluster.connect(CASSANDRA_KEYSPACE)

#!/bin/bash
# Single-node Docker Cassandra: create keyspace before ledger migration 000 (USE algorand_platform).
set -eu

host="${CASSANDRA_HOSTS:-cassandra}"
host="${host%%,*}"
keyspace="${CASSANDRA_KEYSPACE:-algorand_platform}"

python3 - <<PY
from cassandra.cluster import Cluster

hosts = ["${host}"]
keyspace = "${keyspace}"
cluster = Cluster(hosts)
session = cluster.connect()
session.execute(
    f"""
    CREATE KEYSPACE IF NOT EXISTS {keyspace}
      WITH replication = {{'class': 'SimpleStrategy', 'replication_factor': 1}}
    """
)
print(f"keyspace ready: {keyspace}")
cluster.shutdown()
PY

#!/usr/bin/env bash
# Run backend pytest against docker-compose deps (Cassandra, Redis, …).
set -euo pipefail

cd /app/backend
export PYTHONPATH=.
export CASSANDRA_HOSTS="${CASSANDRA_HOSTS:-cassandra}"
export CASSANDRA_KEYSPACE="${CASSANDRA_KEYSPACE:-algorand_platform}"
export CASSANDRA_LOCAL_DC="${CASSANDRA_LOCAL_DC:-datacenter1}"
export REDIS_URL="${REDIS_URL:-redis://redis:6379/0}"

/usr/local/bin/platform-docker/wait-for-cassandra.sh

if [[ "${SKIP_LINT:-0}" != "1" ]]; then
  /usr/local/bin/platform-docker/lint.sh
fi

extra=()
if [[ "${SKIP_ARC0060_TESTS:-0}" == "1" ]]; then
  extra+=(--ignore=tests/test_arc0060_verify.py)
fi

exec pytest tests/ -q "${extra[@]}" "$@"

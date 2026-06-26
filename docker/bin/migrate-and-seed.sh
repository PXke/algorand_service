#!/bin/bash
set -eu

cd /app
export CASSANDRA_HOSTS="${CASSANDRA_HOSTS:-cassandra}"
export CASSANDRA_KEYSPACE="${CASSANDRA_KEYSPACE:-algorand_platform}"
export CASSANDRA_LOCAL_DC="${CASSANDRA_LOCAL_DC:-datacenter1}"

/usr/local/bin/platform-docker/wait-for-cassandra.sh
/usr/local/bin/platform-docker/bootstrap-keyspace.sh

echo "Applying CQL migrations..."
python3 deploy/scripts/cql_migrate.py apply --tier all

if [[ "${SEED_SERVICE_REGISTRY:-1}" == "1" ]]; then
  echo "Seeding service_registry..."
  python3 deploy/scripts/seed_service_registry.py
fi

echo "Migrations and seed complete."

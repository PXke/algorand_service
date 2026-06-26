# Brick: Cassandra repository (shared access)

## Goal

Consistent Cassandra session and repository patterns for API (and documented parity with workers).

## Status

`done`

## Features (should do)

- `get_cassandra_session()` from config (`CASSANDRA_HOSTS`, `CASSANDRA_KEYSPACE`)
- Chain repository: read tx by id, list tx by round, read Conduit head round
- Product stores implement insert/list/get for their tables (see per-product bricks)

## Good to have

- Connection health reflected in `health-observability` — **done** (`/health/ready`)
- Shared retry policy for transient Cassandra errors — **done** (`RetryPolicy` + `ExponentialReconnectionPolicy` in both API and workers sessions)

## Future improvements

- `backend/app/core/ports/` interfaces shared by API and workers package
- Read replicas / local-quorum policy per query type
- Repository integration tests with Testcontainers
- Metrics: query latency per table

## Standards & RFCs

[Cassandra CQL](https://cassandra.apache.org/doc/latest/cassandra/developing/cql/) for reads/writes. [standards-and-rfcs.md](../architecture/standards-and-rfcs.md).

## Depends on

- `cassandra-schema-migrations`, `conduit-cassandra`

## Code map

- `backend/app/core/cassandra.py`
- `backend/app/modules/chain/repository.py`
- `backend/app/modules/news/stores/cassandra.py`
- `backend/app/modules/suggestions/stores/cassandra.py`
- `backend/app/modules/registry/repository.py`
- `workers/app/core/cassandra.py` (worker-side duplicate for isolation)

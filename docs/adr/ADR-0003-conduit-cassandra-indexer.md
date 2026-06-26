# ADR-0003: Conduit Cassandra Exporter for On-Chain Data

## Status

Accepted

## Context

The platform needs durable on-chain data (blocks, transactions) in Cassandra for suggestions verification, activity feeds, and security workers—without querying algod for every read.

Algorand provides [Conduit](https://github.com/algorand/conduit) with a PostgreSQL exporter backed by Indexer `idb`. There is no official Cassandra exporter.

## Decision

Add a custom Conduit **exporter** plugin (`cassandra`) in `conduit/` that:

- Uses the standard **algod importer** in **follower** mode.
- Writes a platform-focused schema (`blocks`, `transactions_by_*`, `conduit_meta`).
- Runs as a separate long-lived process (systemd), not inside Robyn.

Robyn and Celery read Cassandra via `cassandra-driver`.

## Consequences

- Requires operating algod (follower) + Conduit + Cassandra.
- v1 schema is not full Indexer parity; can extend tables or add processors later.
- Go toolchain needed to build `conduit/bin/conduit`.

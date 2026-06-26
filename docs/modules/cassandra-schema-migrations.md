# Brick: Cassandra schema migrations

## Goal

Track which CQL is applied per cluster; apply only new migrations; separate prod vs experimental tiers.

## Status

`done`

## Features (should do)

- Manifest `schema/migrations/manifest.toml` listing every migration (stream, version, tier, status)
- Ledger table `schema_migrations` in Cassandra
- CLI: `status`, `apply`, `apply --tier prod`, `register-baseline`
- Versioned CQL under `backend/schema/migrations/app/` and `conduit/schema/migrations/chain/`
- Document monolith `.cql` files as deprecated snapshots

## Good to have

- Dry-run output shows statement count per file
- CI check: every `.cql` file referenced in manifest

## Future improvements

- Automated baseline on first deploy per environment
- Down migrations (rare; documented exceptions only)
- Multi-datacenter replication templates per env in migration `001`
- Drift detection: checksum mismatch fails deploy

## Standards & RFCs

| Reference | Use |
|-----------|-----|
| [Cassandra CQL](https://cassandra.apache.org/doc/latest/cassandra/developing/cql/) | DDL/DML |
| Internal | `schema_migrations` ledger |

[standards-and-rfcs.md](../architecture/standards-and-rfcs.md#cassandra-schema-migrations), [cql-migrations.md](../architecture/cql-migrations.md).

## Depends on

- Cassandra keyspace `algorand_platform`

## Code map

- `schema/migrations/manifest.toml`
- `deploy/scripts/cql-migrate.sh`, `deploy/scripts/cql_migrate.py`
- `docs/architecture/cql-migrations.md`

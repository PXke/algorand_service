# Brick: Conduit → Cassandra

## Goal

Stream **algod** follower blocks into Cassandra for platform chain reads (not full Indexer PostgreSQL).

## Status

`done`

## Features (should do)

- Custom Conduit exporter plugin `cassandra`
- Tables: `conduit_meta`, `blocks`, `transactions_by_round`, `transactions_by_id`, `transactions_by_sender`, `transactions_by_receiver`
- Columns `receiver`, `amount_microalgos` on txn tables for payment matching
- Resume cursor `conduit_meta.next_round` and `last_ingested_round`
- algod importer in **follower** mode (ledger deltas required)
- Chain migrations `001`–`006` via `cql-migrate` (prefer `auto_migrate: false` in prod)
- systemd unit template for long-running process

## Good to have

- `write_transactions_by_receiver` enabled in prod config for treasury analytics
- Makefile `test` and `conduit` build targets documented
- Sample `conduit.yml.example` with TestNet algod URL

## Future improvements

- Conduit processors for app call args / inner txns
- Backfill tool from Indexer export
- Multi-region Cassandra replication runbook per keyspace
- Metrics exporter for rounds behind algod head
- Full Indexer parity tables (only if product requires)

## Standards & RFCs

| Reference | Use |
|-----------|-----|
| [Conduit](https://github.com/algorand/conduit) | Exporter plugin |
| [go-algorand-sdk](https://github.com/algorand/go-algorand-sdk) | Transaction encoding |
| Cassandra CQL | Chain tables |

[standards-and-rfcs.md](../architecture/standards-and-rfcs.md#conduit-cassandra).

## Depends on

- Cassandra keyspace, algod API access
- `cassandra-schema-migrations`

## Code map

- `conduit/`, `conduit/plugin/exporter/`
- `conduit/schema/migrations/chain/`
- `conduit/config/conduit.yml.example`
- `deploy/systemd/algorand-platform-conduit.service`

## Related bricks

- [chain-read.md](chain-read.md)
- [cassandra-schema-migrations.md](cassandra-schema-migrations.md)

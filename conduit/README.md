# Conduit Cassandra Exporter

[Algorand Conduit](https://github.com/algorand/conduit) pipeline that ingests blocks from **algod** (follower mode) and writes them to **Apache Cassandra** — the on-chain leg of the platform database.

This is separate from the Robyn API: Conduit runs as its own long-lived process.

## Architecture

```text
algod (follower)  -->  Conduit importer  -->  cassandra exporter  -->  Cassandra
                                                              ^
Robyn / workers read chain tables -----------------------------|
```

## Build

```bash
cd conduit
go mod tidy
make conduit
./bin/conduit list   # should include exporter "cassandra"
```

## Configure

1. Create keyspace (see `schema/cassandra.cql`).
2. Copy `config/conduit.yml.example` and set algod URL/token.
3. Initialize data dir:

```bash
./bin/conduit init --importer algod --exporter cassandra -d ./conduit_data
# merge exporter config from config/conduit.yml.example into conduit_data/conduit.yml
```

4. Run:

```bash
./bin/conduit -f ./conduit_data/conduit.yml
```

## Tables

| Table | Purpose |
|-------|---------|
| `conduit_meta` | Cursor (`next_round`), genesis JSON |
| `blocks` | Block headers per round |
| `transactions_by_round` | Txns in block order |
| `transactions_by_id` | Lookup by txid |
| `transactions_by_sender` | Recent activity per account |
| `transactions_by_receiver` | Payments to treasury / services |

Prefer versioned CQL: `schema/migrations/manifest.toml` + `deploy/scripts/cql-migrate.sh` (set `auto_migrate: false` in production).

## vs PostgreSQL exporter

Algorand’s built-in `postgresql` exporter uses the full **Indexer `idb`** schema. This exporter uses a **platform-focused** Cassandra model that is easier to evolve with the newspaper / suggestions products. Full indexer parity can be added later via processors or expanded tables.

## Deploy

See `deploy/systemd/algorand-platform-conduit.service` and `docs/modules/conduit-cassandra.md`.

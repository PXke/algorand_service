# Brick: Chain read (API)

## Goal

API and workers read **indexed** transactions from Cassandra (Conduit), not live algod for product hot paths.

## Status

`done`

## Features (should do)

- `get_transaction(txid)` → `IndexedTransaction` (sender, type, receiver, amount, `txn_json`)
- `get_chain_head_round()` from `conduit_meta.last_ingested_round`
- `list_transactions_for_round(round)` for chain tail
- Map Cassandra rows via `row_to_indexed_transaction()`
- Lazy Cassandra import so unit tests run without driver

## Good to have

- In-memory / fake repository for tests (`FakeChainRepository`)
- Clear error when Conduit head is stale vs algod

## Future improvements

- Redis cache for hot txids (suggestion verify)
- Admin API: search tx by sender/receiver with pagination
- Read-through to algod only when explicitly requested (`?source=algod`)
- Batch fetch multiple txids for workers

## Standards & RFCs

[Algorand transaction reference](https://developer.algorand.org/docs/get-details/transactions/) for `pay` / `appl` / `axfer` fields. [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#chain-read).

## Depends on

- `conduit-cassandra`

## Code map

- `backend/app/modules/chain/`
- `workers/app/modules/chain_tail/chain_reader.py`

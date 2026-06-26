# Brick: Suggestions store

## Goal

Persist suggestions and upvotes in Cassandra (in-memory for local dev).

## Status

`done`

## Features (should do)

- `suggestions_by_status` with clustering by `created_at`
- Secondary index on `submission_txid` for dedupe
- `upvotes_by_suggestion` primary key `(suggestion_id, wallet_address)`
- `SUGGESTION_STORE` / `UPVOTE_STORE` env select implementation
- `get(suggestion_id)` for upvote path

## Good to have

- Migration script to backfill from in-memory test data (dev only)

## Future improvements

- `expires_at` column + compaction job (90-day policy)
- Vote count denormalized on suggestion row
- Materialized view: suggestions by wallet
- Soft-delete status without removing on-chain proof reference

## Standards & RFCs

Cassandra CQL + [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259). [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#suggestions-api--suggestions-store).

## Depends on

- `cassandra-schema-migrations` (app `001`–`002`)

## Code map

- `backend/app/modules/suggestions/stores/`
- `backend/schema/migrations/app/001_suggestions.cql`
- `backend/schema/migrations/app/002_upvotes.cql`

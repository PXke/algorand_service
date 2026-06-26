-- DEPRECATED for deploy: use versioned migrations + cql-migrate instead.
-- Snapshot of app stream 001–003. Registry: schema/migrations/manifest.toml
--
-- Application tables (Robyn API), separate from Conduit chain tables.

USE algorand_platform;

CREATE TABLE IF NOT EXISTS suggestions_by_status (
  status text,
  created_at timestamp,
  suggestion_id uuid,
  wallet_address text,
  title text,
  body text,
  submission_txid text,
  PRIMARY KEY ((status), created_at, suggestion_id)
) WITH CLUSTERING ORDER BY (created_at DESC);

CREATE INDEX IF NOT EXISTS suggestions_submission_txid_idx
  ON suggestions_by_status (submission_txid);

CREATE TABLE IF NOT EXISTS upvotes_by_suggestion (
  suggestion_id text,
  wallet_address text,
  created_at timestamp,
  PRIMARY KEY ((suggestion_id), wallet_address)
);

CREATE TABLE IF NOT EXISTS service_registry (
  service_id text PRIMARY KEY,
  display_name text,
  match_kind text,
  match_value text,
  enabled boolean,
  updated_at timestamp
);

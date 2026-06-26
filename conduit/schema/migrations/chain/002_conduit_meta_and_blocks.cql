USE algorand_platform;

CREATE TABLE IF NOT EXISTS conduit_meta (
  id text PRIMARY KEY,
  value text,
  updated_at timestamp
);

CREATE TABLE IF NOT EXISTS blocks (
  round bigint PRIMARY KEY,
  block_timestamp bigint,
  proposer text,
  txn_count int,
  block_header_json text,
  ingested_at timestamp
);

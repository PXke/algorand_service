package exporter

// CQL applied when auto_migrate is true (idempotent).
const schemaCQL = `
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

CREATE TABLE IF NOT EXISTS transactions_by_round (
  round bigint,
  intra int,
  txid text,
  sender text,
  txn_type text,
  fee bigint,
  note blob,
  txn_json text,
  PRIMARY KEY ((round), intra)
);

CREATE TABLE IF NOT EXISTS transactions_by_id (
  txid text PRIMARY KEY,
  round bigint,
  intra int,
  sender text,
  txn_type text,
  txn_json text,
  receiver text,
  amount_microalgos bigint
);

CREATE TABLE IF NOT EXISTS transactions_by_sender (
  sender text,
  round bigint,
  txid text,
  intra int,
  txn_type text,
  receiver text,
  amount_microalgos bigint,
  PRIMARY KEY ((sender), round, txid)
) WITH CLUSTERING ORDER BY (round DESC);

CREATE TABLE IF NOT EXISTS transactions_by_receiver (
  receiver text,
  round bigint,
  txid text,
  intra int,
  sender text,
  txn_type text,
  amount_microalgos bigint,
  PRIMARY KEY ((receiver), round, txid)
) WITH CLUSTERING ORDER BY (round DESC);
`

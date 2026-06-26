USE algorand_platform;

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
  txn_json text
);

USE algorand_platform;

CREATE TABLE IF NOT EXISTS transactions_by_sender (
  sender text,
  round bigint,
  txid text,
  intra int,
  txn_type text,
  PRIMARY KEY ((sender), round, txid)
) WITH CLUSTERING ORDER BY (round DESC);

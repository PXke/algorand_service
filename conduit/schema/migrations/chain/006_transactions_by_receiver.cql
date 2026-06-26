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

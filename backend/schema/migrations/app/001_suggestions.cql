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

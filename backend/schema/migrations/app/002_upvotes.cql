USE algorand_platform;

CREATE TABLE IF NOT EXISTS upvotes_by_suggestion (
  suggestion_id text,
  wallet_address text,
  created_at timestamp,
  PRIMARY KEY ((suggestion_id), wallet_address)
);

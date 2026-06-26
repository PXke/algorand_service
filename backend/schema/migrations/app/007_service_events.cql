USE algorand_platform;

CREATE TABLE IF NOT EXISTS service_events (
  service_id text,
  occurred_at timestamp,
  event_id uuid,
  txid text,
  round bigint,
  match_kind text,
  match_value text,
  PRIMARY KEY ((service_id), occurred_at, event_id)
) WITH CLUSTERING ORDER BY (occurred_at DESC);

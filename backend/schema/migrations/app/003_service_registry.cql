USE algorand_platform;

CREATE TABLE IF NOT EXISTS service_registry (
  service_id text PRIMARY KEY,
  display_name text,
  match_kind text,
  match_value text,
  enabled boolean,
  updated_at timestamp
);

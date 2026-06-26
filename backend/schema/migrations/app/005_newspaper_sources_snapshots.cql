USE algorand_platform;

CREATE TABLE IF NOT EXISTS page_sources (
  source_id text PRIMARY KEY,
  service_id text,
  url text,
  enabled boolean,
  updated_at timestamp
);

CREATE TABLE IF NOT EXISTS page_snapshots (
  source_id text,
  captured_at timestamp,
  content_hash text,
  title text,
  body text,
  PRIMARY KEY ((source_id), captured_at)
) WITH CLUSTERING ORDER BY (captured_at DESC);

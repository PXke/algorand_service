USE algorand_platform;

CREATE TABLE IF NOT EXISTS publish_queue (
  queue_id uuid PRIMARY KEY,
  status text,
  priority int,
  topic text,
  publish_kind text,
  service_id text,
  display_name text,
  scrape_url text,
  dedupe_key text,
  payload text,
  created_at timestamp,
  updated_at timestamp
);

CREATE TABLE IF NOT EXISTS publish_queue_pending (
  status text,
  priority int,
  created_at timestamp,
  queue_id uuid,
  service_id text,
  topic text,
  publish_kind text,
  PRIMARY KEY ((status), priority, created_at, queue_id)
) WITH CLUSTERING ORDER BY (priority DESC, created_at ASC);

CREATE TABLE IF NOT EXISTS publish_queue_dedupe (
  dedupe_key text PRIMARY KEY,
  queue_id uuid,
  created_at timestamp
);

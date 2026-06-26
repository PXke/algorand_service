USE algorand_platform;

CREATE TABLE IF NOT EXISTS url_queue (
  queue_id uuid PRIMARY KEY,
  url text,
  source text,
  priority int,
  enqueued_at timestamp,
  status text,
  metadata map<text, text>
);

CREATE TABLE IF NOT EXISTS url_queue_by_url (
  url text PRIMARY KEY,
  queue_id uuid,
  enqueued_at timestamp
);

CREATE TABLE IF NOT EXISTS url_queue_pending (
  status text,
  priority int,
  enqueued_at timestamp,
  queue_id uuid,
  url text,
  source text,
  PRIMARY KEY ((status), priority, enqueued_at, queue_id)
) WITH CLUSTERING ORDER BY (priority DESC, enqueued_at ASC);

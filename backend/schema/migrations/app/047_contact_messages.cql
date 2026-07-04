-- Reader-submitted messages from the public /contact form. Partitioned by
-- month bucket so the admin inbox read stays bounded -- the form is public
-- and rate-limited, but a spam burst must not grow one unbounded partition.
-- NOTE: no semicolons inside comments -- the migration splitter cuts on them.
CREATE TABLE IF NOT EXISTS contact_messages (
  bucket text,
  created_at timestamp,
  message_id uuid,
  name text,
  email text,
  message text,
  PRIMARY KEY (bucket, created_at, message_id)
) WITH CLUSTERING ORDER BY (created_at DESC, message_id ASC);

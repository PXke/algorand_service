USE algorand_platform;

-- Admin-approved articles waiting for a daily publish slot under the 7/day cap
-- The backend inserts here when the feed is already full for the UTC day
-- and a worker drains it in interest order as slots free up
CREATE TABLE IF NOT EXISTS pending_feed_queue (
  bucket text,
  interest_score double,
  approved_at timestamp,
  article_id uuid,
  PRIMARY KEY ((bucket), interest_score, approved_at, article_id)
) WITH CLUSTERING ORDER BY (interest_score DESC, approved_at ASC);

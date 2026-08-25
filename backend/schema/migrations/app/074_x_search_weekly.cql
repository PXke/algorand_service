USE algorand_platform;

-- Weekly-swept X (Twitter) recent-search results per tracked ecosystem
-- service, read back by the writer's search_x tool instead of a live
-- per-compose API call (redesigned 2026-08-25 -- see
-- workers/app/modules/newspaper/x_search_sweep.py). One row per service_id,
-- overwritten on each weekly sweep run -- this is a rolling cache of "this
-- week's posts about this service", not a history, so no clustering column
-- is needed.
CREATE TABLE IF NOT EXISTS x_search_weekly (
  service_id text PRIMARY KEY,
  display_name text,
  query text,
  posts_json text,
  post_count int,
  swept_at timestamp,
  error text
);

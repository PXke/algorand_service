USE algorand_platform;

-- Diagnostics for the '(direct)' referrer bucket, which collapses several very
-- different things (true direct, dark social, scripts with a browser-shaped UA).
-- NOTE: no semicolons in comments (the migrator splits on the first one).

-- Coarse UA-class split of human pageviews that land in '(direct)', so plain
-- desktop/mobile direct traffic can be told apart from non-browser-shaped UAs
-- that slip past the bot denylist. Read a day partition and aggregate in-app.
CREATE TABLE IF NOT EXISTS pageview_direct_uaclass_daily (
  day text,
  ua_class text,
  views counter,
  PRIMARY KEY (day, ua_class)
);

-- Short-lived raw sample of '(direct)' human requests (referer + UA + path) so
-- the real traffic behind the bucket can be eyeballed before tuning classifiers.
-- Rows expire after 7 days via the table default TTL. No client IP is stored.
-- Newest-first clustering so reads take the latest N rows of a day partition.
CREATE TABLE IF NOT EXISTS pageview_direct_sample (
  day text,
  ts timeuuid,
  path text,
  referer text,
  user_agent text,
  ua_class text,
  PRIMARY KEY (day, ts)
) WITH CLUSTERING ORDER BY (ts DESC)
  AND default_time_to_live = 604800;

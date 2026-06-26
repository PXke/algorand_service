USE algorand_platform;

-- Pageviews per path per day split by kind ('human' | 'bot'), so Top Pages can
-- show human vs bot side by side. Read a day partition and aggregate in-app,
-- same access pattern as pageview_path_daily (035) which this supersedes.
-- NOTE: no semicolons in comments (the migrator splits on the first one).
CREATE TABLE IF NOT EXISTS pageview_path_kind_daily (
  day text,
  path text,
  kind text,
  views counter,
  PRIMARY KEY (day, path, kind)
);

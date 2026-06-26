USE algorand_platform;

-- First-party traffic analytics, recorded server-side from the SSR document
-- routes (no client JS, within the Cassandra/Typesense/Redis-only stack).
-- Counter tables hold only the PK plus a single counter column.
-- NOTE: no semicolons in comments (the migrator splits on the first one).

-- Daily totals split by kind ('human' | 'bot'). Read by kind for a time series.
CREATE TABLE IF NOT EXISTS pageview_daily (
  kind text,
  day text,
  views counter,
  PRIMARY KEY (kind, day)
);

-- Human pageviews per path per day. Read a day partition and sort in-app for top pages.
CREATE TABLE IF NOT EXISTS pageview_path_daily (
  day text,
  path text,
  views counter,
  PRIMARY KEY (day, path)
);

-- Human pageviews per referrer host per day. '(direct)' when no/own referrer.
CREATE TABLE IF NOT EXISTS pageview_referrer_daily (
  day text,
  referrer text,
  views counter,
  PRIMARY KEY (day, referrer)
);

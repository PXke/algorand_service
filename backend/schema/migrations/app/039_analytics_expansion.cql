USE algorand_platform;

-- Second wave of first-party analytics: search demand, bot identification,
-- source-to-landing attribution, and dead-link tracking. All counter tables
-- (PK plus a single counter), read a day partition and aggregate in-app.
-- Unique-visitor counts live in Redis (HyperLogLog), not here.
-- NOTE: no semicolons in comments (the migrator splits on the first one).

-- Search terms entered per day. Editorial signal — what readers look for.
CREATE TABLE IF NOT EXISTS search_query_daily (
  day text,
  query text,
  searches counter,
  PRIMARY KEY (day, query)
);

-- Searches that returned zero results per day — content gaps worth writing.
CREATE TABLE IF NOT EXISTS search_zero_daily (
  day text,
  query text,
  searches counter,
  PRIMARY KEY (day, query)
);

-- Bot pageviews per identified crawler per day (Googlebot, GPTBot, ClaudeBot…),
-- so the bot column says WHICH crawlers index us instead of one opaque lump.
CREATE TABLE IF NOT EXISTS pageview_bot_daily (
  day text,
  bot text,
  views counter,
  PRIMARY KEY (day, bot)
);

-- Human pageviews per (referrer, landing path) per day — which source drives
-- which story. Read a day partition and aggregate the top pairs in-app.
CREATE TABLE IF NOT EXISTS pageview_referrer_path_daily (
  day text,
  referrer text,
  path text,
  views counter,
  PRIMARY KEY (day, referrer, path)
);

-- Requests to unknown article/section URLs per day — broken inbound links and
-- crawler waste. Keyed by the missed path.
CREATE TABLE IF NOT EXISTS pageview_notfound_daily (
  day text,
  path text,
  views counter,
  PRIMARY KEY (day, path)
);

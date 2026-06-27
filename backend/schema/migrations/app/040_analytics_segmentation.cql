USE algorand_platform;

-- Third wave of first-party analytics: site-wide device/browser segmentation,
-- hour-of-day distribution, and full external referrer URLs (not just the host).
-- All counter tables (PK plus a single counter), read a day partition and
-- aggregate in-app. Referrer categories and section rollups are DERIVED in the
-- read layer from existing tables and need no storage here.
-- NOTE: no semicolons in comments (the migrator splits on the first one).

-- Full external referrer URLs per day (normalized: tracking params stripped,
-- fragment dropped, length-capped). Host-only lives in pageview_referrer_daily —
-- this keeps the exact thread/aggregator page that drove a visit. External human
-- referrers only (direct/internal never get a row), bounding cardinality.
CREATE TABLE IF NOT EXISTS pageview_referrer_url_daily (
  day text,
  referrer_url text,
  views counter,
  PRIMARY KEY (day, referrer_url)
);

-- Device/UA class of every human pageview per day (desktop/mobile browser, etc.)
-- Generalizes pageview_direct_uaclass_daily (which only covered the direct bucket)
-- to all human traffic, for a site-wide device donut.
CREATE TABLE IF NOT EXISTS pageview_device_daily (
  day text,
  device text,
  views counter,
  PRIMARY KEY (day, device)
);

-- Browser family of every human pageview per day (Chrome/Safari/Firefox/Edge…).
CREATE TABLE IF NOT EXISTS pageview_browser_daily (
  day text,
  browser text,
  views counter,
  PRIMARY KEY (day, browser)
);

-- Human pageviews per hour-of-day (UTC, 0-23) per day — when readers show up.
-- Keyed by (day, hour) so a weekday x hour heatmap stays possible later.
CREATE TABLE IF NOT EXISTS pageview_hour_daily (
  day text,
  hour int,
  views counter,
  PRIMARY KEY (day, hour)
);

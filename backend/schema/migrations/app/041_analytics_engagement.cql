USE algorand_platform;

-- Engagement layer for first-party analytics: server-side session stitching from
-- the privacy-safe visitor token (no client JS, no raw IP). Sessions and the
-- new-vs-returning split live here as a counter table. Editorial scorecard and
-- AI-crawler visibility are DERIVED in the read layer from existing tables and
-- need no storage.
-- NOTE: no semicolons in comments (the migrator splits on the first one).

-- One row per new-session event, classified by whether the visitor token had been
-- seen before (vtype = new | returning). A "session" is a visitor's activity
-- within a 30-minute sliding window (tracked in Redis). Total sessions = the sum
-- across vtypes. Pages-per-visit = human pageviews divided by total sessions.
CREATE TABLE IF NOT EXISTS session_daily (
  day text,
  vtype text,
  sessions counter,
  PRIMARY KEY (day, vtype)
);

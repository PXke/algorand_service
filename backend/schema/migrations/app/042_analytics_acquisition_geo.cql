USE algorand_platform;

-- Acquisition + geography layer for first-party analytics: campaign attribution
-- (tagged links, the only reliable way to name dark-social/Facebook traffic that
-- arrives without a Referer) and country-level geography (privacy-safe, derived
-- from the client IP at record time via a local GeoIP database -- no IP stored).
-- Both are counter tables read a day partition at a time and aggregated in-app.
-- NOTE: no semicolons in comments (the migrator splits on the first one).

-- Human visits carrying a campaign tag (utm_* or ref on the landing URL), per
-- day. Lets a specific post/newsletter/link be attributed even when the Referer
-- is stripped or absent.
CREATE TABLE IF NOT EXISTS campaign_daily (
  day text,
  campaign text,
  views counter,
  PRIMARY KEY (day, campaign)
);

-- Human visits per ISO 3166-1 alpha-2 country code, per day. Country is resolved
-- from the client IP via a local DB-IP Lite database at record time -- the IP
-- itself is never persisted.
CREATE TABLE IF NOT EXISTS geo_country_daily (
  day text,
  country text,
  views counter,
  PRIMARY KEY (day, country)
);

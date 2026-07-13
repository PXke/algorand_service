USE algorand_platform;

-- Reader-preferred language per day, from the Accept-Language header of every
-- human pageview. Same one-partition-per-day counter-table shape as
-- pageview_device_daily/pageview_browser_daily (040) — a language donut next
-- to the existing device/browser breakdowns, and real demand data for which
-- article-translation languages to prioritize.
CREATE TABLE IF NOT EXISTS pageview_language_daily (
  day text,
  lang text,
  views counter,
  PRIMARY KEY (day, lang)
);

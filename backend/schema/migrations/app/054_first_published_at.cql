USE algorand_platform;

-- Original first-publication moment, preserved across recompose re-publishes.
-- Since 2026-07-15 an approved recompose re-stamps published_at to the apply
-- time (story returns to the top of the feed) -- without this column the
-- original date is lost, so the daily publish cap would count a refresh as a
-- new publish and the hot-feed velocity ranking would treat lifetime views as
-- earned since the refresh. NULL means never recomposed (first publish equals
-- published_at)
ALTER TABLE articles_by_id ADD first_published_at timestamp;

-- Feed projection copy so cap counting and hot ranking can read it without a
-- per-article detail lookup
ALTER TABLE articles_feed ADD first_published_at timestamp;

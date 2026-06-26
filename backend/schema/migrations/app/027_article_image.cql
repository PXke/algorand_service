USE algorand_platform;

-- Hero image (og:image captured at scrape time) surfaced in feed previews.
ALTER TABLE articles_feed ADD image_url text;
ALTER TABLE articles_by_id ADD image_url text;

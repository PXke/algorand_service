USE algorand_platform;

-- The favicon brand-chip on front-page tiles needs each article's DOMAIN, but the
-- feed projection (articles_feed) never carried source_url -- only the detail row
-- (articles_by_id) did. Add it here so the feed API can return it and the card
-- can derive the per-domain favicon. Backfilled going forward (old rows null).
-- NOTE: no semicolons in comments (the migrator splits on the first one).

ALTER TABLE articles_feed ADD source_url text;

USE algorand_platform;

-- Last content revision (edit / recompose) for the article. Surfaced as
-- dateModified in the NewsArticle JSON-LD and article meta plus sitemap
-- lastmod -- until now dateModified always equalled datePublished, so
-- crawlers were never told an article changed (known Bing-audit gap)
ALTER TABLE articles_by_id ADD updated_at timestamp;

-- Feed projection copy so sitemap lastmod can read it without a per-article
-- detail lookup
ALTER TABLE articles_feed ADD updated_at timestamp;

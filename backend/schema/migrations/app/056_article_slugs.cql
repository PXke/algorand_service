-- Descriptive article URLs: /news/articles/<slug> instead of /news/articles/<uuid>
--
-- Google's SEO starter guide asks for URLs that carry words rather than IDs.
-- Additive only: nothing reads these columns until the URL builders are
-- switched over, so applying this migration changes no served URL.
--
-- A slug that has been served is a PERMANENT URL. Retitling an article must
-- never move it, and a slug is never reassigned to a different article -- if
-- one has to change, the old value redirects to the new one and is kept.

-- The article's own slug, so a detail row can render its canonical URL without
-- a second lookup. NULL means not yet assigned (drafts get theirs at publish)
ALTER TABLE articles_by_id ADD slug text;

-- Feed projection copy, so listing pages can build hrefs without fetching each
-- article's detail row
ALTER TABLE articles_feed ADD slug text;

-- Reverse index: slug -> article. This is what makes resolving a slug an exact
-- single-partition read. Do NOT resolve slugs by scanning articles_by_id.
--
-- Rows here are the record of which slugs are claimed, including by articles
-- that were later deleted -- a tombstoned article's slug stays claimed so it
-- can keep answering 410 Gone rather than being silently handed to a new story
CREATE TABLE IF NOT EXISTS articles_by_slug (
    slug text PRIMARY KEY,
    article_id uuid,
    claimed_at timestamp
);

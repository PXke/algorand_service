-- Admin-only draft status: pull a live article out of public view (feed,
-- sitemap, direct URL) while keeping its stored content intact and
-- reversible. Distinct from delete/tombstone -- a draft is never gone, just
-- temporarily withheld -- restoring it re-inserts the same articles_feed row.
--
-- NULL/absent reads as false everywhere this is checked (existing rows never
-- had a value written, and Cassandra returns NULL for unset columns).
ALTER TABLE articles_by_id ADD draft boolean;

-- Admin can't discover a drafted article by scanning articles_feed -- draft
-- removes it from there by design. This is the "currently drafted" index so
-- the admin UI can list and restore them. Expected size is a handful of rows
-- at a time (a deliberate manual admin action, not a pipeline volume path),
-- so a plain unbounded scan (no bucket/clustering) is the right shape here,
-- unlike articles_feed/publish_queue.
CREATE TABLE IF NOT EXISTS draft_articles (
    article_id uuid PRIMARY KEY,
    title text,
    source_url text,
    drafted_at timestamp
);

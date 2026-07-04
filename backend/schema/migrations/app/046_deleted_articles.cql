-- Tombstones for hard-deleted articles. Search-engine hygiene: a deleted
-- article's URL lives on in old sitemaps and crawler queues, and serving 410
-- Gone (instead of a generic 404) tells Google to drop it fast and stop
-- retrying. Written by the admin delete path, read by the SSR document route.
-- NOTE: no semicolons inside comments -- the migration splitter cuts on them.
CREATE TABLE IF NOT EXISTS deleted_articles (
  article_id uuid PRIMARY KEY,
  deleted_at timestamp,
  title text
);

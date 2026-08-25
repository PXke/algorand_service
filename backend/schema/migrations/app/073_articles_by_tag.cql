USE algorand_platform;

-- Tag index: replaces the "tag_stats scans 500 articles, feed?tag=X over-
-- fetches 4x-cap and filters in Python" pattern (both were a linear scan-
-- and-filter with no index at all) with a real per-tag partition, mirroring
-- the pending_feed_queue/articles_by_slug style of purpose-built reverse
-- index this schema already uses rather than a secondary index on `articles`
-- itself.
--
-- SAI CONTAINS on articles.tags (a list<text>) was evaluated first (this
-- Cassandra 5.0 platform does support it, verified locally against a scratch
-- table) and works for a bare `WHERE tags CONTAINS ?` lookup -- but combining
-- it with even a partial partition-key predicate like `status = ?` demands
-- ALLOW FILTERING (same restriction FIND_BY_SERVICE_ID's own comment already
-- documents for service_id), and Cassandra flatly rejects `ORDER BY
-- published_at` alongside any secondary-index predicate ("ORDER BY with
-- 2ndary indexes is not supported"). That leaves every tag-filtered feed
-- page unordered and unable to paginate by cursor. tag_stats also needs the
-- distinct-tag universe itself (tags are the writer's own free-form labels,
-- confirmed no curated taxonomy table exists anywhere in
-- backend/app/modules/news/) -- SAI has no "list the distinct indexed
-- values" query, so a maintained small table is needed regardless of
-- whether SAI is used for the point-lookup half of this problem. Once that
-- table exists, it trivially and more cheaply covers the feed-page-by-tag
-- case too, with real clustering-ordered pagination as a bonus -- so this is
-- the only index-maintenance mechanism, not two.
--
-- Carries the same deliberately-body-less projection columns as the
-- `articles` feed projection (see 067's LIST_PUBLISHED_PAGE) so a tag page
-- never needs a second per-article lookup to render a feed card. Written
-- and removed alongside every `articles` write that can change status or
-- tags -- see shared/algorand_shared/article_tag_index.py.
CREATE TABLE IF NOT EXISTS articles_by_tag (
  tag text,
  published_at timestamp,
  article_id uuid,
  service_id text,
  title text,
  summary text,
  image_url text,
  source_url text,
  slug text,
  translations map<text, text>,
  first_published_at timestamp,
  updated_at timestamp,
  tags list<text>,
  PRIMARY KEY ((tag), published_at, article_id)
) WITH CLUSTERING ORDER BY (published_at DESC);

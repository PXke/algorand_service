USE algorand_platform;

-- Article data-model consolidation, step 3 of the plan: `articles` collapses
-- articles_by_id + articles_feed + draft_articles + deleted_articles +
-- pending_feed_queue into one canonical table with a real status column,
-- replacing "status inferred from row presence/absence across 5 separate
-- tables" with an actual field. Created alongside the old tables -- nothing
-- reads from these three yet, the data migration and call-site cutover are
-- separate, later steps. See the article-table-consolidation plan for the
-- full design rationale and the precise status-derivation rule used when
-- migrating existing rows.
--
-- status values: draft | on_hold | backlog | published | deleted (app-level
-- enum, not a Cassandra type -- same convention as every other status/kind
-- column in this schema). (status, year) is the partition key so no single
-- partition grows unbounded (mirrors the month-bucket pattern already used
-- for articles_feed/pending_feed_queue) and, since `published` is the status
-- anyone browses chronologically, this table doubles as the feed listing:
-- WHERE status='published' AND year=2026 returns the front page pre-sorted.
-- Every status transition is a DELETE-old-partition-row + INSERT-new-
-- partition-row (status is part of the partition key, so it can't be a plain
-- UPDATE) -- not new complexity, articles_feed already does exactly this
-- today on republish.
CREATE TABLE IF NOT EXISTS articles (
  status text,
  year int,
  published_at timestamp,
  article_id uuid,
  service_id text,
  title text,
  summary text,
  body text,
  image_url text,
  tags list<text>,
  source_url text,
  trigger_txid text,
  trigger_round bigint,
  slug text,
  translations map<text, text>,
  first_published_at timestamp,
  updated_at timestamp,
  burst_day text,
  prompt_version text,
  composed_by_model text,
  deleted_at timestamp,
  PRIMARY KEY ((status, year), published_at, article_id)
) WITH CLUSTERING ORDER BY (published_at DESC);

-- service_id: the field missing all night that forced fragile domain/
-- timestamp-based dedup detection -- direct indexed "every article from this
-- service" query. composed_by_model: denormalized copy of the latest
-- article_history row's model, so "which articles are still Mistral-authored"
-- is a direct indexed query instead of a from-scratch timestamp heuristic.
-- Both are low-QPS admin/debugging access, not reader-facing hot-path.
CREATE CUSTOM INDEX IF NOT EXISTS articles_service_id_sai
  ON articles (service_id) USING 'sai';

CREATE CUSTOM INDEX IF NOT EXISTS articles_composed_by_model_sai
  ON articles (composed_by_model) USING 'sai';

-- Direct by-id resolver, WITHOUT a separate lookup table: `articles` is no
-- longer keyed by article_id alone (status is part of the partition key), so
-- a plain "fetch article X" (likely the single most common read in the
-- system) would otherwise need a resolver table (article_id -> status, year)
-- plus a second round-trip, the same shape as the existing articles_by_slug
-- reverse index. SAI is specifically built to support point queries that
-- don't include the partition key -- unlike legacy secondary indexes, no
-- ALLOW FILTERING or partition key required -- so `SELECT * FROM articles
-- WHERE article_id = ?` works directly against this index in one round-trip,
-- with no second table to keep in sync on every status transition. Benchmark
-- this against a resolver-table design with real data volume before the
-- cutover step (task #126) commits to it as the article-detail read path.
CREATE CUSTOM INDEX IF NOT EXISTS articles_article_id_sai
  ON articles (article_id) USING 'sai';

-- Replaces article_versions: full content snapshot per edit/recompose, kept
-- as a genuinely separate table (not embedded on `articles`) so scanning/
-- filtering articles never drags N versions along -- the real access pattern
-- is "history for this ONE article", not "all versions across all articles".
-- session_id + session_created_at are both needed together: compose_sessions'
-- own PRIMARY KEY is ((bucket), created_at, session_id), so session_id alone
-- can't locate a transcript row. Must be captured at compose time --
-- compose_sessions currently has a 7-day TTL (removal is a later step in this
-- same plan), so anything not copied here when it happens is unrecoverable
-- after a week.
CREATE TABLE IF NOT EXISTS article_history (
  article_id uuid,
  version int,
  title text,
  summary text,
  body text,
  edit_reason text,
  editor text,
  edited_at timestamp,
  model text,
  image_url text,
  session_id timeuuid,
  session_created_at timestamp,
  PRIMARY KEY (article_id, version)
) WITH CLUSTERING ORDER BY (version DESC);

USE algorand_platform;

-- Article-table consolidation, final step: drops the 5 legacy tables the
-- consolidated `articles` table (migration 067) replaced. Every read and
-- write against these has been migrated onto `articles` across 5 phases
-- tonight (Phases 1-5) -- confirmed via a full repo grep immediately before
-- writing this migration: zero remaining production code references any of
-- these table names. A handful of already-executed, kept-for-reference
-- one-off scripts in workers/scratch/ still mention them textually -- those
-- are historical artifacts, not expected to run again, and are not blocked
-- by this drop.
--
-- A full JSON backup of every row in all 5 tables was already taken before
-- the original data migration began (see the article-table-consolidation
-- plan's own step 1) -- this drop does not need a fresh backup.
DROP TABLE IF EXISTS articles_by_id;
DROP TABLE IF EXISTS articles_feed;
DROP TABLE IF EXISTS draft_articles;
DROP TABLE IF EXISTS deleted_articles;
DROP TABLE IF EXISTS pending_feed_queue;

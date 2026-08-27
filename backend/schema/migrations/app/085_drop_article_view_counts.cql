USE algorand_platform;

-- article_view_counts (migration 026, COUNTER table) is fully superseded by
-- articles.views (migration 084, plain int column). Cutover code deployed
-- and live-verified 2026-08-27: flush_pending_views/get_views/get_views_bulk
-- everywhere (both services, incl. the news_service._fetch_detail call site)
-- now read/write articles.views exclusively. workers/scratch/
-- backfill_article_views.py has already run against prod and copied every
-- nonzero counter value over -- spot-checked against the counter table's own
-- rows, exact match. Safe to drop.
DROP TABLE IF EXISTS article_view_counts;

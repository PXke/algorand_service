USE algorand_platform;

-- Fold the per-article view tally into `articles` as a plain int column,
-- replacing the separate article_view_counts COUNTER table (migration 026).
-- Counter columns can never coexist with regular columns in the same table
-- (hard Cassandra constraint), but this tally no longer needs a true atomic
-- increment: since 2026-08-25 the hot page-view path only INCRs a Redis
-- pending key, and the sole writer is the 10-minute flush_pending_views
-- beat -- a single periodic writer that can afford read-current-total,
-- add-delta, write-back (the same read-current-key-then-patch shape as
-- update_article_image, keyed off a fresh GET_BY_ID read so the patch
-- always lands on the row's CURRENT partition even after a recompose
-- re-stamps published_at). NULL reads as 0 (Cassandra has no column
-- defaults) -- both services' get_views/get_views_bulk already treat a
-- missing tally as 0. Existing counter values are copied over by the
-- one-off workers/scratch/backfill_article_views.py (additive, run once,
-- after this column and the cutover code are live) -- a raw CQL file
-- cannot join two tables with different key shapes. article_view_counts
-- itself is dropped in a separate later migration once the backfill is
-- verified, so the cutover deploy stays rollback-safe.
ALTER TABLE articles ADD views int;

USE algorand_platform;

-- Drop article_history (migration 067). It was created as the version-trail
-- half of the article-table consolidation, meant to replace article_versions,
-- but that cutover step never landed -- zero live code reads or writes it
-- (verified via fresh grep of backend, workers, shared and both test suites,
-- only comments and the one-off scratch backfill script mention it) and the
-- table is already 4 rows behind the still-live article_versions (672 vs 676
-- on prod, checked before this migration was written). The richer columns it
-- was built for (compose model, session_id) are not captured anywhere in the
-- current write path, so finishing the cutover is not feasible either -- the
-- owner approved dropping it (schema audit 2026-08-26/27). article_versions
-- stays the live version trail (ARTICLE_VERSION_INSERT in shared
-- article_statements.py).
-- NOTE: no semicolons in comments (the migrator splits on the first one).

DROP TABLE IF EXISTS algorand_platform.article_history;

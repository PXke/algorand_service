USE algorand_platform;

-- Article-table consolidation, Phase 4: pending_feed_queue is the one old
-- table whose role isn't just a presence index -- its clustering key
-- (interest_score DESC, approved_at ASC) is real ordering data `articles`
-- has no equivalent for. These two columns let a backlog row (status =
-- 'backlog') carry that ordering directly, so the paced-release worker can
-- query articles instead of the separate table. Nullable -- only ever set
-- on rows that pass through 'backlog', same convention as deleted_at/
-- status_updated_at.
ALTER TABLE articles ADD interest_score double;
ALTER TABLE articles ADD approved_at timestamp;

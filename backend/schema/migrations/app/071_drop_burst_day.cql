USE algorand_platform;

-- burst-compose removed 2026-08-25: never ran in production (gated behind
-- DAILY_BURST_ENABLED, unset on prod -- zero rows anywhere ever had
-- burst_day set) and its dependent admin "picks" UI was never built.
-- Drops the column from all three tables it was added to.
ALTER TABLE publish_queue DROP burst_day;
ALTER TABLE articles_by_id DROP burst_day;
ALTER TABLE articles DROP burst_day;

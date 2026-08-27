USE algorand_platform;

-- Switch the crawl frontier's two history tables from STCS to
-- TimeWindowCompactionStrategy, paired with the new go-forward row TTL
-- (URL_QUEUE_ROW_TTL_SECONDS, USING TTL ? on every url_queue write -- ships
-- disabled/0). Terminal done/failed rows have zero functional role (the
-- enqueue dedupe only matches status='pending' rows and the recrawl cooldown
-- lives in Redis, confirmed in the 2026-08-27 schema audit), yet ~145k of
-- them had accumulated because nothing ever deleted them. Once the TTL is
-- flipped on, TWCS drops whole expired SSTables per 1-day window instead of
-- leaving STCS to accumulate scannable tombstones. With the TTL still 0 the
-- strategy change is inert but harmless -- writes just land in daily windows.
-- url_queue's post-insert status UPDATE lands seconds-to-minutes after the
-- insert (same or adjacent window), well within TWCS's tolerance.
-- url_queue_pending deliberately stays on STCS: it is a delete-per-dequeue
-- status index (effectively 0 rows at any time) whose constant out-of-order
-- tombstones are exactly the write pattern TWCS handles poorly -- its
-- existing gc_grace_seconds=3600 (migration 033) already reaps it fine.
-- gc_grace_seconds=3600 on all three tables is unchanged.
-- NOTE: no semicolons in comments (the migrator splits on the first one).

ALTER TABLE url_queue WITH compaction = {'class': 'TimeWindowCompactionStrategy', 'compaction_window_unit': 'DAYS', 'compaction_window_size': 1};

ALTER TABLE url_queue_by_url WITH compaction = {'class': 'TimeWindowCompactionStrategy', 'compaction_window_unit': 'DAYS', 'compaction_window_size': 1};

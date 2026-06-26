USE algorand_platform;

-- url_queue_pending partitions by status, so every dequeue/done DELETE drops a
-- tombstone into the single 'pending' partition. With the default 10-day
-- gc_grace_seconds those tombstones linger and the LIMIT scans read through
-- thousands of them (tombstone_warn_threshold warnings, slow reads). The cluster
-- runs RF=1 (SimpleStrategy), so a short gc_grace is safe -- there are no
-- replicas to reconcile, so dropped tombstones cannot resurrect data. 1 hour.
-- NOTE: no semicolons in comments (the migrator splits on the first one).

ALTER TABLE url_queue_pending WITH gc_grace_seconds = 3600;

ALTER TABLE url_queue WITH gc_grace_seconds = 3600;

ALTER TABLE url_queue_by_url WITH gc_grace_seconds = 3600;

USE algorand_platform;

-- Retires the publish_queue 3-lane selection system (migration 010),
-- replaced by artifacts/to_compose (migration 077, cut over 2026-08-25).
-- publish_queue was kept dual-write for one deploy cycle as a rollback
-- safety net (see ingest_signal.py/editorial_assignment.py at that commit);
-- owner call one day later ("we don't need them in a closed feature") to
-- drop it now. Priority values between the two systems are not comparable
-- and do not need reconciling -- artifacts is the live system going
-- forward. The dual-write itself (enqueue_publish calls, dual_write_queue_id
-- metadata, _resolve_dual_written_queue_row) was removed from the compose
-- pipeline in the same change that adds this migration -- confirmed via a
-- full repo grep immediately before writing it: zero remaining production
-- code references any of the three tables below.
--
-- publish_queue_dedupe is dropped alongside the other two: it exists solely
-- to backstop enqueue_publish's per-content-hash dedupe, which has no
-- purpose once nothing calls enqueue_publish.
DROP TABLE IF EXISTS algorand_platform.publish_queue;

DROP TABLE IF EXISTS algorand_platform.publish_queue_pending;

DROP TABLE IF EXISTS algorand_platform.publish_queue_dedupe;

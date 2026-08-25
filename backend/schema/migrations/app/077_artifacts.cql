USE algorand_platform;

-- Editorial-room artifacts: the LIVE replacement for the publish_queue
-- selection system (cut over 2026-08-25, same day this schema landed --
-- see queue_drain_tasks.py's drain_to_compose + select_to_compose_for_today_task,
-- and artifact_priority.py's compute_artifact_priority). publish_queue /
-- publish_queue_pending stay live-fed (a deliberate rollback-safety
-- dual-write) but are no longer read for selection or compose triggering.
--
-- `artifacts` is any raw candidate that could become an article -- a crawled
-- page diff, a YouTube video, a mail message, a brief, a tweet. It mirrors
-- publish_queue's own thin-table-plus-pending-index shape (see publish_queue
-- / publish_queue_pending, migration 010): the frequently scanned/sorted
-- table stays thin, raw content lives in a separate artifact_content table
-- (mirrors the articles / article_history split, migration 067), fetched
-- only at read/compose time.
--
-- status: pending / selected / composed / discarded
-- channel: crawler / youtube / mail / twitter / brief / special_edition
-- event_date: the date extracted FROM the content itself when findable (how
--   fresh the underlying information actually is), distinct from created_at
--   (when we discovered it) -- falls back to created_at when nothing is
--   extractable.
-- human_pick_day: mirrors publish_queue.human_pick_day (migration 060) --
--   the compose day (YYYY-MM-DD) an admin pinned this artifact for, cleared
--   once that day's selection consumes it.
CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id uuid PRIMARY KEY,
  service_id text,
  url text,
  channel text,
  created_at timestamp,
  event_date timestamp,
  priority double,
  priority_computed_at timestamp,
  status text,
  human_pick_day text
);

-- Pending-only index, mirrors publish_queue_pending exactly: partitioned by
-- status so the priority sweep and the day-ahead selection scan only ever
-- read the (bounded) pending set, never the full artifacts table. priority
-- and created_at sit in the clustering key so a re-scored artifact needs
-- delete-old + insert-new here rather than an UPDATE -- same shape as
-- publish_queue_store.mark_queue_status's own delete-then-reinsert.
-- human_pick_day is denormalized here too (not part of the primary key, so
-- pin/unpin is a plain UPDATE against this table -- no delete+reinsert)
-- so the day-ahead selector can spot the pinned artifact directly off this
-- one pending-partition scan instead of a full-row fetch per candidate.
CREATE TABLE IF NOT EXISTS artifacts_pending (
  status text,
  priority double,
  created_at timestamp,
  artifact_id uuid,
  service_id text,
  channel text,
  url text,
  event_date timestamp,
  human_pick_day text,
  PRIMARY KEY ((status), priority, created_at, artifact_id)
) WITH CLUSTERING ORDER BY (priority DESC, created_at ASC);

-- Raw content, one row per artifact, joined only at read/compose time (a
-- single cheap point-query) -- keeps artifacts/artifacts_pending thin for
-- the frequent priority-sweep scan. `metadata` is a channel-specific JSON
-- blob (e.g. tweet author, mail subject/from).
CREATE TABLE IF NOT EXISTS artifact_content (
  artifact_id uuid PRIMARY KEY,
  title text,
  content text,
  metadata text
);

-- Day-ahead compose selection: one human-picked slot (lane = 'human', always
-- slot 0) plus N-1 platform-picked slots (lane = 'platform'), N = the
-- existing NEWS_MAX_ARTICLES_PER_DAY cap. If no human pick was made by the
-- selection cutoff, slot 0 is simply never inserted for that day -- an
-- explicit owner decision, no platform backfill of an unused human slot.
-- service_id is denormalized from artifacts for the platform-fill's
-- 1-per-service dedup check without a round trip back to `artifacts`.
CREATE TABLE IF NOT EXISTS to_compose (
  compose_day text,
  slot int,
  artifact_id uuid,
  lane text,
  service_id text,
  picked_at timestamp,
  PRIMARY KEY ((compose_day), slot)
) WITH CLUSTERING ORDER BY (slot ASC);

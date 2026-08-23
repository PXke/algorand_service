USE algorand_platform;

-- Per-language translation lifecycle (start/finish), analogous to
-- compose_sessions but for translate_article_batch_task: written 'running'
-- when a language's translation begins and updated to 'ok'/'error' when it
-- finishes, so a row stuck 'running' past the staleness window can be
-- reaped and surfaced instead of silently looking in-progress forever (see
-- reap_stale_translation_sessions). Single 'all' partition (admin lists
-- newest-first), same 7-day TTL as compose_sessions.

CREATE TABLE IF NOT EXISTS translation_sessions (
  bucket text,
  started_at timestamp,
  session_id timeuuid,
  article_id text,
  lang text,
  status text,
  duration_ms int,
  error text,
  PRIMARY KEY ((bucket), started_at, session_id)
) WITH CLUSTERING ORDER BY (started_at DESC, session_id DESC)
  AND default_time_to_live = 604800;

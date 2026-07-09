USE algorand_platform;

-- Writer pipeline feedback (via report_compose_issue): prompt confusion, bad source
-- data, tool misbehavior, pipeline friction — reviewed in admin Tool insights.
-- Single 'all' partition, 90-day TTL (same cadence as tool_suggestions).

CREATE TABLE IF NOT EXISTS compose_feedback (
  bucket text,
  created_at timestamp,
  feedback_id timeuuid,
  category text,
  severity text,
  summary text,
  detail text,
  related_tool text,
  service_id text,
  source_url text,
  model text,
  PRIMARY KEY ((bucket), created_at, feedback_id)
) WITH CLUSTERING ORDER BY (created_at DESC, feedback_id DESC)
  AND default_time_to_live = 7776000;

USE algorand_platform;

-- Full agentic transcript of one article compose: the system + user prompts,
-- every assistant turn, every tool call + result, and the final output. Lets the
-- admin read exactly what the writer model did and why (debug/analysis). Bulky,
-- so single 'all' partition (admin lists newest-first) with a 7-day default TTL.

CREATE TABLE IF NOT EXISTS compose_sessions (
  bucket text,
  created_at timestamp,
  session_id timeuuid,
  service_id text,
  source_url text,
  model text,
  status text,
  rounds int,
  tool_calls int,
  duration_ms int,
  messages text,
  final_output text,
  PRIMARY KEY ((bucket), created_at, session_id)
) WITH CLUSTERING ORDER BY (created_at DESC, session_id DESC)
  AND default_time_to_live = 604800;

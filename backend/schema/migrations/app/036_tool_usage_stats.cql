USE algorand_platform;

-- Persistent writer tool-usage counters, recorded at the end of every compose
-- from the agentic trace (see record_tool_usage_from_trace). compose_sessions
-- has a 7-day TTL, so this is the only durable record of which tools the writer
-- actually relies on and which keep erroring -- the signal for adding/retiring
-- tools over time. Day-bucketed so the admin can read a partition and chart
-- trends. Counter tables hold only the PK plus counter columns.
-- NOTE: no semicolons in comments (the migrator splits on the first one).

CREATE TABLE IF NOT EXISTS tool_usage_stats (
  day text,
  tool text,
  calls counter,
  errors counter,
  PRIMARY KEY (day, tool)
);

USE algorand_platform;

-- Writer introspection (Cassandra only, no extra infra): capabilities the model
-- wished it had, recorded via the suggest_tool tool and reviewed in the admin
-- "Tool insights" tab so we can add tools over time. Single 'all' partition (low
-- volume, admin lists newest-first) with a 90-day default TTL so it self-prunes.
-- (Tool *errors* go to Bugsnag, not Cassandra.)

CREATE TABLE IF NOT EXISTS tool_suggestions (
  bucket text,
  created_at timestamp,
  suggestion_id timeuuid,
  capability text,
  reason text,
  service_id text,
  source_url text,
  model text,
  resolved boolean,
  PRIMARY KEY ((bucket), created_at, suggestion_id)
) WITH CLUSTERING ORDER BY (created_at DESC, suggestion_id DESC)
  AND default_time_to_live = 7776000;

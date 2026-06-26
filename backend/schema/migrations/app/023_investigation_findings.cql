USE algorand_platform;

-- Evidence trail from the investigative agent: every tool call made while
-- composing an article, stored for the audit/review trail (Cassandra only,
-- no graph DB). Searchable copy goes to Typesense separately.
CREATE TABLE IF NOT EXISTS investigation_findings (
  service_id text,
  created_at timestamp,
  finding_id timeuuid,
  source_url text,
  tool text,
  arguments text,
  result_json text,
  PRIMARY KEY ((service_id), created_at, finding_id)
) WITH CLUSTERING ORDER BY (created_at DESC, finding_id DESC);

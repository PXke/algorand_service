USE algorand_platform;

-- Cached enrichment for the writer (domain probe, diffs, chain stats, app store hints).
CREATE TABLE IF NOT EXISTS service_intelligence (
  service_id text PRIMARY KEY,
  primary_domain text,
  intelligence_json text,
  first_seen_at timestamp,
  updated_at timestamp
);

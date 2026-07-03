-- Service layer: one service (a product / organisation) aggregates MULTIPLE
-- sources. Web domains that belong to the same entity (algorand.co +
-- algorand.com + forum.algorand.co) must feed ONE weekly service-watch
-- aggregate instead of composing near-duplicate articles per domain, and
-- non-web lanes (youtube / mail, later bluesky) attach to the same service.
-- service_registry stays the service list -- these tables map sources onto it.

CREATE TABLE IF NOT EXISTS service_sources (
  service_id text,
  source_id text,
  source_type text,
  url text,
  domain text,
  enabled boolean,
  added_at timestamp,
  PRIMARY KEY ((service_id), source_id)
);

-- Reverse lookup: which service owns a registrable domain (eTLD+1). Written
-- together with service_sources for web sources -- discovery and enqueue paths
-- resolve a URL to its owning service through this table.
CREATE TABLE IF NOT EXISTS service_by_domain (
  domain text PRIMARY KEY,
  service_id text
);

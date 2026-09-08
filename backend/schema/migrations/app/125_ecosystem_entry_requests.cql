USE algorand_platform;

-- "Suggest a change" (roadmap item 26 follow-up, owner ask 2026-09-08:
-- "Yes we need a suggest a change"). A free, anonymous request against an
-- ALREADY-APPROVED registry entry -- distinct from ecosystem_projects
-- (a fresh submission): no domain dedupe, no liveness check, no category,
-- just a human-reviewed note the admin acts on through the ordinary edit/
-- delete path. See backend/app/modules/ecosystem/models/domain.py's
-- EntryRequest and shared/algorand_shared/ecosystem_statements.py.

-- Canonical row. kind is one of change/removal, status is one of
-- pending/resolved/dismissed (app.modules.ecosystem.models.domain).
CREATE TABLE IF NOT EXISTS ecosystem_entry_requests (
  request_id text PRIMARY KEY,
  slug text,
  kind text,
  message text,
  contact text,
  status text,
  created_at timestamp,
  resolved_at timestamp,
  resolved_by text
);

-- Admin queue read (design doc section 3.3's decision-queue shape, reused
-- for this smaller flow) -- a row moves partition on every decision:
-- deleted at the old status's clustering key, inserted fresh at the new
-- one, same "store before mark" full-INSERT convention as
-- ecosystem_submissions_by_status.
CREATE TABLE IF NOT EXISTS ecosystem_entry_requests_by_status (
  status text,
  created_at timestamp,
  request_id text,
  slug text,
  kind text,
  PRIMARY KEY ((status), created_at, request_id)
) WITH CLUSTERING ORDER BY (created_at DESC, request_id ASC);

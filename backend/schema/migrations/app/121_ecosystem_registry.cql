USE algorand_platform;

-- Algorand Open Registry (roadmap item 26, CLAUDE.md section 9.1 -- design
-- pass docs/awesome-algorand-directory-design.md, decisions confirmed
-- 2026-09-07). A NEW, separate concern from the paid x402 directory --
-- free, human-reviewed, anonymous-submission ecosystem listing, glossary-
-- shaped per the design doc's section 6.2. See
-- backend/app/modules/ecosystem/ and shared/algorand_shared/
-- ecosystem_statements.py.

-- Canonical row. status is one of pending/approved/rejected
-- (app.modules.ecosystem.models.domain). source is one of
-- seeded/submitted/claimed (seeded rows come from the crawler's existing
-- domain_tracking/service_registry ecosystem_listed set -- see
-- workers/app/modules/crawler/ecosystem_sync.py -- and get a "trusted
-- source" fast lane in the admin queue). editor_pick is the free v1
-- featured flag (design doc section 5: a paid tier is explicitly deferred).
-- last_probed_at/reachable/last_http_status are the liveness fields,
-- written once at submit time and refreshed by the workers periodic
-- re-check beat (app/modules/ecosystem_probe/) -- deliberately only on
-- this canonical row, never denormalized into the by-category/by-tag
-- projections below, so a liveness refresh never needs a projection
-- rewrite. draft_description is the admin-only, never-auto-published
-- DeepSeek blurb suggestion (design doc section 6.3) -- only a human
-- copying it into `description` through the ordinary edit/approve path
-- ever changes what is actually served.
CREATE TABLE IF NOT EXISTS ecosystem_projects (
  slug text PRIMARY KEY,
  name text,
  domain text,
  url text,
  repo_url text,
  x402_url text,
  description text,
  category text,
  tags set<text>,
  stage text,
  open_source boolean,
  source text,
  status text,
  editor_pick boolean,
  contact text,
  service_id text,
  draft_description text,
  submitted_at timestamp,
  reviewed_at timestamp,
  reviewed_by text,
  reject_reason text,
  last_probed_at timestamp,
  reachable boolean,
  last_http_status int
);

-- LWT one-entry-per-registrable-domain dedupe gate (design doc section
-- 3.2), the same INSERT ... IF NOT EXISTS shape as backend's own
-- X402DirectoryStmts.INSERT_LISTING_IF_ABSENT (migration 090), keyed on
-- domain (the real submission-uniqueness key here) rather than a url hash.
-- `status` is denormalized here purely so a duplicate submission's error
-- message can say "already listed" vs "already pending" without a second
-- point read.
CREATE TABLE IF NOT EXISTS ecosystem_projects_by_domain (
  domain text PRIMARY KEY,
  slug text,
  status text
);

-- Category browse feed, written only on approve, deleted on
-- reject/delete/edit-then-rewrite. Every approved entry also gets a second
-- row under the reserved "all" pseudo-category (mirrors x402_directory's
-- DIRECTORY_PARTITION convention, EcosystemStmts.ALL_CATEGORY_PARTITION) so
-- the unfiltered index is the same single-partition bounded read as a real
-- category, never ALLOW FILTERING (CLAUDE.md section 4).
CREATE TABLE IF NOT EXISTS ecosystem_projects_by_category (
  category text,
  approved_at timestamp,
  slug text,
  name text,
  domain text,
  url text,
  description text,
  tags set<text>,
  stage text,
  open_source boolean,
  editor_pick boolean,
  x402_url text,
  PRIMARY KEY ((category), approved_at, slug)
) WITH CLUSTERING ORDER BY (approved_at DESC, slug ASC);

-- Free-tag browse feed (design doc section 4: one closed category plus
-- free multi-tags, not single-tag) -- same shape as the category feed, one
-- row per tag up to settings.ecosystem_max_tags.
CREATE TABLE IF NOT EXISTS ecosystem_projects_by_tag (
  tag text,
  approved_at timestamp,
  slug text,
  name text,
  domain text,
  url text,
  description text,
  category text,
  stage text,
  open_source boolean,
  editor_pick boolean,
  x402_url text,
  PRIMARY KEY ((tag), approved_at, slug)
) WITH CLUSTERING ORDER BY (approved_at DESC, slug ASC);

-- Admin review queue read (design doc section 3.3). A row moves partition
-- on every decision: deleted at the old status's clustering key, inserted
-- fresh at the new one -- same "store before mark" full-INSERT convention
-- as every other projection in this codebase (CLAUDE.md section 3:
-- articles_feed's partial-UPDATE phantom-row lesson).
CREATE TABLE IF NOT EXISTS ecosystem_submissions_by_status (
  status text,
  submitted_at timestamp,
  slug text,
  name text,
  domain text,
  url text,
  category text,
  source text,
  PRIMARY KEY ((status), submitted_at, slug)
) WITH CLUSTERING ORDER BY (submitted_at DESC, slug ASC);

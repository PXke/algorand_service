USE algorand_platform;

-- Owner-attached evidence for a specific article (exclusive interviews,
-- documents, extra data the public web cannot provide -- see
-- docs/newspaper-article-sources-design.md, prompted by the sproutalgo.com
-- held-article case 2026-09-02). Canonical store -- projected into
-- investigation_findings as `admin_supplied_source` trace entries at each
-- recompose (workers/app/modules/ai/llm_compose.py's compose_scrape_article,
-- admin_sources= param), never written there directly -- see the design
-- doc's section 2 for why investigation_findings itself was rejected as the
-- canonical home (no update/delete path, wrong key, ages out of the LIST
-- window, per-row size cap too small for a transcript).
CREATE TABLE IF NOT EXISTS article_admin_sources (
  article_id uuid,          -- the live/held article this evidence belongs to
  source_id timeuuid,       -- attach time, newest first
  added_by text,            -- admin wallet (audit -- the VERIFIED wallet, never a self-asserted header -- see backend/app/modules/admin/api/routes.py)
  label text,               -- provenance the writer is told, e.g. "Exclusive interview with X, 2026-09-02"
  kind text,                -- 'text' (v1) -- 'url' reserved for Phase 2 (fetched at recompose time, never by backend -- see design doc section 1)
  attribution_url text,     -- optional -- provenance metadata in v1, fetch target in Phase 2
  content text,             -- the pasted material (bounded by ADMIN_SOURCE_MAX_CHARS)
  status text,              -- 'active' | 'removed'
  added_at timestamp,
  removed_at timestamp,
  PRIMARY KEY ((article_id), source_id)
) WITH CLUSTERING ORDER BY (source_id DESC);

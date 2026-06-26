USE algorand_platform;

-- Gatekeeper validation anchors: the immutable, human-tagged ground-truth set
-- used to validate the LLM annotator (never used for training). Single source of
-- truth, written both by the grade screen anchor toggle and by tagging a
-- published article. source_text + article_text are snapshotted at tag time so
-- an anchor stays fixed even if the article is later edited.
-- NOTE: no semicolons in comments (the migrator splits on the first one).

CREATE TABLE IF NOT EXISTS gatekeeper_anchors (
  bucket text,                 -- always 'main' (low volume, ~40 rows)
  created_at timestamp,
  anchor_id timeuuid,
  article_id text,
  url text,
  source_text text,
  article_text text,
  factuality_fail boolean,
  tone_fail boolean,
  error_types list<text>,
  admin_wallet text,
  PRIMARY KEY ((bucket), created_at, anchor_id)
) WITH CLUSTERING ORDER BY (created_at DESC, anchor_id DESC);

-- Latest annotator-validation report (computed by the gatekeeper validation
-- task). Newest-first, read LIMIT 1. report_json holds the full
-- ValidationReport.summary() so the admin UI renders without a schema coupling.

CREATE TABLE IF NOT EXISTS gatekeeper_validation_report (
  bucket text,                 -- always 'main'
  computed_at timestamp,
  report_json text,
  n_anchors int,
  trusted_count int,
  PRIMARY KEY ((bucket), computed_at)
) WITH CLUSTERING ORDER BY (computed_at DESC);

USE algorand_platform;

-- MTTH gatekeeper telemetry, strictly bifurcated to defeat the survivorship-bias
-- trap (a model that updates its own priors from its own filtered output and
-- suffocates the pipeline).
--
-- Stream A (gatekeeper_decisions): WRITE-ONLY operating-point log. One row per
-- gate decision. Monitors threshold efficacy/latency. NEVER feeds a prior. Logs
-- raw logits AND the c-shift applied, so past decisions can be replayed when c
-- changes. Day-bucketed, newest-first, 90-day TTL.

CREATE TABLE IF NOT EXISTS gatekeeper_decisions (
  day text,                       -- 'YYYY-MM-DD' partition
  decided_at timestamp,
  draft_id text,
  model_version text,
  profile_version text,
  raw_factuality double,
  raw_tone double,
  c_factuality double,            -- log-odds shift in force at decision time
  c_tone double,
  prob_factuality double,         -- sigmoid(raw + c)
  prob_tone double,
  threshold_factuality double,
  threshold_tone double,
  completeness_pass boolean,
  decision text,                  -- ROUTE | DROP_FACTUALITY | DROP_TONE | RETRY_COMPLETENESS
  retry_count int,
  PRIMARY KEY ((day), decided_at, draft_id)
) WITH CLUSTERING ORDER BY (decided_at DESC, draft_id ASC)
  AND default_time_to_live = 7776000;

-- Stream B (gatekeeper_audit): the SOURCE OF TRUTH. A sample of ALL drafts,
-- regardless of gate decision. The only stream allowed to update base_fail_rate,
-- error composition, and calibration. selected_by is the firewall key:
--   'uniform'    -> may touch base_fail_rate (unbiased)
--   'stratified' -> composition/hard-example only, BARRED from base_fail_rate
-- selection_prob carries the IPW weight for any pooled estimate. Longer TTL so a
-- full rolling window of ~1000 uniform audits is always available.

CREATE TABLE IF NOT EXISTS gatekeeper_audit (
  day text,
  audited_at timestamp,
  draft_id text,
  model_version text,
  profile_version text,
  selected_by text,               -- 'uniform' | 'stratified'  (firewall key)
  selection_prob double,          -- for inverse-probability weighting
  label_factuality double,        -- annotator soft label in [0,1]
  label_tone double,
  error_types list<text>,         -- taxonomy tags
  unclassified boolean,           -- annotator could not map to taxonomy (novel-mode canary)
  annotator_confidence double,
  human_verified boolean,
  human_label double,             -- nullable, populated on the spot-check subset
  prob_factuality double,         -- copied from Stream A, for calibration error
  prob_tone double,
  PRIMARY KEY ((day), audited_at, draft_id)
) WITH CLUSTERING ORDER BY (audited_at DESC, draft_id ASC)
  AND default_time_to_live = 15552000;

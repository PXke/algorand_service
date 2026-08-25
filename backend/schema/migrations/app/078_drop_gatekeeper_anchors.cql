USE algorand_platform;

-- Drop the LLM-annotator validation harness tables (migration 032). The
-- harness compared a Tier-2 Mistral classifier against human-tagged anchor
-- articles to decide which factuality/tone error types it could be trusted
-- to auto-label. It had zero downstream consumers -- trusted_types was never
-- read by anything -- and the anchor pool was never populated (0/40 tagged).
-- Owner call 2026-08-25: retired in favor of the existing article_grader.py
-- LLM rubric, which is trusted as-is. The deterministic pre-publish gate
-- (gatekeeper/live.py, gate_draft) and the article-grader support modules
-- (fact_align.py, completeness.py, structure.py) are untouched by this drop.
-- NOTE: no semicolons in comments (the migrator splits on the first one).

DROP TABLE IF EXISTS algorand_platform.gatekeeper_anchors;

DROP TABLE IF EXISTS algorand_platform.gatekeeper_validation_report;

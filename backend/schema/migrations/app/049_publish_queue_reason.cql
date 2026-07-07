USE algorand_platform;

-- Last drain/compose decision for this row (gate name or outcome reason,
-- e.g. domain_cooldown, novelty_collapsed, not_credible:no_evidence,
-- poor_quality_content, published). Written by the workers' drain and
-- compose paths so an admin can see WHY a row was skipped, held, or
-- resolved -- previously these reasons only existed in fire-and-forget
-- Celery task return values and vanished
ALTER TABLE publish_queue ADD last_reason text;

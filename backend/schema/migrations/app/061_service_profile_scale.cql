-- Real-world project-scale signal (DeFiLlama TVL or GitHub org stars,
-- whichever resolves), feeding compute_priority's scale_pts term and Lane 2
-- ("biggest/most significant") of the publish-queue lane split. Distinct
-- from impressiveness_score, which only measures page-content shape
-- (length, headings, doc-links), not actual scale.
--
-- NULL reads as "never scored" for old rows and any service the resolver
-- has not yet reached -- compute_priority treats a NULL/absent signal as
-- the neutral unresolved floor, never as "resolved and tiny".
ALTER TABLE service_profiles ADD scale_score float;
ALTER TABLE service_profiles ADD scale_source text;
ALTER TABLE service_profiles ADD scale_updated_at timestamp;

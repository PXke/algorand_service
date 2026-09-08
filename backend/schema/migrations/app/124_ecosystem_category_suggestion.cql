-- Free-text, submitter-suggested category name for the Algorand Open
-- Registry (roadmap item 26 follow-up, owner ask 2026-09-08: "allow people
-- to suggest new categories"). Admin-review-only -- never validated against
-- the closed CATEGORIES enum, never served on a public read
-- (_public_project_json omits it -- see app/modules/ecosystem/api/routes.py).
-- Purely additive: existing rows read back as empty string via
-- StoredProject.category_suggestion's default, same "ADD a nullable column,
-- backend supplies the default" shape as prior additive migrations in this
-- table (draft_description, service_id).
ALTER TABLE algorand_platform.ecosystem_projects ADD category_suggestion text;

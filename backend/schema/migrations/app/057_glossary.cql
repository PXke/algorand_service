-- Glossary: admin-curated term/definition pairs, linked to from article
-- bodies (deterministic post-compose auto-link, never model-authored) and
-- served as their own public /glossary and /glossary/<slug> pages.
--
-- Entries are hand-written or AI-drafted-then-edited by an admin -- nothing
-- here is ever written directly by the compose pipeline. `status` gates
-- what's public: only 'published' rows are listed or linked to, while
-- 'draft' lets an admin save a term before it's ready.

-- translations: same shape as articles_by_id.translations (lang -> JSON
-- {"term": ..., "definition": ..., "aliases": [...]}), filled by the same
-- local translation engine/backfill task articles already use.
CREATE TABLE IF NOT EXISTS glossary_terms (
    slug text PRIMARY KEY,
    term text,
    definition text,
    aliases list<text>,
    status text,
    created_at timestamp,
    updated_at timestamp,
    created_by text,
    translations map<text, text>
);

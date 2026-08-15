-- Opaque bearer token -> article_id, letting an admin share exactly one
-- (usually still-draft) article with a non-admin reviewer via an
-- unguessable link, without any wallet/session login. The token itself is
-- the entire authorization -- see NewsService.get_article_ignoring_draft_gate,
-- callable only after a token resolves here.
--
-- Soft-delete (revoked flag), deliberately NOT hard-delete like
-- glossary_terms/draft_articles: a revoked token must still resolve so the
-- public route can answer a distinct "this link was revoked" (403) instead
-- of an indistinguishable 404, and admin needs a persistent share history
-- for a draft that went out to an external reviewer.
-- Column is `share_token`, not `token` -- `token` is a reserved CQL keyword
-- (the token() partitioner function), and CREATE TABLE rejects it outright
-- as a bare column name even quoted-inconsistently across tooling.
-- Renaming sidesteps needing to quote it at every call site.
CREATE TABLE IF NOT EXISTS share_links (
    share_token text PRIMARY KEY,
    article_id uuid,
    label text,
    created_at timestamp,
    created_by text,
    revoked boolean,
    revoked_at timestamp
);

-- Reverse index for the admin "links for this article" list -- same
-- denormalized _by_ shape as articles_by_slug/service_by_domain, kept in
-- sync by a dual write in sharing/store.py (create_link/revoke_link),
-- non-transactional, same pattern as set_article_draft's own dual write
-- into articles_by_id + draft_articles.
CREATE TABLE IF NOT EXISTS share_links_by_article (
    article_id uuid,
    created_at timestamp,
    share_token text,
    label text,
    created_by text,
    revoked boolean,
    revoked_at timestamp,
    PRIMARY KEY ((article_id), created_at, share_token)
) WITH CLUSTERING ORDER BY (created_at DESC);

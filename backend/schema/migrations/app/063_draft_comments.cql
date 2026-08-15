-- Shared-thread comments on a (usually still-draft) article, left by anyone
-- holding a valid share link for it -- everyone with a link sees ALL
-- comments (product decision), so there is no per-token/per-author
-- partitioning here. Small per-article volume expected (a handful of
-- reviewers on one draft, not high-frequency writes), so this is a plain
-- per-article partition with no bucketing, same shape as
-- glossary_terms/draft_articles.
--
-- anchor_quote/prefix/suffix implement a W3C TextQuoteSelector-style anchor
-- (the exact highlighted text plus a short span of surrounding context),
-- resolved CLIENT-SIDE against the freshly rendered Markdown DOM at read
-- time (frontend/src/lib/textHighlight.ts) -- never raw character offsets
-- into the markdown source, since marked's custom renderer (lede tagging,
-- chart blocks, image filtering) does not preserve a stable
-- source-to-rendered-DOM offset mapping. NULL anchor_quote = a general
-- (unanchored) comment. A non-NULL anchor_quote that no longer matches the
-- current body (article edited after the comment was made) is an
-- "orphaned" comment -- still returned and shown, just not positioned
-- inline. Resolution is entirely client-side, so this table has no
-- "resolved" concept to track.
CREATE TABLE IF NOT EXISTS draft_comments (
    article_id uuid,
    created_at timestamp,
    comment_id uuid,
    body text,
    author_name text,
    anchor_quote text,
    anchor_prefix text,
    anchor_suffix text,
    PRIMARY KEY ((article_id), created_at, comment_id)
) WITH CLUSTERING ORDER BY (created_at ASC);

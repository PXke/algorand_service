"""Prepared CQL shared between backend and workers for article-adjacent tables.

Both deployables read and write the SAME physical Cassandra tables
(`articles_by_id`, `articles_feed`, `article_versions`, `pending_feed_queue`,
`publish_queue`+`_pending`) via
independently hand-maintained statement classes in each service's own
`app/core/statements.py`. That is the exact shape of bug `feed_bucket.py` was
already extracted to prevent once (see its own docstring) -- a diff of both
files this session found the two class sets had already partly drifted (see
below). Every statement here is BYTE-IDENTICAL CQL text found duplicated
verbatim in both services; each local `statements.py` now assigns its class
attribute from one of these constants instead of defining its own copy, so a
future edit to one of these queries can no longer silently stop matching the
other service's copy.

Deliberately NOT centralized here: statements unique to one service (most of
each class), and a handful of same-NAME statements this diff found already
carry DIFFERENT CQL between the two services -- those are real, possibly
intentional, behavioral differences (documented below) and are left alone
rather than silently unified:

- `ArticleStmts` GET_PUBLISHED_AT / GET_PUBLISHED_AT_AND_DRAFT: workers' copies
  also select `first_published_at`, backend's don't.
- `FeedStmts` INSERT_FULL: workers' carries all 11 projection columns
  (including first_published_at/updated_at, required when DELETE+INSERT-ing a
  moved feed row on recompose); backend's INSERT_FULL is actually the base
  9-column insert (same shape as workers' plain INSERT) -- same NAME, smaller
  query. Only relevant to backend today if it ever needs the move-row pattern,
  which as of this diff it doesn't appear to.
- `ArticleVersionStmts` LIST: workers' includes `body`, backend's doesn't.
- `EditorialBriefStmts` LIST/GET: backend's include `wallet_address`,
  `created_at`, `updated_at`; workers' don't need them for compose.

This module will grow the NEW `articles`/`article_id_lookup`/`article_history`
statement classes once the consolidated schema lands (see the article
data-model consolidation plan); today it only deduplicates existing CQL
against the current 11-table schema, with zero schema change.

Names are flat module-level constants, NOT nested in classes. `_Stmt` is a
data descriptor -- accessing it through a class (`SomeClass.ATTR`) invokes
`__get__` immediately, even at class-BODY-execution time, which would try to
connect to Cassandra at import time (exactly what the lazy-prepare design
exists to avoid). Module-level attribute access does not go through the
descriptor protocol, so importing a flat constant and assigning it to a local
class attribute (`GET_TAGS = ARTICLE_GET_TAGS`) stays lazy -- `__get__` only
fires on the first real `ArticleStmts.GET_TAGS` access from a call site.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cassandra.query import PreparedStatement


class _Stmt:
    """Descriptor holding CQL; resolves to the (cached) PreparedStatement on access.

    Preparation is delegated to `app.core.cassandra.prepare_cached` -- resolved
    per-process, so this works identically whether accessed from backend or
    workers, each of which has its own `app.core.cassandra` module.
    """

    def __init__(self, cql: str) -> None:
        self.cql = cql

    def __get__(self, obj: object | None, owner: type | None) -> PreparedStatement:
        from app.core.cassandra import prepare_cached

        return prepare_cached(self.cql)


# --------------------------------------------------------------------------- #
# articles_by_id
# --------------------------------------------------------------------------- #
ARTICLE_GET_TAGS = _Stmt("SELECT tags FROM algorand_platform.articles_by_id WHERE article_id = ?")
ARTICLE_UPDATE_PUBLISHED_AT = _Stmt(
    "UPDATE algorand_platform.articles_by_id SET published_at = ? WHERE article_id = ?"
)
ARTICLE_CLEAR_TRANSLATIONS = _Stmt(
    "DELETE translations FROM algorand_platform.articles_by_id WHERE article_id = ?"
)

# --------------------------------------------------------------------------- #
# articles_feed
# --------------------------------------------------------------------------- #
FEED_DELETE = _Stmt(
    "DELETE FROM algorand_platform.articles_feed "
    "WHERE bucket = ? AND published_at = ? AND article_id = ?"
)
FEED_SET_SLUG = _Stmt(
    "UPDATE algorand_platform.articles_feed SET slug = ? "
    "WHERE bucket = ? AND published_at = ? AND article_id = ?"
)

# --------------------------------------------------------------------------- #
# article_versions
# --------------------------------------------------------------------------- #
ARTICLE_VERSION_LATEST = _Stmt(
    "SELECT version FROM algorand_platform.article_versions "
    "WHERE article_id = ? ORDER BY version DESC LIMIT 1"
)
ARTICLE_VERSION_INSERT = _Stmt(
    "INSERT INTO algorand_platform.article_versions ("
    "article_id, version, title, summary, body, edit_reason, editor, edited_at"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

# --------------------------------------------------------------------------- #
# pending_feed_queue
# --------------------------------------------------------------------------- #
PENDING_FEED_INSERT = _Stmt(
    "INSERT INTO algorand_platform.pending_feed_queue "
    "(bucket, interest_score, approved_at, article_id) "
    "VALUES (?, ?, ?, ?)"
)
PENDING_FEED_DELETE = _Stmt(
    "DELETE FROM algorand_platform.pending_feed_queue "
    "WHERE bucket = ? AND interest_score = ? AND approved_at = ? AND article_id = ?"
)
# --------------------------------------------------------------------------- #
# publish_queue / publish_queue_pending
# --------------------------------------------------------------------------- #
PUBLISH_QUEUE_INSERT_PENDING = _Stmt(
    "INSERT INTO algorand_platform.publish_queue_pending ("
    "status, priority, created_at, queue_id, service_id, topic, publish_kind"
    ") VALUES (?, ?, ?, ?, ?, ?, ?)"
)
PUBLISH_QUEUE_DELETE_PENDING = _Stmt(
    "DELETE FROM algorand_platform.publish_queue_pending "
    "WHERE status = ? AND priority = ? AND created_at = ? AND queue_id = ?"
)
PUBLISH_QUEUE_SET_HUMAN_PICK = _Stmt(
    "UPDATE algorand_platform.publish_queue SET human_pick_day = ?, updated_at = ? "
    "WHERE queue_id = ?"
)
PUBLISH_QUEUE_CLEAR_HUMAN_PICK = _Stmt(
    "UPDATE algorand_platform.publish_queue SET human_pick_day = null, updated_at = ? "
    "WHERE queue_id = ?"
)


# --------------------------------------------------------------------------- #
# articles / article_history -- the NEW consolidated schema (migration 067).
# Unlike everything above (legacy-table dedup), these are the actual long-term
# interface both services use going forward, so they're proper classes (not
# flat constants) -- nothing here gets re-assigned into another class's body,
# so the _Stmt descriptor's lazy-prepare-on-access behavior is unaffected.
# Cutover is incremental (see the article-table-consolidation plan, step 5):
# this starts with the CREATE path only (insert_stored_article dual-write);
# UPDATE/status-transition/read cutover follow as their own later steps.
# --------------------------------------------------------------------------- #
class ArticlesStmts:
    """Prepared statements for the new `articles` table."""

    INSERT = _Stmt(
        "INSERT INTO algorand_platform.articles ("
        "status, year, published_at, article_id, service_id, title, summary, body, "
        "image_url, tags, source_url, trigger_txid, trigger_round, slug, translations, "
        "first_published_at, updated_at, prompt_version, composed_by_model, deleted_at, "
        "status_updated_at, interest_score, approved_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    # Feed listing: `articles` doubles as the feed projection for
    # status='published' (see the plan) -- year is the partition granularity
    # here (not month, like the old articles_feed), since per-year partitions
    # comfortably hold this platform's real article volume (~7/day). Column
    # set matches the old feed projection's deliberately-body-less shape.
    LIST_PUBLISHED_PAGE = _Stmt(
        "SELECT article_id, service_id, title, summary, published_at, first_published_at, "
        "updated_at, tags, slug, image_url, source_url, translations "
        "FROM algorand_platform.articles "
        "WHERE status = 'published' AND year = ? AND published_at < ? LIMIT ?"
    )
    # 2026-08-24: replaces articles_feed.BY_BUCKET_RECENT for the novelty/
    # duplicate-detection corpus (article_grader._recent_articles) -- one
    # query per relevant year (the ~10-week decay window spans at most 2
    # calendar years) instead of one per month bucket.
    LIST_RECENT_FOR_NOVELTY = _Stmt(
        "SELECT article_id, service_id, title, tags, published_at "
        "FROM algorand_platform.articles "
        "WHERE status = 'published' AND year = ? AND published_at > ?"
    )
    # 2026-08-24: replaces articles_feed.COUNT_TODAY / BY_BUCKET_TAGS-for-day
    # for daily-cap and tag-cap counting -- "today"/"this UTC day" is always
    # within the current year partition, so no fan-out needed.
    COUNT_PUBLISHED_IN_RANGE = _Stmt(
        "SELECT article_id, tags, first_published_at, published_at "
        "FROM algorand_platform.articles "
        "WHERE status = 'published' AND year = ? AND published_at >= ? AND published_at < ?"
    )
    # 2026-08-24: id (+ status_updated_at) enumeration for a given status/
    # year, shared by draft-article listing (backend admin -- needs
    # status_updated_at as its "drafted_at" sort key) and deleted-article
    # listing (410 sitemap exclusion -- ignores the extra column, just needs
    # the ids).
    LIST_IDS_BY_STATUS = _Stmt(
        "SELECT article_id, status_updated_at "
        "FROM algorand_platform.articles WHERE status = ? AND year = ?"
    )
    SET_SLUG = _Stmt(
        "UPDATE algorand_platform.articles SET slug = ? "
        "WHERE status = ? AND year = ? AND published_at = ? AND article_id = ?"
    )
    # article_id alone doesn't locate a row (status/year/published_at are the
    # real partition/clustering key) -- the SAI index on article_id makes this
    # a direct single-round-trip lookup anyway (benchmarked, see the plan).
    GET_BY_ID = _Stmt(
        "SELECT status, year, published_at FROM algorand_platform.articles WHERE article_id = ?"
    )
    # Full row, for status transitions -- delete-old-partition + insert-new-
    # partition must carry every column, or the transition silently drops
    # whatever wasn't explicitly re-listed (the exact "partial feed upsert"
    # bug class article_store.py's replace_article_content/update_article
    # comments already warn about on the OLD schema).
    UPDATE_TAGS = _Stmt(
        "UPDATE algorand_platform.articles SET tags = ? "
        "WHERE status = ? AND year = ? AND published_at = ? AND article_id = ?"
    )
    UPDATE_IMAGE = _Stmt(
        "UPDATE algorand_platform.articles SET image_url = ? "
        "WHERE status = ? AND year = ? AND published_at = ? AND article_id = ?"
    )
    UPDATE_TRANSLATIONS = _Stmt(
        "UPDATE algorand_platform.articles SET translations = translations + ? "
        "WHERE status = ? AND year = ? AND published_at = ? AND article_id = ?"
    )
    CLEAR_TRANSLATIONS = _Stmt(
        "DELETE translations FROM algorand_platform.articles "
        "WHERE status = ? AND year = ? AND published_at = ? AND article_id = ?"
    )
    # In-place content edit where the partition key (status/year/published_at)
    # does NOT move -- a plain UPDATE, not delete+insert. Only safe when the
    # caller already knows status/year/published_at are unchanged (e.g. an
    # admin edit, or a draft-guarded recompose that deliberately doesn't
    # re-stamp published_at).
    UPDATE_CONTENT = _Stmt(
        "UPDATE algorand_platform.articles SET title = ?, summary = ?, body = ?, tags = ?, "
        "image_url = ?, updated_at = ? "
        "WHERE status = ? AND year = ? AND published_at = ? AND article_id = ?"
    )
    GET_FULL_BY_ID = _Stmt(
        "SELECT status, year, published_at, article_id, service_id, title, summary, body, "
        "image_url, tags, source_url, trigger_txid, trigger_round, slug, translations, "
        "first_published_at, updated_at, prompt_version, composed_by_model, deleted_at, "
        "status_updated_at, interest_score, approved_at "
        "FROM algorand_platform.articles WHERE article_id = ?"
    )
    # article-table consolidation Phase 4: replaces pending_feed_queue's
    # (interest_score DESC, approved_at ASC) clustering order -- `articles`
    # has no equivalent clustering key (status/year is the partition,
    # published_at the clustering column), so callers fetch every
    # status='backlog' row for the relevant year(s) and sort in application
    # code. Backlog depth is capped at PENDING_FEED_MAX_DEPTH (default 3),
    # so this is always a tiny scan.
    LIST_BACKLOG = _Stmt(
        "SELECT article_id, service_id, title, interest_score, approved_at "
        "FROM algorand_platform.articles WHERE status = 'backlog' AND year = ?"
    )
    # Direct SAI point-query on service_id -- replaces the article_match_keys
    # "service_id" key-type lookup (article_matching.find_latest_service_article
    # / service_has_article) now that service_id lives on `articles` itself.
    # Filtering to status='published' happens in application code rather than
    # here: status is part of the partition key, and combining a partial-
    # partition-key equality with a SAI predicate needs ALLOW FILTERING --
    # simpler and consistent with this codebase's existing style to fetch by
    # the indexed column and filter in Python (same pattern the callers
    # already used against article_match_keys' unordered result set).
    FIND_BY_SERVICE_ID = _Stmt(
        "SELECT article_id, status, published_at, updated_at "
        "FROM algorand_platform.articles WHERE service_id = ?"
    )
    # Used when article_id is being REUSED for a fresh insert (e.g. recompose-
    # under-review overwriting its own draft row): since published_at is part
    # of the partition key here (unlike the old articles_by_id, keyed by
    # article_id alone), inserting at a new published_at without first
    # deleting the row at the OLD partition key leaves an orphaned duplicate
    # behind -- the exact bug class this whole consolidation exists to kill.
    DELETE = _Stmt(
        "DELETE FROM algorand_platform.articles "
        "WHERE status = ? AND year = ? AND published_at = ? AND article_id = ?"
    )


# --------------------------------------------------------------------------- #
# articles_by_tag -- migration 073. Per-tag partition index maintained by
# article_tag_index.sync_tag_index alongside every `articles` write that can
# change status or tags; powers both `feed?tag=X` (real clustering-ordered,
# keyset-paginated lookup) and tag_stats (distinct-tag universe + cheap
# single-partition COUNT), replacing a 500-row scan-and-filter in Python.
# See migration 073's own comment for why this table exists instead of SAI
# on `articles.tags`.
# --------------------------------------------------------------------------- #
class ArticleTagIndexStmts:
    """Prepared statements for the `articles_by_tag` index table."""

    INSERT = _Stmt(
        "INSERT INTO algorand_platform.articles_by_tag ("
        "tag, published_at, article_id, service_id, title, summary, image_url, "
        "source_url, slug, translations, first_published_at, updated_at, tags"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    DELETE = _Stmt(
        "DELETE FROM algorand_platform.articles_by_tag "
        "WHERE tag = ? AND published_at = ? AND article_id = ?"
    )
    # slug is claimed at release, not creation (see article_store.py's
    # _claim_slug_for_feed) -- after sync_tag_index has already written the
    # tag-index rows with slug=NULL. A non-key column, so a plain UPDATE
    # back-fills it without a delete+insert.
    SET_SLUG = _Stmt(
        "UPDATE algorand_platform.articles_by_tag SET slug = ? "
        "WHERE tag = ? AND published_at = ? AND article_id = ?"
    )
    # Mirrors ArticlesStmts.LIST_PUBLISHED_PAGE's column set exactly, so both
    # feed a card through the same _feed_row_to_stored mapper on the backend
    # side.
    LIST_PAGE = _Stmt(
        "SELECT article_id, service_id, title, summary, published_at, first_published_at, "
        "updated_at, tags, slug, image_url, source_url, translations "
        "FROM algorand_platform.articles_by_tag "
        "WHERE tag = ? AND published_at < ? LIMIT ?"
    )
    # Same column set, no cursor -- used by tag_stats to sample a tag's most
    # recent articles (for the last-seen epoch and the view-count sum), not
    # to paginate.
    LIST_RECENT = _Stmt(
        "SELECT article_id, service_id, title, summary, published_at, first_published_at, "
        "updated_at, tags, slug, image_url, source_url, translations "
        "FROM algorand_platform.articles_by_tag WHERE tag = ? LIMIT ?"
    )
    # Single-partition COUNT -- cheap and exact, unlike a cross-partition
    # GROUP BY (which Cassandra genuinely can't do well); this is the reason
    # tag is the partition key here.
    COUNT = _Stmt("SELECT COUNT(*) FROM algorand_platform.articles_by_tag WHERE tag = ?")
    # Distinct partition keys = the tag universe. Cheap: DISTINCT on a
    # partition key is a per-partition scan that only reads one row per
    # partition, and this platform's real tag cardinality is small and
    # stable (a news-site topic taxonomy, not user-generated free text).
    LIST_TAGS = _Stmt("SELECT DISTINCT tag FROM algorand_platform.articles_by_tag")

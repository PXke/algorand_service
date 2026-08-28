"""Prepared CQL shared between backend and workers for crawl-frontier / classifier tables.

Both deployables read and write the SAME physical Cassandra tables
(`url_queue`, `url_queue_pending`, `classifier_review_queue`,
`classifier_review_pending`, `classifier_feedback`, `crawled_pages_by_domain`)
via independently hand-maintained statement classes in each service's own
`app/core/statements.py`. A 2026-08-28 diff of both files (the same audit that
produced `chain_statements.py` / `article_statements.py` / `platform_statements.py`)
found these specific statements BYTE-IDENTICAL in both copies; each local
`statements.py` now assigns its class attribute from one of these constants
instead of defining its own copy.

Deliberately NOT centralized here (real, documented drift -- see each
statement's own comment where it stays local, and the class-level comments in
both services' `statements.py`):

- `UrlQueueStmts.INSERT_BY_URL`: backend's copy also writes a `status` column
  on `url_queue_by_url`; workers' doesn't. Harmless either way -- nothing
  actually reads `.status` back off that table (workers' own `BY_URL` query
  selects it but `url_queue.py`'s `enqueue_url` only ever reads
  `.queue_id` off that row, then does a fresh status lookup against
  `url_queue` itself via `GET_STATUS`) -- so this is inert drift, not a live
  bug. Left alone rather than guessing which shape is "right".
- `ClassifierReviewStmts.GET_FULL`: workers' copy also selects `status` --
  genuinely needed, since `publish_tasks.py`'s recompose-from-review path
  reads `row.status` to refuse recomposing an already-resolved review
  (2026-07-10 regression fix). Backend's caller (`_complete_classifier_review`)
  never reads status back; it always overwrites it with the resolution being
  applied, so it doesn't need the column.
- `ClassifierFeedbackStmts.GET_GRADE`: workers' copy also selects `url`, but
  no workers call site actually reads it (grep found none) -- unused local
  drift, not exercised by any path today. Left as-is rather than editing
  unrelated dead code out of scope for this consolidation.

Names are flat module-level constants, NOT nested in a class, for the same
reason as `article_statements.py` (see that module's docstring for the full
explanation): `_Stmt` is a data descriptor, and only module-level attribute
access skips the descriptor protocol needed to keep preparation lazy.
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
# url_queue / url_queue_pending
# --------------------------------------------------------------------------- #
URL_QUEUE_INSERT = _Stmt(
    "INSERT INTO algorand_platform.url_queue ("
    "queue_id, url, source, priority, enqueued_at, status, metadata"
    ") VALUES (?, ?, ?, ?, ?, ?, ?) USING TTL ?"
)
URL_QUEUE_INSERT_PENDING = _Stmt(
    "INSERT INTO algorand_platform.url_queue_pending ("
    "status, priority, enqueued_at, queue_id, url, source"
    ") VALUES (?, ?, ?, ?, ?, ?) USING TTL ?"
)

# --------------------------------------------------------------------------- #
# classifier_review_queue / classifier_review_pending
# --------------------------------------------------------------------------- #
CLASSIFIER_REVIEW_INSERT_QUEUE = _Stmt(
    "INSERT INTO algorand_platform.classifier_review_queue ("
    "review_id, url, page_text, page_title, category, "
    "storage_score, status, created_at, metadata"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
CLASSIFIER_REVIEW_GET_DETAIL = _Stmt(
    "SELECT review_id, url, page_title, page_text, category, storage_score, metadata "
    "FROM algorand_platform.classifier_review_queue WHERE review_id = ?"
)
CLASSIFIER_REVIEW_LIST_PENDING = _Stmt(
    "SELECT review_id, url, category, created_at "
    "FROM algorand_platform.classifier_review_pending WHERE status = ? LIMIT ?"
)
CLASSIFIER_REVIEW_DELETE_PENDING = _Stmt(
    "DELETE FROM algorand_platform.classifier_review_pending "
    "WHERE status = ? AND created_at = ? AND review_id = ?"
)

# --------------------------------------------------------------------------- #
# classifier_feedback
# --------------------------------------------------------------------------- #
CLASSIFIER_FEEDBACK_INSERT = _Stmt(
    "INSERT INTO algorand_platform.classifier_feedback ("
    "feedback_id, url, text_sample, category, predicted_category, quality, "
    "predicted_publish, approved, admin_wallet, created_at, metadata"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

# --------------------------------------------------------------------------- #
# crawled_pages_by_domain
# --------------------------------------------------------------------------- #
CRAWLED_PAGE_COUNT_BY_DOMAIN = _Stmt(
    "SELECT COUNT(*) AS c FROM algorand_platform.crawled_pages_by_domain WHERE domain = ?"
)

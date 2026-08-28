"""Regression test for the 2026-08-28 statements.py de-dup consolidation.

Five classes (ClassifierFeedbackStmts, ClassifierReviewStmts, CrawledPageStmts,
UrlQueueStmts, ServiceRegistryStmts) had statements independently copy-pasted
byte-identical between backend/app/core/statements.py and
workers/app/core/statements.py. The identical ones now source from
algorand_shared.crawler_statements / algorand_shared.platform_statements
instead of a local literal -- this test pins that sourcing (so a future edit
can't silently re-fork one copy without the other) and pins the specific,
deliberate drift this consolidation found and left alone (see each
statement's own comment in app/core/statements.py for why).
"""

from __future__ import annotations

from algorand_shared.crawler_statements import (
    CLASSIFIER_FEEDBACK_INSERT,
    CLASSIFIER_REVIEW_DELETE_PENDING,
    CLASSIFIER_REVIEW_GET_DETAIL,
    CLASSIFIER_REVIEW_INSERT_QUEUE,
    CLASSIFIER_REVIEW_LIST_PENDING,
    CRAWLED_PAGE_COUNT_BY_DOMAIN,
    URL_QUEUE_INSERT,
    URL_QUEUE_INSERT_PENDING,
)
from algorand_shared.platform_statements import (
    SERVICE_REGISTRY_GET_ID,
    SERVICE_REGISTRY_GET_SCRAPE_URL,
    SERVICE_REGISTRY_LIST_ALL,
    SERVICE_REGISTRY_SET_ENABLED,
    SERVICE_REGISTRY_UPSERT,
)

from app.core.statements import (
    ClassifierFeedbackStmts,
    ClassifierReviewStmts,
    CrawledPageStmts,
    ServiceRegistryStmts,
    UrlQueueStmts,
)


def test_byte_identical_statements_are_sourced_from_shared() -> None:
    """Each attribute IS the shared descriptor object, not a re-typed copy."""
    assert ClassifierFeedbackStmts.__dict__["INSERT"] is CLASSIFIER_FEEDBACK_INSERT
    assert ClassifierReviewStmts.__dict__["INSERT_QUEUE"] is CLASSIFIER_REVIEW_INSERT_QUEUE
    assert ClassifierReviewStmts.__dict__["GET_DETAIL"] is CLASSIFIER_REVIEW_GET_DETAIL
    assert ClassifierReviewStmts.__dict__["LIST_PENDING"] is CLASSIFIER_REVIEW_LIST_PENDING
    assert ClassifierReviewStmts.__dict__["DELETE_PENDING"] is CLASSIFIER_REVIEW_DELETE_PENDING
    assert CrawledPageStmts.__dict__["COUNT_BY_DOMAIN"] is CRAWLED_PAGE_COUNT_BY_DOMAIN
    assert UrlQueueStmts.__dict__["INSERT"] is URL_QUEUE_INSERT
    assert UrlQueueStmts.__dict__["INSERT_PENDING"] is URL_QUEUE_INSERT_PENDING
    assert ServiceRegistryStmts.__dict__["LIST_ALL"] is SERVICE_REGISTRY_LIST_ALL
    assert ServiceRegistryStmts.__dict__["GET_ID"] is SERVICE_REGISTRY_GET_ID
    assert ServiceRegistryStmts.__dict__["UPSERT"] is SERVICE_REGISTRY_UPSERT
    assert ServiceRegistryStmts.__dict__["SET_ENABLED"] is SERVICE_REGISTRY_SET_ENABLED
    assert ServiceRegistryStmts.__dict__["GET_SCRAPE_URL"] is SERVICE_REGISTRY_GET_SCRAPE_URL


def test_documented_drift_stays_as_documented() -> None:
    """Pin the 3 statements deliberately NOT unified with backend's copy.

    Real, caller-driven differences -- see each class's own docstring --
    must keep their documented shape. A change here means either the drift
    was resolved (update backend's copy + this test together) or it
    silently regressed (fix it back).
    """
    # ClassifierFeedbackStmts.GET_GRADE: no workers call site currently reads
    # `.url` back off the row, but the column must stay selected -- dropping
    # it here without checking is exactly the kind of silent-fork risk this
    # consolidation exists to catch.
    grade_cql = ClassifierFeedbackStmts.__dict__["GET_GRADE"].cql
    assert "url" in grade_cql
    assert "approved" in grade_cql
    assert "metadata" in grade_cql

    # ClassifierReviewStmts.GET_FULL: publish_tasks.py's recompose-from-review
    # path reads row.status to refuse acting on an already-resolved review
    # (2026-07-10 regression fix) -- must keep selecting it.
    full_cql = ClassifierReviewStmts.__dict__["GET_FULL"].cql
    assert "status" in full_cql

    # UrlQueueStmts.INSERT_BY_URL: enqueue_url() never reads `.status` back
    # off the url_queue_by_url row (only `.queue_id`), so this side must NOT
    # write it.
    insert_by_url_cql = UrlQueueStmts.__dict__["INSERT_BY_URL"].cql
    assert "status" not in insert_by_url_cql

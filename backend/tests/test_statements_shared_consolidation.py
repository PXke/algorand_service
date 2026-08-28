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
    assert ServiceRegistryStmts.__dict__["UPSERT"] is SERVICE_REGISTRY_UPSERT
    assert ServiceRegistryStmts.__dict__["GET_ID"] is SERVICE_REGISTRY_GET_ID
    assert ServiceRegistryStmts.__dict__["SET_ENABLED"] is SERVICE_REGISTRY_SET_ENABLED
    assert ServiceRegistryStmts.__dict__["GET_SCRAPE_URL"] is SERVICE_REGISTRY_GET_SCRAPE_URL


def test_documented_drift_stays_as_documented() -> None:
    """Pin the 3 statements deliberately NOT unified with workers' copy.

    Real, caller-driven differences -- see each class's own docstring --
    must keep their documented shape. A change here means either the drift
    was resolved (update workers' copy + this test together) or it silently
    regressed (fix it back).
    """
    # ClassifierFeedbackStmts.GET_GRADE: backend's caller never reads `.url`
    # back off the row (only workers' does) -- must NOT select it.
    grade_cql = ClassifierFeedbackStmts.__dict__["GET_GRADE"].cql
    assert "url" not in grade_cql
    assert "approved" in grade_cql
    assert "metadata" in grade_cql

    # ClassifierReviewStmts.GET_FULL: backend's only caller always overwrites
    # status with the resolution being applied -- must NOT select it.
    full_cql = ClassifierReviewStmts.__dict__["GET_FULL"].cql
    assert "status" not in full_cql

    # UrlQueueStmts.INSERT_BY_URL: backend's admin seed route writes a
    # `status` column workers' copy doesn't -- must keep writing it.
    insert_by_url_cql = UrlQueueStmts.__dict__["INSERT_BY_URL"].cql
    assert "status" in insert_by_url_cql

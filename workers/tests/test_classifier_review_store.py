"""Completing a pending classifier review by id."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from app.modules.crawler.classifier_review_store import complete_classifier_review


def test_complete_classifier_review_invalid_id() -> None:
    """Returns False for a review id that isn't a valid UUID."""
    assert complete_classifier_review("not-a-uuid") is False


def test_complete_classifier_review_success(fake_cassandra_session: MagicMock) -> None:
    """Completes a found pending review, returning True and issuing at least 2 writes."""
    rid = uuid4()
    created = MagicMock()
    created.tzinfo = None

    row = MagicMock()
    row.review_id = rid
    row.url = "https://example.com"
    row.page_text = "body"
    row.page_title = "title"
    row.category = "news"
    row.storage_score = 5.0
    row.status = "pending"
    row.created_at = created
    row.metadata = {"raw": '{"article_id":"abc"}'}

    fake_cassandra_session.execute.return_value.one.return_value = row

    assert complete_classifier_review(str(rid), resolution="approved") is True
    assert fake_cassandra_session.execute.call_count >= 2

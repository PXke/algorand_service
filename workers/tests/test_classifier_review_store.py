from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.modules.crawler.classifier_review_store import complete_classifier_review


def test_complete_classifier_review_invalid_id() -> None:
    assert complete_classifier_review("not-a-uuid") is False


def test_complete_classifier_review_success() -> None:
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

    session = MagicMock()
    session.execute.return_value.one.return_value = row

    with patch(
        "app.core.cassandra.get_cassandra_session",
        return_value=session,
    ):
        assert complete_classifier_review(str(rid), resolution="approved") is True
    assert session.execute.call_count >= 2

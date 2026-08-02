"""Classifier-feedback request validation and category normalization."""

from typing import Any

import pytest

from app.modules.admin.classifier_constants import normalize_content_category
from app.modules.admin.schemas import ClassifierFeedbackCreate
from app.modules.admin.stores.cassandra import AdminCassandraStore


class _FakeSession:
    """Returns an empty result for anything.

    record_classifier_feedback's own two INSERTs plus
    _apply_classifier_corrections' domain_tracking read/write all tolerate
    that; article_id=None short-circuits the article-tag branch before it
    would need a real row.
    """

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, _query: str, _params: tuple = ()) -> Any:  # noqa: ANN401 -- duck-typed Cassandra result
        class _Result:
            def one(self) -> None:
                return None

        return _Result()


def test_classifier_feedback_accepts_category_and_quality() -> None:
    """Round-trips a valid category, predicted_category and quality unchanged."""
    payload = ClassifierFeedbackCreate(
        url="https://algorand.foundation/news",
        approved=True,
        category="news",
        predicted_category="generic",
        quality="high",
    )
    assert payload.category == "news"
    assert payload.predicted_category == "generic"
    assert payload.quality == "high"


def test_classifier_feedback_accepts_pipeline_writer_tag_as_predicted() -> None:
    """Review rows may carry writer tags in the stored category slot."""
    payload = ClassifierFeedbackCreate(
        url="https://example.com/story",
        approved=True,
        category="news",
        predicted_category="defi",
        quality="medium",
    )
    assert payload.category == "news"
    assert payload.predicted_category == "defi"


def test_classifier_feedback_normalizes_category_alias() -> None:
    """Normalizes a category alias like "tools" to its canonical form "tool"."""
    payload = ClassifierFeedbackCreate(
        url="https://example.com",
        approved=True,
        category="tools",
        quality="medium",
    )
    assert payload.category == "tool"


def test_normalize_content_category_maps_unknown_to_generic() -> None:
    """Maps an unknown category to generic, and a known alias to its canonical form."""
    assert normalize_content_category("defi") == "generic"
    assert normalize_content_category("tools") == "tool"


def test_classifier_feedback_coerces_writer_tag_category() -> None:
    """Approve/reject must not 400 when the client sends a pipeline writer tag."""
    payload = ClassifierFeedbackCreate(
        url="https://example.com/story",
        approved=True,
        category="defi",
        predicted_category="defi",
        quality="medium",
    )
    assert payload.category == "generic"
    assert payload.predicted_category == "defi"


def test_classifier_feedback_rejects_invalid_quality() -> None:
    """Raises ValueError when quality is not one of the accepted values."""
    with pytest.raises(ValueError, match="quality must be one of"):
        ClassifierFeedbackCreate(url="https://example.com", approved=False, quality="trash")


def test_record_classifier_feedback_does_not_raise_on_keyword_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for a prod TypeError.

    record_classifier_feedback called _apply_classifier_corrections(approved=...)
    but that method's parameter is named _approved. article_id=None keeps this to
    the exact call path that hit the bug (the store's own domain_tracking write,
    no article/tags touched).
    """
    import app.core.cassandra as cassandra_core

    monkeypatch.setattr(cassandra_core, "get_cassandra_session", lambda: _FakeSession())
    cassandra_core.prepare_cached.cache_clear()
    monkeypatch.setattr(
        AdminCassandraStore, "_record_url_rejected", staticmethod(lambda _url: None)
    )
    monkeypatch.setattr(AdminCassandraStore, "_trigger_compose_next", staticmethod(lambda: None))

    store = AdminCassandraStore()
    result = store.record_classifier_feedback(
        url="https://example.com/story",
        text_sample="some article text",
        category="news",
        predicted_category="generic",
        quality="medium",
        predicted_publish=False,
        approved=False,
        admin_wallet="0xADMIN",
    )
    assert result["approved"] is False
    assert result["category"] == "news"

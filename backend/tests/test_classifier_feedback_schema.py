"""Classifier-feedback request validation and category normalization."""

import pytest

from app.modules.admin.classifier_constants import normalize_content_category
from app.modules.admin.schemas import ClassifierFeedbackCreate


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

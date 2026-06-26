import pytest
from pydantic import ValidationError

from app.modules.admin.schemas import ClassifierFeedbackCreate


def test_classifier_feedback_accepts_category_and_quality() -> None:
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


def test_classifier_feedback_rejects_invalid_quality() -> None:
    with pytest.raises(ValidationError):
        ClassifierFeedbackCreate(url="https://example.com", approved=False, quality="trash")

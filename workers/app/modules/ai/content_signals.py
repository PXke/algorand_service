from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContentSignals:
    """The classifier verdicts for one page, computed once and carried on the
    publish-queue payload. Before this, ``categorize_content`` + ``predict_publish``
    (each touching Cassandra / loading the model) ran up to three times per
    candidate — at the enqueue gate, in the drain's review pre-check, and again
    after composition. Computing once also means the drain and the compose step
    can never disagree about whether an item needs review."""

    category: str
    relevance: float  # on-topic relevance in [0, 1]
    publish_decision: bool | None  # classifier verdict; None => send to review
    confidence: float
    storage_score: float

    @property
    def needs_review(self) -> bool:
        return self.publish_decision is not True

    def to_payload(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "relevance": self.relevance,
            "publish_decision": self.publish_decision,
            "confidence": self.confidence,
            "storage_score": self.storage_score,
        }

    @classmethod
    def from_payload(cls, data: Any) -> ContentSignals | None:
        """Rebuild from a queue payload; None when absent or malformed (so
        callers fall back to recomputing for rows queued before this existed)."""
        if not isinstance(data, dict) or "category" not in data:
            return None
        try:
            return cls(
                category=str(data["category"]),
                relevance=float(data.get("relevance", 1.0)),
                publish_decision=data.get("publish_decision"),
                confidence=float(data.get("confidence", 0.0)),
                storage_score=float(data.get("storage_score", 0.0)),
            )
        except (TypeError, ValueError):
            return None


def compute_content_signals(text: str, url: str) -> ContentSignals:
    from app.modules.ai.content_categorizer import categorize_content
    from app.modules.ai.publish_classifier import (
        predict_publish,
        relevance_score,
        score_content_for_storage,
    )

    category = categorize_content(text, url)
    decision, confidence = predict_publish(text, url, category)
    return ContentSignals(
        category=category,
        relevance=relevance_score(text, url),
        publish_decision=decision,
        confidence=confidence,
        storage_score=score_content_for_storage(text, url),
    )

"""Signals computed once at ingest and carried through the publish queue payload."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContentSignals:
    """The classifier verdicts for one page, computed once and carried on the publish-queue payload. Before this, ``categorize_content`` + ``predict_publish`` (each touching Cassandra / loading the model) ran up to three times per candidate — at the enqueue gate, in the drain's review pre-check, and again after composition. Computing once also means the drain and the compose step can never disagree about whether an item needs review."""

    category: str
    categories: tuple[str, ...]
    relevance: float  # on-topic relevance in [0, 1]
    publish_decision: bool | None  # classifier verdict; None => send to review
    confidence: float
    storage_score: float

    @property
    def needs_review(self) -> bool:
        """Whether the classifier verdict sends this item to manual review."""
        return self.publish_decision is not True

    def to_payload(self) -> dict[str, Any]:
        """Serialize these signals for the publish-queue payload."""
        return {
            "category": self.category,
            "categories": list(self.categories),
            "relevance": self.relevance,
            "publish_decision": self.publish_decision,
            "confidence": self.confidence,
            "storage_score": self.storage_score,
        }

    @classmethod
    def from_payload(cls, data: Any) -> ContentSignals | None:  # noqa: ANN401 -- untrusted queue payload, shape-checked below
        """Rebuild from a queue payload; None when absent or malformed (so callers fall back to recomputing for rows queued before this existed)."""
        if not isinstance(data, dict) or "category" not in data:
            return None
        try:
            raw_cats = data.get("categories")
            if isinstance(raw_cats, list):
                categories = tuple(str(c) for c in raw_cats if c)
            else:
                categories = (str(data["category"]),)
            return cls(
                category=str(data["category"]),
                categories=categories or (str(data["category"]),),
                relevance=float(data.get("relevance", 1.0)),
                publish_decision=data.get("publish_decision"),
                confidence=float(data.get("confidence", 0.0)),
                storage_score=float(data.get("storage_score", 0.0)),
            )
        except (TypeError, ValueError):
            return None


def compute_content_signals(
    text: str, url: str, *, outbound_links: tuple[str, ...] = ()
) -> ContentSignals:
    """outbound_links feeds relevance_score's/score_content_for_storage's explorer-link signal — a multi-chain service's own text can legitimately never say "algorand" while still linking straight to its Algorand explorer entry (quantoz.com/EURQ, zerosignal.ai). Without this, that class of service scores relevance 0 here even after clearing the SAME signal at discovery, sinking its publish-queue priority to the bottom for no real reason (root-caused 2026-07-22)."""
    from app.modules.ai.content_categorizer import categorize_content_all
    from app.modules.ai.publish_classifier import (
        predict_publish,
        relevance_score,
        score_content_for_storage,
    )

    categories = tuple(categorize_content_all(text, url))
    category = categories[0] if categories else "generic"
    decision, confidence = predict_publish(text, url, category)
    return ContentSignals(
        category=category,
        categories=categories,
        relevance=relevance_score(text, url, outbound_links=outbound_links),
        publish_decision=decision,
        confidence=confidence,
        storage_score=score_content_for_storage(text, url, outbound_links=outbound_links),
    )

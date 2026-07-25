"""2026-07-10: a page mis-classified as scam_alert/network_incident scored "confidently publishable" by the ML content-signals classifier (it was legitimate, well-written content — the classifier wasn't wrong, the topic tag was) and sailed past the pre-compute review-slot veto into a full, wasted research + compose pass. _row_needs_review must treat those two topics as always review-bound regardless of what the ML signals say — both correct editorial policy (never auto-publish a scam/incident accusation without a human check) and the fix for the wasted-compute bug, since this function is what lets a drain skip composing when the review queue is already full (see _breaking_review_slot_veto)."""

from types import SimpleNamespace

from app.modules.newspaper.tasks.queue_drain_tasks import _row_needs_review


def _row(topic: str, needs_review: bool) -> SimpleNamespace:
    return SimpleNamespace(
        topic=topic,
        payload={
            "signals": {
                "category": "news",
                "categories": ["news"],
                "relevance": 0.9,
                "publish_decision": not needs_review,
                "confidence": 0.95,
                "storage_score": 1.0,
            }
        },
    )


def test_scam_alert_topic_always_needs_review_even_if_classifier_says_publish() -> None:
    """A scam_alert topic always forces review, even when the classifier says publish."""
    row = _row("scam_alert", needs_review=False)
    assert _row_needs_review(row) is True


def test_network_incident_topic_always_needs_review_even_if_classifier_says_publish() -> None:
    """A network_incident topic always forces review, even when the classifier says publish."""
    row = _row("network_incident", needs_review=False)
    assert _row_needs_review(row) is True


def test_generic_topic_defers_to_classifier_signals() -> None:
    """A generic topic follows whatever the classifier's publish_decision signal says."""
    assert _row_needs_review(_row("generic", needs_review=False)) is False
    assert _row_needs_review(_row("generic", needs_review=True)) is True


def test_unknown_or_missing_topic_falls_through_to_classifier_signals() -> None:
    """An empty/unknown topic falls through to the classifier's publish_decision signal."""
    row = _row("", needs_review=False)
    assert _row_needs_review(row) is False

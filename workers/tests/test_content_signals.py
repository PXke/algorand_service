"""Computing and forwarding content signals through the publish payload."""

from __future__ import annotations

import pytest

from app.modules.ai.content_signals import compute_content_signals


def test_compute_content_signals_forwards_outbound_links(monkeypatch: pytest.MonkeyPatch) -> None:
    """Outbound links passed in reach both relevance_score and score_content_for_storage."""
    # Wiring test for the 2026-07-22 fix: outbound_links must reach both
    # relevance_score and score_content_for_storage, not just be accepted and
    # dropped — that's exactly the bug (score_page supported the signal, but
    # nothing on this call path passed it through).
    import app.modules.ai.content_categorizer as categorizer
    import app.modules.ai.publish_classifier as pc

    monkeypatch.setattr(categorizer, "categorize_content_all", lambda _text, _url: ["news"])
    monkeypatch.setattr(pc, "predict_publish", lambda _text, _url, _category: (True, 0.9))

    seen_relevance_links = []
    seen_storage_links = []
    monkeypatch.setattr(
        pc,
        "relevance_score",
        lambda _text, _url, outbound_links=(): seen_relevance_links.append(outbound_links) or 0.5,
    )
    monkeypatch.setattr(
        pc,
        "score_content_for_storage",
        lambda _text, _url, outbound_links=(): seen_storage_links.append(outbound_links) or 3.0,
    )

    links = ("https://allo.info/asset/1/token",)
    signals = compute_content_signals("some text", "https://svc.example", outbound_links=links)

    assert seen_relevance_links == [links]
    assert seen_storage_links == [links]
    assert signals.relevance == 0.5
    assert signals.storage_score == 3.0


def test_compute_content_signals_defaults_to_no_links(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no outbound_links argument is given, an empty tuple is forwarded to relevance_score."""
    import app.modules.ai.content_categorizer as categorizer
    import app.modules.ai.publish_classifier as pc

    monkeypatch.setattr(categorizer, "categorize_content_all", lambda _text, _url: ["news"])
    monkeypatch.setattr(pc, "predict_publish", lambda _text, _url, _category: (True, 0.9))

    seen = []
    monkeypatch.setattr(
        pc,
        "relevance_score",
        lambda _text, _url, outbound_links=(): seen.append(outbound_links) or 1.0,
    )
    monkeypatch.setattr(pc, "score_content_for_storage", lambda _text, _url, outbound_links=(): 0.0)  # noqa: ARG005 -- name must match the real callee's keyword arg

    compute_content_signals("some text", "https://svc.example")
    assert seen == [()]

"""store_discovery_content: a domain's relevance_score/category must never regress below its best-seen page (root-caused 2026-08-31, algochess.org)."""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.crawler import discovery_store as ds


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    existing_score: float,
    page_score: float,
    page_category: str,
) -> list[dict[str, Any]]:
    """Fake the scorer and the domain_tracker seam; return the list update_domain_status was called with."""
    monkeypatch.setattr(ds, "score_content_for_storage", lambda _text, _url: page_score)
    monkeypatch.setattr(ds, "categorize_content", lambda _text, _url: page_category)
    monkeypatch.setattr(
        ds, "get_domain_status", lambda _domain: {"relevance_score": existing_score}
    )
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        ds,
        "update_domain_status",
        lambda domain, **kwargs: calls.append({"domain": domain, **kwargs}),
    )
    return calls


def test_a_lower_scoring_page_does_not_regress_the_domains_stored_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact algochess.org shape: a rich page (6.9/'tool') already stored, then a thin interactive UI page (0/'generic') crawled -- the domain's column must stay at the higher score, not collapse to 0."""
    calls = _patch(monkeypatch, existing_score=6.9, page_score=0.0, page_category="generic")

    ds.store_discovery_content(
        url="https://algochess.org/practice?level=4&clock=300",
        page_title="AlgoChess — play chess for real ALGO",
        page_text="Practice — free\nStockfish level 4\n5:00\nResign",
    )

    assert len(calls) == 1
    assert calls[0]["relevance_score"] is None  # preserve the existing (higher) score
    assert calls[0]["category"] == ""  # preserve the existing category alongside it


def test_a_higher_scoring_page_does_update_the_domains_stored_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page that scores at or above the domain's current best is still written through -- this isn't a one-way lock, just a floor."""
    calls = _patch(monkeypatch, existing_score=2.0, page_score=6.9, page_category="tool")

    ds.store_discovery_content(
        url="https://algochess.org/",
        page_title="AlgoChess",
        page_text="Chess for real ALGO, settled on-chain on Algorand.",
    )

    assert len(calls) == 1
    assert calls[0]["relevance_score"] == 6.9
    assert calls[0]["category"] == "tool"


def test_a_brand_new_domain_with_no_prior_score_is_written_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No existing domain_tracking row (get_domain_status returns None) floors at 0, so any real page score writes through."""
    monkeypatch.setattr(ds, "score_content_for_storage", lambda _text, _url: 3.5)
    monkeypatch.setattr(ds, "categorize_content", lambda _text, _url: "news")
    monkeypatch.setattr(ds, "get_domain_status", lambda _domain: None)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        ds,
        "update_domain_status",
        lambda domain, **kwargs: calls.append({"domain": domain, **kwargs}),
    )

    ds.store_discovery_content(url="https://new-example.com/", page_title="X", page_text="text")

    assert calls[0]["relevance_score"] == 3.5
    assert calls[0]["category"] == "news"

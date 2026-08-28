"""Storage-time content-quality gates shared across every write path into crawled_pages."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from app.modules.crawler.crawled_page_store import (
    domain_has_similar_content,
    looks_like_soft_404,
    normalize_domain,
)


def test_soft_404_detection() -> None:
    """Short client-router 'not found' fallbacks are flagged; real pages (even short ones) are not."""
    assert looks_like_soft_404('404 Page Not Found The page "gungi" could not be found. Go Home')
    assert looks_like_soft_404("Sorry, this page could not be found.")
    assert not looks_like_soft_404("A short real page with no error phrasing at all.")
    assert not looks_like_soft_404("w " * 200)  # long -- not a soft-404 regardless of content


def test_normalize_domain_matches_upsert_crawled_pages_key() -> None:
    """normalize_domain must produce the exact same key upsert_crawled_page stores under (raw netloc, not the eTLD+1-collapsed form domain_tracker.domain_from_url uses) -- a mismatch here would make domain_has_similar_content silently miss everything already stored."""
    assert normalize_domain("https://www.Lumirogue.com/play/gungi") == "www.lumirogue.com"
    assert normalize_domain("https://lumirogue.com") == "lumirogue.com"
    assert normalize_domain("not a url") == ""


@dataclass
class _ListingRow:
    page_id: str


@dataclass
class _BodyRow:
    body: str


def _stub_domain_lookup(
    monkeypatch: pytest.MonkeyPatch, *, listing_rows: list[_ListingRow], bodies: list[_BodyRow]
) -> None:
    """Wire get_cassandra_session().execute(LIST_BY_DOMAIN, ...) to return listing_rows, and execute_parallel_with_args(GET_BODY, ...) to return one (True, FakeCassandraResult(row)) per body, in order."""
    from conftest import FakeCassandraResult

    session = MagicMock()
    session.execute.return_value = listing_rows
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: session)
    monkeypatch.setattr(
        "app.core.cassandra.execute_parallel_with_args",
        lambda _stmt, _args: [(True, FakeCassandraResult(b)) for b in bodies],
    )


def test_domain_has_similar_content_true_on_exact_normalized_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root-caused 2026-08-28 (Lumi Rogue): ~20 URL-variant guesses of one client-rendered SPA all served byte-identical shell HTML. A new fetch whose normalized body matches an already-crawled page for the same domain must be flagged as a duplicate before it gets stored."""
    shell = "LUMI ROGUE v0.21\n\nTry the demo (tutorial)"
    _stub_domain_lookup(
        monkeypatch,
        listing_rows=[_ListingRow(page_id="p1")],
        bodies=[_BodyRow(body=shell)],
    )
    assert domain_has_similar_content("lumirogue.com", shell) is True


def test_domain_has_similar_content_false_for_genuinely_new_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body that doesn't match anything already crawled for the domain is not flagged as a duplicate."""
    _stub_domain_lookup(
        monkeypatch,
        listing_rows=[_ListingRow(page_id="p1")],
        bodies=[_BodyRow(body="the old shell text")],
    )
    assert domain_has_similar_content("lumirogue.com", "a genuinely different real page") is False


def test_domain_has_similar_content_fails_open_on_lookup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Cassandra hiccup here must never block a legitimate page from being stored (CLAUDE.md §2.9-style fail-open)."""

    def _raise() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", _raise)
    assert domain_has_similar_content("lumirogue.com", "anything") is False


def test_domain_has_similar_content_empty_inputs_short_circuit() -> None:
    """Empty domain/body never reaches Cassandra at all -- no need to stub anything for this to pass."""
    assert domain_has_similar_content("", "some body") is False
    assert domain_has_similar_content("lumirogue.com", "") is False
    assert domain_has_similar_content("lumirogue.com", "   ") is False

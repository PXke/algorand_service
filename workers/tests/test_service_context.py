"""Aggregating per-page context into one bounded service-watch snapshot."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.ai.llm_openai_compatible import _parse_json_object
from app.modules.newspaper.service_context import (
    ContextPage,
    _looks_like_soft_404,
    _select_distinct_pages,
    build_service_context,
)


def _pages() -> list[ContextPage]:
    return [
        ContextPage(url="https://ex.io/zeta", title="Zeta", body="zeta body words"),
        ContextPage(url="https://ex.io/alpha", title="Alpha", body="alpha body words"),
    ]


def test_aggregate_entry_first_then_url_order() -> None:
    """Orders the entry page first, then harvested pages sorted by URL."""
    out = build_service_context(
        service_id="ex-io",
        display_name="Example",
        entry_url="https://ex.io",
        entry_title="Example home",
        entry_text="home text",
        pages=_pages(),
    )
    home = out.index("PAGE: https://ex.io\n")
    alpha = out.index("PAGE: https://ex.io/alpha")
    zeta = out.index("PAGE: https://ex.io/zeta")
    assert home < alpha < zeta  # entry first, harvest in URL order
    assert out.startswith("# SERVICE WATCH: Example")


def test_aggregate_is_stable_across_page_input_order() -> None:
    """Produces identical output regardless of the input pages' order."""
    a = build_service_context(
        service_id="s",
        display_name="S",
        entry_url="https://s.io",
        entry_title="t",
        entry_text="x",
        pages=_pages(),
    )
    b = build_service_context(
        service_id="s",
        display_name="S",
        entry_url="https://s.io",
        entry_title="t",
        entry_text="x",
        pages=list(reversed(_pages())),
    )
    assert a == b


def test_aggregate_respects_total_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Truncates the aggregated context to roughly the configured character cap."""
    import app.core.config as config

    monkeypatch.setattr(config, "SERVICE_CONTEXT_MAX_CHARS", 300)
    pages = [
        ContextPage(url=f"https://ex.io/p{i}", title=f"P{i}", body="w " * 500) for i in range(10)
    ]
    out = build_service_context(
        service_id="s",
        display_name="S",
        entry_url="https://s.io",
        entry_title="t",
        entry_text="entry",
        pages=pages,
    )
    assert len(out) <= 320  # cap plus joiners


def test_aggregate_falls_back_to_entry_only() -> None:
    """Builds a single-page context from just the entry when no harvested pages exist."""
    out = build_service_context(
        service_id="s",
        display_name="S",
        entry_url="https://s.io",
        entry_title="Home",
        entry_text="only the entry",
        pages=[],
    )
    assert "only the entry" in out
    assert out.count("## PAGE:") == 1


def test_fair_share_selection_across_hosts() -> None:
    """Represents quiet, less-recent hosts alongside a busy host instead of letting recency crowd them out."""
    from datetime import UTC, datetime, timedelta

    from app.modules.newspaper.service_context import _fair_share_by_host

    now = datetime.now(tz=UTC)
    busy = [
        (now - timedelta(minutes=i), f"id-b{i}", f"https://blog.x.io/p{i}", f"B{i}")
        for i in range(10)
    ]
    quiet = [
        (now - timedelta(days=5), "id-f", "https://forum.x.io/latest", "Forum"),
        (now - timedelta(days=9), "id-d", "https://docs.x.io/guide", "Docs"),
    ]
    picked = _fair_share_by_host(busy + quiet, max_pages=6)
    hosts = {u.split("/")[2] for _, _, u, _ in picked}
    # The quiet hosts must be represented despite the busy host's recency.
    assert "forum.x.io" in hosts
    assert "docs.x.io" in hosts
    assert len(picked) == 6


def test_soft_404_detection() -> None:
    """Short client-router 'not found' fallbacks are flagged; real pages (even short ones) are not."""
    assert _looks_like_soft_404('404 Page Not Found The page "gungi" could not be found. Go Home')
    assert _looks_like_soft_404("Sorry, this page could not be found.")
    assert not _looks_like_soft_404("A short real page with no error phrasing at all.")
    assert not _looks_like_soft_404("w " * 200)  # long -- not a soft-404 regardless of content


@dataclass
class _FakeBodyRow:
    body: str
    title: str = ""


def _candidate(
    url: str, *, minutes_ago: int = 0, page_id: str = "id", title: str = ""
) -> tuple[datetime, str, str, str]:
    now = datetime.now(tz=UTC)
    return (now - timedelta(minutes=minutes_ago), page_id, url, title)


def _one(row: _FakeBodyRow) -> object:
    class _Result:
        def one(self) -> _FakeBodyRow:
            return row

    return _Result()


def test_select_distinct_pages_skips_soft_404s() -> None:
    """A soft-404 candidate never occupies a page slot, even when nothing else competes for it."""
    ordered = [_candidate("https://ex.io/ghost", page_id="p1")]
    bodies = [(True, _one(_FakeBodyRow(body="404 Page Not Found. Go Home")))]
    pages = _select_distinct_pages(ordered, bodies, max_pages=5)
    assert pages == []


def test_select_distinct_pages_dedupes_content_not_just_url() -> None:
    """Root-caused 2026-08-28 (Lumi Rogue): ~20 URL-variants of one client-rendered SPA all serve byte-identical shell HTML. Content-dedup must collapse them to ONE slot, freeing the rest of max_pages for genuinely different pages instead of the flood eating the whole budget."""
    shell = "LUMI ROGUE v0.21 Try the demo (tutorial) Rankings Need an Ankh?"
    ordered = [
        _candidate("https://ex.io/?view=gungi", minutes_ago=1, page_id="p1"),
        _candidate("https://ex.io/play/gungi", minutes_ago=2, page_id="p2"),
        _candidate("https://ex.io/#/gungi", minutes_ago=3, page_id="p3"),
        _candidate(
            "https://ex.io/about", minutes_ago=100, page_id="p4"
        ),  # genuinely different, older
    ]
    bodies = [
        (True, _one(_FakeBodyRow(body=shell))),
        (True, _one(_FakeBodyRow(body=shell))),
        (True, _one(_FakeBodyRow(body=shell))),
        (True, _one(_FakeBodyRow(body="A real About page with real content about the team."))),
    ]
    pages = _select_distinct_pages(ordered, bodies, max_pages=2)
    urls = [p.url for p in pages]
    assert urls == ["https://ex.io/?view=gungi", "https://ex.io/about"]


def test_parse_json_object_salvages_fences_and_prose() -> None:
    """Extracts a JSON object from raw JSON, a fenced code block, or surrounding prose, else returns None."""
    assert _parse_json_object('{"a": 1}') == {"a": 1}
    assert _parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json_object('Here is the result:\n{"a": {"b": 2}} hope it helps') == {
        "a": {"b": 2}
    }
    assert _parse_json_object("no json here") is None
    assert _parse_json_object("[1, 2]") is None  # root must be an object


def test_parse_json_object_salvages_json_label_fence() -> None:
    """Extracts the JSON object from a fence preceded by a bare "JSON" label line."""
    raw = 'JSON\n\n```json\n{"narrative_synthesis": 4, "technical_depth": 5, "issues": []}\n```'
    assert _parse_json_object(raw) == {
        "narrative_synthesis": 4,
        "technical_depth": 5,
        "issues": [],
    }


def test_parse_json_object_salvages_scores_from_broken_issue_strings() -> None:
    """Recovers the numeric scores even when an unescaped quote breaks the issues array, dropping it instead of failing entirely."""
    raw = '{"narrative_synthesis": 4, "technical_depth": 2, "issues": ["bad "quote" here"]}'
    assert _parse_json_object(raw) == {
        "narrative_synthesis": 4,
        "technical_depth": 2,
        "issues": [],
    }

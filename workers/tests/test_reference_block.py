"""Appending a Sources block of fetched research URLs to a draft."""

from __future__ import annotations

from app.modules.ai.reference_block import append_reference_block, fetched_sources


def _trace() -> list[dict]:
    return [
        {"tool": "search_web", "arguments": {"query": "algoanna"}, "result": {"items": []}},
        {
            "tool": "fetch_url",
            "arguments": {"url": "https://lending.algoanna.com/faq"},
            "result": {"url": "https://lending.algoanna.com/faq", "title": "AlgoAnna Lending FAQ"},
        },
        {
            "tool": "fetch_url",
            "arguments": {"url": "https://algoanna.com"},
            "result": {"url": "https://algoanna.com", "title": "AlgoAnna"},
        },
        {  # failed fetch — must be excluded
            "tool": "fetch_url",
            "arguments": {"url": "https://broken.example"},
            "result": {"url": "https://broken.example", "error": "timeout"},
        },
    ]


def test_fetched_sources_keeps_only_successful_unique() -> None:
    """Keeps only successful, unique fetch_url results and drops the failed one."""
    src = fetched_sources(_trace())
    assert src == [
        ("https://lending.algoanna.com/faq", "AlgoAnna Lending FAQ"),
        ("https://algoanna.com", "AlgoAnna"),
    ]


def test_appends_missing_deep_url_under_new_section() -> None:
    """Adds a fetched deep URL to a new Sources section without duplicating the already-cited domain."""
    # Body cites only the main domain (the reported failure mode).
    body = "Intro paragraph.\n\nSee [AlgoAnna](https://algoanna.com) for more."
    out = append_reference_block({"body": body}, _trace())
    assert "## Sources" in out["body"]
    # The deep URL the model dropped is now present...
    assert "https://lending.algoanna.com/faq" in out["body"]
    # ...and the already-cited main domain isn't duplicated in the block.
    assert out["body"].count("https://algoanna.com)") == 1


def test_merges_into_existing_sources_section_without_duplicate_heading() -> None:
    """Merges missing fetched URLs into an existing "## Sources" section instead of adding a duplicate heading."""
    body = "Body.\n\n## Sources\n\n- [AlgoAnna](https://algoanna.com)"
    out = append_reference_block({"body": body}, _trace())
    assert out["body"].count("## Sources") == 1
    assert "https://lending.algoanna.com/faq" in out["body"]


def test_noop_when_all_urls_already_cited() -> None:
    """Leaves the body unchanged when every fetched URL is already cited in the text."""
    body = "x https://algoanna.com y https://lending.algoanna.com/faq z"
    out = append_reference_block({"body": body}, _trace())
    assert out["body"] == body  # unchanged, no empty Sources block


def test_noop_without_fetches_or_body() -> None:
    """Leaves the body unchanged when there is no fetch trace or the body is empty."""
    assert append_reference_block({"body": "hello"}, [])["body"] == "hello"
    assert append_reference_block({"body": ""}, _trace())["body"] == ""


def _search_trace() -> list[dict]:
    return [
        {
            "tool": "search_web",
            "arguments": {"query": "Bruno Martins Algorand CTO"},
            "result": {
                "query": "Bruno Martins Algorand CTO",
                "results": [
                    {
                        "title": "Algorand plans broad quantum resilience by 2027",
                        "url": "https://www.msn.com/en-us/technology/blockchain/algorand-plans-broad-quantum-resilience-by-2027/ar-AA262424",
                        "snippet": "...",
                    },
                    {
                        # A hit from a domain the body never cites — must NOT
                        # be pulled in just because it showed up in search.
                        "title": "Unrelated coverage",
                        "url": "https://unrelated.example/algorand",
                        "snippet": "...",
                    },
                ],
            },
        },
    ]


def test_backfills_search_hit_for_a_domain_the_body_already_cites() -> None:
    """Replaces a bare-domain citation with the deep link from a matching search-hit result."""
    # Root cause of the broken MSN citation (2026-07-21): the model only ever
    # saw this article as a search snippet, then wrote just the bare domain
    # in its own footer instead of the deep link the snippet actually had.
    body = (
        "Body citing MSN.\n\n## Source\n"
        "- [MSN: Algorand's Quantum Resilience Roadmap](https://www.msn.com)"
    )
    out = append_reference_block({"body": body}, _search_trace())
    assert (
        "https://www.msn.com/en-us/technology/blockchain/"
        "algorand-plans-broad-quantum-resilience-by-2027/ar-AA262424" in out["body"]
    )
    # The unrelated domain the model never cited must not be pulled in.
    assert "unrelated.example" not in out["body"]


def test_does_not_backfill_search_hits_for_uncited_domains() -> None:
    """Adds nothing when no domain in the body matches any search-hit result."""
    # No domain in the body matches any search hit — nothing should be added.
    body = "Body that cites nothing from search results."
    out = append_reference_block({"body": body}, _search_trace())
    assert out["body"] == body

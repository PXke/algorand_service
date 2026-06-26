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
    src = fetched_sources(_trace())
    assert src == [
        ("https://lending.algoanna.com/faq", "AlgoAnna Lending FAQ"),
        ("https://algoanna.com", "AlgoAnna"),
    ]


def test_appends_missing_deep_url_under_new_section() -> None:
    # Body cites only the main domain (the reported failure mode).
    body = "Intro paragraph.\n\nSee [AlgoAnna](https://algoanna.com) for more."
    out = append_reference_block({"body": body}, _trace())
    assert "## Sources" in out["body"]
    # The deep URL the model dropped is now present...
    assert "https://lending.algoanna.com/faq" in out["body"]
    # ...and the already-cited main domain isn't duplicated in the block.
    assert out["body"].count("https://algoanna.com)") == 1


def test_merges_into_existing_sources_section_without_duplicate_heading() -> None:
    body = "Body.\n\n## Sources\n\n- [AlgoAnna](https://algoanna.com)"
    out = append_reference_block({"body": body}, _trace())
    assert out["body"].count("## Sources") == 1
    assert "https://lending.algoanna.com/faq" in out["body"]


def test_noop_when_all_urls_already_cited() -> None:
    body = "x https://algoanna.com y https://lending.algoanna.com/faq z"
    out = append_reference_block({"body": body}, _trace())
    assert out["body"] == body  # unchanged, no empty Sources block


def test_noop_without_fetches_or_body() -> None:
    assert append_reference_block({"body": "hello"}, [])["body"] == "hello"
    assert append_reference_block({"body": ""}, _trace())["body"] == ""

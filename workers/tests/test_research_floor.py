"""Stage-1 research floor: count distinct research tools, excluding review_draft."""

from app.modules.ai.mistral_compose import _distinct_research_calls


def test_counts_distinct_excluding_review_draft() -> None:
    trace = [
        {"tool": "search_web", "result": {}},
        {"tool": "search_web", "result": {}},  # duplicate name -> still one distinct
        {"tool": "search_bluesky", "result": {}},
        {"tool": "get_article", "result": {}},
        {"tool": "review_draft", "result": {}},  # self-check, excluded
        {"result": {}},  # no tool key
    ]
    assert _distinct_research_calls(trace) == 3


def test_empty_trace_is_zero() -> None:
    assert _distinct_research_calls([]) == 0
    assert _distinct_research_calls([{"tool": "review_draft"}]) == 0

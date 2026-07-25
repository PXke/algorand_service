"""Stage-1 research floor: count distinct research SOURCES (domains fetched, or a stable per-tool identity when a call carries no URL), excluding review_draft.

Counting bare tool-name variety let the floor be satisfied by several trivial
calls that all skim the same one or two domains; these tests pin the
domain-aware behavior that replaced it.
"""

from app.modules.ai.mistral_compose import _distinct_research_calls


def test_counts_distinct_excluding_review_draft() -> None:
    """Falls back to per-tool-name identity when no URLs are present, excluding review_draft."""
    # No URLs in any result here, so each falls back to its tool name — same
    # count as the old tool-name-only behavior.
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
    """Counts zero distinct calls for an empty trace or one containing only review_draft."""
    assert _distinct_research_calls([]) == 0
    assert _distinct_research_calls([{"tool": "review_draft"}]) == 0


def test_same_domain_via_different_tools_counts_once() -> None:
    """Counts a URL fetched via two different tools as a single distinct source."""
    trace = [
        {
            "tool": "search_web",
            "arguments": {"query": "algorand"},
            "result": {"results": [{"url": "https://forum.algorand.org/t/1"}]},
        },
        {
            "tool": "fetch_url",
            "arguments": {"url": "https://forum.algorand.org/t/1"},
            "result": {"url": "https://forum.algorand.org/t/1", "text": "..."},
        },
    ]
    assert _distinct_research_calls(trace) == 1


def test_one_search_call_surfacing_many_domains_counts_each() -> None:
    """Counts each distinct domain returned by one search call separately."""
    trace = [
        {
            "tool": "search_web",
            "arguments": {"query": "algorand defi"},
            "result": {
                "results": [
                    {"url": "https://tinyman.org/blog/1"},
                    {"url": "https://folks.finance/news/2"},
                    {"url": "https://algorand.co/updates/3"},
                ]
            },
        },
    ]
    assert _distinct_research_calls(trace) == 3


def test_no_url_tool_falls_back_to_tool_plus_arg_identity() -> None:
    """Falls back to a tool-name-plus-argument identity for URL-less tools, deduping repeats."""
    trace = [
        {"tool": "get_defi_tvl", "arguments": {"protocol": "tinyman"}, "result": {"tvl": 1}},
        {"tool": "get_defi_tvl", "arguments": {"protocol": "tinyman"}, "result": {"tvl": 2}},
        {"tool": "get_defi_tvl", "arguments": {"protocol": "folks"}, "result": {"tvl": 3}},
    ]
    assert _distinct_research_calls(trace) == 2  # repeat same-protocol call doesn't inflate


def test_six_trivial_calls_no_longer_gamed_without_breadth() -> None:
    """Refuses to count six differently-named tool calls against the same domain as six distinct sources."""
    # The exact failure mode the floor exists to prevent: many DIFFERENT tool
    # names, but all pointed at the same one source — must not satisfy a floor
    # of 6 under the new domain-aware counting.
    trace = [
        {
            "tool": f"tool_{i}",
            "arguments": {"url": "https://same-domain.example/x"},
            "result": {"url": "https://same-domain.example/x"},
        }
        for i in range(6)
    ]
    assert _distinct_research_calls(trace) == 1

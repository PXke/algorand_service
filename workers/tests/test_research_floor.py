"""Stage-1 research floor: count distinct research SOURCES (domains fetched, or a stable per-tool identity when a call carries no URL), excluding review_draft.

Counting bare tool-name variety let the floor be satisfied by several trivial
calls that all skim the same one or two domains; these tests pin the
domain-aware behavior that replaced it.
"""

from unittest.mock import MagicMock

import pytest

from app.modules.ai.llm_compose import _distinct_research_calls, _run_research_floor


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


def _domain_trace(n: int) -> list[dict]:
    return [
        {
            "tool": "fetch_url",
            "arguments": {"url": f"https://source-{i}.example/x"},
            "result": {"url": f"https://source-{i}.example/x"},
        }
        for i in range(n)
    ]


# --- _run_research_floor: special-edition threshold -------------------------
#
# Root-caused 2026-08-04: a real special edition's Stage-1 research loop
# stopped at round 4 of a possible 96 (require_tool is None for research, so
# the loop ends the instant the model stops calling tools -- the round
# CEILING never gets hit unless the model keeps going on its own). The one
# existing safety net used the ordinary 6-source bar, trivially cleared by a
# routine multi-topic sweep, so it never engaged even though the piece was
# nowhere near investigative depth. is_special_edition quadruples the target
# (same 4x convention as research_max_rounds) so the floor actually has
# teeth for the case it was supposed to catch.


def _floor_client(
    min_calls_config: int, max_passes_config: int, monkeypatch: pytest.MonkeyPatch
) -> MagicMock:
    monkeypatch.setattr("app.core.config.RESEARCH_FLOOR_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.RESEARCH_MIN_TOOL_CALLS", min_calls_config, raising=False)
    monkeypatch.setattr("app.core.config.RESEARCH_FLOOR_MAX_PASSES", max_passes_config, raising=False)
    return MagicMock()


def test_ordinary_article_does_not_nudge_once_the_normal_bar_is_met(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-special-edition article stops nudging at the plain RESEARCH_MIN_TOOL_CALLS bar."""
    client = _floor_client(6, 1, monkeypatch)
    trace = _domain_trace(6)

    _run_research_floor(client, "sys", "user", [], {}, trace, {})

    client.chat_with_tools.assert_not_called()


def test_special_edition_still_nudges_past_the_normal_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    """6 sources satisfies the ordinary floor but not a special edition's 4x target -- the exact gap that let the real session sail through unnudged."""
    client = _floor_client(6, 1, monkeypatch)
    trace = _domain_trace(6)

    _run_research_floor(client, "sys", "user", [], {}, trace, {}, is_special_edition=True)

    client.chat_with_tools.assert_called()


def test_special_edition_max_passes_are_also_quadrupled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Root-caused 2026-08-04 (Humanitarian Network recompose): min_calls was quadrupled for a special edition, but the number of nudge PASSES to close that gap was not -- a session that plateaued at 8 of a 24-source target got waved through after just 2 unscaled passes, writing a 1,050-word piece with no more depth than an ordinary article. A MagicMock client never grows the trace, so every pass still falls short and the loop runs its full budget -- this pins that budget at max_passes_config * 4, not the raw config value."""
    client = _floor_client(6, 2, monkeypatch)
    trace = _domain_trace(6)

    _run_research_floor(client, "sys", "user", [], {}, trace, {}, is_special_edition=True)

    assert client.chat_with_tools.call_count == 8  # 2 (config) * 4 (special-edition multiplier)


def test_non_special_edition_max_passes_are_not_scaled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ordinary (non-special-edition) path is unaffected by the special-edition multiplier."""
    client = _floor_client(6, 2, monkeypatch)
    trace = _domain_trace(3)  # below the plain 6-source bar, never met

    _run_research_floor(client, "sys", "user", [], {}, trace, {})

    assert client.chat_with_tools.call_count == 2


def test_special_edition_stops_once_it_reaches_the_higher_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """24 sources (6x4) satisfies a special edition's own target -- no nudge needed."""
    client = _floor_client(6, 1, monkeypatch)
    trace = _domain_trace(24)

    _run_research_floor(client, "sys", "user", [], {}, trace, {}, is_special_edition=True)

    client.chat_with_tools.assert_not_called()


def test_special_edition_nudge_states_the_quadrupled_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """The nudge sent to the model names the special edition's own (higher) target, not the ordinary one -- a nudge citing the wrong number would tell the model it's already done."""
    client = _floor_client(6, 1, monkeypatch)
    trace = _domain_trace(6)

    _run_research_floor(client, "sys", "user", [], {}, trace, {}, is_special_edition=True)

    sent_user_content = client.chat_with_tools.call_args[0][0][1]["content"]
    assert "at least 24" in sent_user_content
    assert "at least 6 " not in sent_user_content

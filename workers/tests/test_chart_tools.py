"""chart_data tool: validation, algo_price series, registry wiring."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.modules.ai.chart_tools import (
    _tool_chart_data,
    build_chart,
    chart_data_session_trace,
)
from app.modules.ai.writer_tools import TOOL_HANDLERS, all_tools


def _trace(*entries: tuple[str, dict, dict]) -> list[dict]:
    """Build a fake session trace to pass to ``chart_data_session_trace``.

    Same shape llm_openai_compatible._run_tool_call appends:
    [{"tool", "arguments", "result"}, ...].
    """
    return [{"tool": t, "arguments": a, "result": r} for t, a, r in entries]


class _Sample:
    def __init__(self, collected_at: datetime, price_usd: float) -> None:
        self.collected_at = collected_at
        self.price_usd = price_usd


def test_build_chart_validates_lengths() -> None:
    """Rejects a chart whose series y-values don't match the length of the x-axis."""
    out = build_chart(
        chart_type="bar",
        title="TVL by protocol",
        x=["A", "B"],
        series=[{"name": "TVL", "y": [1.0]}],
    )
    assert "error" in out
    assert "same length" in out["error"]


def test_build_chart_returns_fence() -> None:
    """Builds a valid chart and wraps it in a parseable ```chart markdown fence."""
    out = build_chart(
        chart_type="line",
        title="Weekly active users",
        x=["W1", "W2", "W3"],
        series=[{"name": "Users", "y": [100, 120, 115]}],
    )
    assert out["chart"]["type"] == "line"
    assert out["points"] == 3
    assert out["markdown_fence"].startswith("```chart\n")
    parsed = json.loads(out["markdown_fence"].removeprefix("```chart\n").removesuffix("\n```"))
    assert parsed["title"] == "Weekly active users"


def test_custom_chart_data() -> None:
    """The chart_data tool builds a custom-dataset chart from caller-supplied data.

    x/series data whose numbers were genuinely seen earlier in this session's
    tool trace.
    """
    trace = _trace(
        ("get_fee_estimate", {"chain": "legacy"}, {"fee_usd": 4.50}),
        ("get_fee_estimate", {"chain": "algorand"}, {"fee_usd": 0.001}),
    )
    with chart_data_session_trace(trace):
        out = _tool_chart_data(
            dataset="custom",
            chart_type="bar",
            title="Fees saved",
            x=["Legacy", "Algorand"],
            series=[{"name": "USD", "y": [4.50, 0.001]}],
        )
    assert "error" not in out
    assert out["chart"]["series"][0]["y"] == [4.5, 0.001]


def test_custom_chart_data_grounded_by_a_trace_entry_appended_after_binding() -> None:
    """Reproduces the real production lifecycle (2026-08-27 regression).

    The caller binds an EMPTY list at the start of a compose session, then
    appends tool-call entries to that SAME list object round by round as
    the session progresses -- it is never re-bound with a fresh, already-
    populated list the way every other test in this file sets it up.

    Root-caused live 2026-08-27: `chart_data_session_trace` used to bind
    `trace or None` -- since an empty list is falsy, this discarded the
    real list at the moment it was still empty and bound `None` forever
    (a ContextVar holds an object reference, not a live view of a variable
    the caller keeps mutating). Every subsequent `trace.append(...)` still
    mutated the real list, but the check never saw it -- every custom chart
    in every real compose session was rejected as "not grounded" regardless
    of whether the data was genuinely real, discovered when a live session
    fabricated-looking rejection turned out to name numbers that WERE
    real (algoanna.com's lending-platform loan counts).
    """
    trace: list[dict] = []
    with chart_data_session_trace(trace):
        # Simulates later rounds' tool calls appending to the SAME list
        # object the context was entered with -- not a fresh, re-bound one.
        trace.append({"tool": "get_fee_estimate", "arguments": {"chain": "legacy"}, "result": {"fee_usd": 4.50}})
        trace.append({"tool": "get_fee_estimate", "arguments": {"chain": "algorand"}, "result": {"fee_usd": 0.001}})

        out = _tool_chart_data(
            dataset="custom",
            chart_type="bar",
            title="Fees saved",
            x=["Legacy", "Algorand"],
            series=[{"name": "USD", "y": [4.50, 0.001]}],
        )
    assert "error" not in out
    assert out["chart"]["series"][0]["y"] == [4.5, 0.001]


def test_custom_chart_data_rejects_ungrounded_value() -> None:
    """A custom value with no anchor anywhere in this session's trace is rejected.

    The error names the offending value — the rug.ninja fabrication shape (a
    chart invented with zero tool-call grounding), caught here instead of
    surviving to the gatekeeper after a full compose+review cycle.
    """
    trace = _trace(("get_fee_estimate", {"chain": "legacy"}, {"fee_usd": 4.50}))
    with chart_data_session_trace(trace):
        out = _tool_chart_data(
            dataset="custom",
            chart_type="bar",
            title="Fees saved",
            x=["Legacy", "Algorand"],
            # 0.001 was never fetched by any tool this session -- fabricated.
            series=[{"name": "USD", "y": [4.50, 0.001]}],
        )
    assert "error" in out
    assert "0.001" in out["error"]
    assert "chart" not in out


def test_custom_chart_data_no_session_trace_fails_closed() -> None:
    """No session trace bound at all means a custom chart is rejected.

    (e.g. wiring never entered the context) rather than silently accepted --
    no evidence means no grounding, which is the correct default for a tool
    that must never invent data.
    """
    out = _tool_chart_data(
        dataset="custom",
        chart_type="bar",
        title="Fees saved",
        x=["Legacy", "Algorand"],
        series=[{"name": "USD", "y": [4.50, 0.001]}],
    )
    assert "error" in out


def test_custom_chart_data_allows_derived_percentage() -> None:
    """A percentage correctly computed from a real trace ratio is not falsely rejected.

    0.435 -> 43.5%: "43.5" never appears verbatim in the trace, but this
    mirrors the gatekeeper's own percent<->plain*100 leniency
    (fact_align._matches) exactly, so this check can't be stricter than the
    gatekeeper's own downstream verdict on the same numbers.
    """
    trace = _trace(
        ("get_pool_stats", {"pool": "x", "day": "before"}, {"utilization": 0.201}),
        ("get_pool_stats", {"pool": "x", "day": "now"}, {"utilization": 0.435}),
    )
    with chart_data_session_trace(trace):
        out = _tool_chart_data(
            dataset="custom",
            chart_type="bar",
            title="Pool utilization",
            x=["Before", "Now"],
            series=[{"name": "Utilization %", "y": [20.1, 43.5]}],
        )
    assert "error" not in out


def test_algo_price_chart_unaffected_by_provenance_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The algo_price (fetched-directly) path never invokes the provenance check.

    It's inherently grounded (reads the platform's own metrics store, never
    model-supplied numbers), so it must succeed with zero session trace bound,
    unlike the custom path above.
    """
    from datetime import UTC, datetime

    ts = datetime(2026, 7, 1, tzinfo=UTC)
    samples = [_Sample(ts, 0.15), _Sample(ts.replace(day=2), 0.16)]
    monkeypatch.setattr(
        "app.modules.metrics.price_metrics_store.list_recent_samples",
        lambda **_kw: samples,
    )
    out = _tool_chart_data(dataset="algo_price", days=7)
    assert "error" not in out


def test_algo_price_chart(monkeypatch: pytest.MonkeyPatch) -> None:
    """The algo_price dataset builds a line chart from recent price samples."""
    from datetime import UTC, datetime

    ts = datetime(2026, 7, 1, tzinfo=UTC)
    samples = [_Sample(ts, 0.15), _Sample(ts.replace(day=2), 0.16)]

    monkeypatch.setattr(
        "app.modules.metrics.price_metrics_store.list_recent_samples",
        lambda **_kw: samples,
    )
    out = _tool_chart_data(dataset="algo_price", days=7)
    assert "error" not in out
    assert out["chart"]["type"] == "line"
    assert len(out["chart"]["x"]) == 2
    assert "ALGO" in out["chart"]["series"][0]["name"]


def test_algo_price_insufficient_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    """The algo_price dataset errors out when there are no recent price samples to chart."""
    monkeypatch.setattr(
        "app.modules.metrics.price_metrics_store.list_recent_samples",
        lambda **_kw: [],
    )
    out = _tool_chart_data(dataset="algo_price")
    assert "error" in out


def test_chart_data_registered() -> None:
    """The chart_data tool is registered in both the writer's tool schemas and handlers."""
    schemas, handlers = all_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "chart_data" in names
    assert "chart_data" in handlers
    assert handlers["chart_data"] is TOOL_HANDLERS["chart_data"]


def test_suggest_chart_maps_to_chart_data() -> None:
    """A suggested "plot_generator" capability resolves to the existing chart_data tool."""
    from app.modules.ai.writer_tools import _match_existing_tool

    assert _match_existing_tool("plot_generator", set(TOOL_HANDLERS)) == "chart_data"

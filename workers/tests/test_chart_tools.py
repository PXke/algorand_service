"""chart_data tool: validation, algo_price series, registry wiring."""

from __future__ import annotations

import json

from app.modules.ai.chart_tools import _tool_chart_data, build_chart
from app.modules.ai.writer_tools import TOOL_HANDLERS, all_tools


class _Sample:
    def __init__(self, collected_at, price_usd: float) -> None:
        self.collected_at = collected_at
        self.price_usd = price_usd


def test_build_chart_validates_lengths() -> None:
    out = build_chart(
        chart_type="bar",
        title="TVL by protocol",
        x=["A", "B"],
        series=[{"name": "TVL", "y": [1.0]}],
    )
    assert "error" in out
    assert "same length" in out["error"]


def test_build_chart_returns_fence() -> None:
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
    out = _tool_chart_data(
        dataset="custom",
        chart_type="bar",
        title="Fees saved",
        x=["Legacy", "Algorand"],
        series=[{"name": "USD", "y": [4.50, 0.001]}],
    )
    assert "error" not in out
    assert out["chart"]["series"][0]["y"] == [4.5, 0.001]


def test_algo_price_chart(monkeypatch) -> None:
    from datetime import UTC, datetime

    ts = datetime(2026, 7, 1, tzinfo=UTC)
    samples = [_Sample(ts, 0.15), _Sample(ts.replace(day=2), 0.16)]

    monkeypatch.setattr(
        "app.modules.metrics.price_metrics_store.list_recent_samples",
        lambda **kw: samples,
    )
    out = _tool_chart_data(dataset="algo_price", days=7)
    assert "error" not in out
    assert out["chart"]["type"] == "line"
    assert len(out["chart"]["x"]) == 2
    assert "ALGO" in out["chart"]["series"][0]["name"]


def test_algo_price_insufficient_samples(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modules.metrics.price_metrics_store.list_recent_samples",
        lambda **kw: [],
    )
    out = _tool_chart_data(dataset="algo_price")
    assert "error" in out


def test_chart_data_registered() -> None:
    schemas, handlers = all_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "chart_data" in names
    assert "chart_data" in handlers
    assert handlers["chart_data"] is TOOL_HANDLERS["chart_data"]


def test_suggest_chart_maps_to_chart_data() -> None:
    from app.modules.ai.writer_tools import _match_existing_tool

    assert _match_existing_tool("plot_generator", set(TOOL_HANDLERS)) == "chart_data"

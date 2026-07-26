"""Chart data for the writer: validated ```chart fence JSON from live or custom series.

The Flutter article renderer (when wired) and the compose prompt both expect:
  {"type": "line"|"bar", "title": str, "x": [labels], "series": [{"name": str, "y": [nums]}]}

This tool fetches built-in platform series or validates numbers the model gathered
from other tools — it never invents data.
"""

from __future__ import annotations

import json
import math
from typing import Any

_MAX_POINTS = 20
_MAX_SERIES = 3


def _downsample_pairs(
    pairs: list[tuple[Any, float]], max_points: int = _MAX_POINTS
) -> list[tuple[Any, float]]:
    if len(pairs) <= max_points:
        return pairs
    step = max(1, len(pairs) // max_points)
    return pairs[::step][:max_points]


def algo_price_series(*, days: int = 7) -> list[tuple[str, float]]:
    """Daily-ish ALGO USD samples as (mm-dd label, price)."""
    from app.modules.metrics.price_metrics_store import list_recent_samples

    d = max(1, min(int(days), 30))
    samples = list_recent_samples(asset_id="algorand", lookback_days=d, limit=400)
    pts = sorted(
        ((row.collected_at, float(row.price_usd)) for row in samples),
        key=lambda t: t[0],
    )
    sampled = _downsample_pairs(pts)
    return [(t[0].strftime("%m-%d"), round(t[1], 5)) for t in sampled]


def _validate_chart_labels(x: list[str]) -> tuple[list[str] | None, str | None]:
    """Validate the x-axis category/time labels. Returns (labels, error)."""
    if not isinstance(x, list) or len(x) < 2:
        return None, "x must be a list of at least 2 category/time labels"
    if len(x) > _MAX_POINTS:
        return None, f"x may have at most {_MAX_POINTS} labels"
    labels: list[str] = []
    for i, raw in enumerate(x):
        label = str(raw).strip()
        if not label:
            return None, f"x[{i}] must be a non-empty label"
        if len(label) > 40:
            label = label[:37] + "..."
        labels.append(label)
    return labels, None


def _validate_chart_series_values(ys: list, si: int) -> tuple[list[float] | None, str | None]:
    """Validate one series' y-values are finite numbers. Returns (values, error)."""
    nums: list[float] = []
    for yi, raw in enumerate(ys):
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return None, f"series[{si}].y[{yi}] must be a number"
        if not math.isfinite(val):
            return None, f"series[{si}].y[{yi}] must be a finite number"
        nums.append(round(val, 6))
    return nums, None


def _validate_chart_series(
    series: list[dict[str, Any]], labels: list[str]
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Validate the chart's series list against the already-validated labels. Returns (series, error)."""
    if not isinstance(series, list) or not series:
        return None, "series must be a non-empty list of {name, y} objects"
    if len(series) > _MAX_SERIES:
        return None, f"at most {_MAX_SERIES} series per chart"

    out_series: list[dict[str, Any]] = []
    for si, item in enumerate(series):
        if not isinstance(item, dict):
            return None, f"series[{si}] must be an object with name and y"
        name = str(item.get("name") or "").strip() or f"Series {si + 1}"
        if len(name) > 60:
            name = name[:57] + "..."
        ys = item.get("y")
        if not isinstance(ys, list) or len(ys) != len(labels):
            return None, (
                f"series[{si}].y must be a list of numbers with "
                f"exactly {len(labels)} values (same length as x)"
            )
        nums, error = _validate_chart_series_values(ys, si)
        if error:
            return None, error
        out_series.append({"name": name, "y": nums})
    return out_series, None


def build_chart(
    *,
    chart_type: str,
    title: str,
    x: list[str],
    series: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate and return a chart object ready for a ```chart markdown fence."""
    ctype = (chart_type or "line").strip().lower()
    if ctype not in ("line", "bar"):
        return {"error": "chart_type must be 'line' or 'bar'"}

    clean_title = (title or "").strip()
    if not clean_title:
        return {"error": "title is required"}
    if len(clean_title) > 120:
        return {"error": "title must be at most 120 characters"}

    labels, error = _validate_chart_labels(x)
    if error:
        return {"error": error}

    out_series, error = _validate_chart_series(series, labels)
    if error:
        return {"error": error}

    chart = {"type": ctype, "title": clean_title, "x": labels, "series": out_series}
    fence_body = json.dumps(chart, separators=(",", ":"))
    return {
        "chart": chart,
        "markdown_fence": f"```chart\n{fence_body}\n```",
        "points": len(labels),
    }


def _tool_chart_data(
    *,
    dataset: str = "custom",
    chart_type: str = "line",
    title: str = "",
    days: int = 7,
    x: list[str] | None = None,
    series: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return validated chart JSON (+ markdown fence) for the article body."""
    kind = (dataset or "custom").strip().lower()

    if kind == "algo_price":
        pts = algo_price_series(days=days)
        if len(pts) < 2:
            return {"error": "insufficient ALGO price samples — try again later or skip the chart"}
        labels, prices = zip(*pts, strict=True)
        lookback = max(1, min(int(days), 30))
        chart_title = (title or "").strip() or f"ALGO price (USD), last {lookback} days"
        return build_chart(
            chart_type=chart_type or "line",
            title=chart_title,
            x=list(labels),
            series=[{"name": "ALGO (USD)", "y": list(prices)}],
        )

    if kind != "custom":
        return {
            "error": f"unknown dataset {dataset!r} — use 'algo_price' or 'custom'",
            "datasets": ["algo_price", "custom"],
        }

    if x is None or series is None:
        return {
            "error": "custom dataset requires x (labels) and series (name + y values) "
            "from data you verified via other tools — this tool does not fetch or invent numbers",
        }
    return build_chart(chart_type=chart_type, title=title, x=x, series=series)


CHART_DATA_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "chart_data",
        "description": (
            "Build a validated ```chart JSON block for the article body. Returns "
            "`chart` (the object), `markdown_fence` (paste verbatim into the body), "
            "and `points`. Use dataset=algo_price for a live ALGO USD line chart "
            "(prefer this over get_price_history when you need a chart). Use "
            "dataset=custom with x labels and series y-values ONLY for numbers you "
            "already verified from other tools — never invent data. At most one chart "
            "per article; only when it materially helps the story (see ALGO "
            "PRICE/MARKET RULE)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "dataset": {
                    "type": "string",
                    "enum": ["algo_price", "custom"],
                    "description": (
                        "algo_price: live ALGO USD history from platform metrics; "
                        "custom: your own verified labels + values"
                    ),
                },
                "chart_type": {
                    "type": "string",
                    "enum": ["line", "bar"],
                    "description": "line for trends over time, bar for category comparisons",
                },
                "title": {
                    "type": "string",
                    "description": (
                        "short chart headline (required for custom; optional for algo_price)"
                    ),
                },
                "days": {
                    "type": "integer",
                    "description": "algo_price only: 1-30 lookback days (default 7)",
                },
                "x": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "custom only: category or time labels (2-20)",
                },
                "series": {
                    "type": "array",
                    "description": (
                        "custom only: [{name, y: [numbers]}] — each y length must match x"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "y": {"type": "array", "items": {"type": "number"}},
                        },
                        "required": ["y"],
                    },
                },
            },
            "required": ["dataset"],
        },
    },
}

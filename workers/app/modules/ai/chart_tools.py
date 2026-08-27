"""Chart data for the writer: validated ```chart fence JSON from live or custom series.

The Flutter article renderer (when wired) and the compose prompt both expect:
  {"type": "line"|"bar", "title": str, "x": [labels], "series": [{"name": str, "y": [nums]}]}

This tool fetches built-in platform series (dataset='algo_price', inherently
grounded — it reads the platform's own metrics store, never model input) or
validates numbers the model gathered from other tools (dataset='custom'). The
custom path is enforced, not just documented: each y-value must be entailed by
this session's own tool-call trace (same matcher the gatekeeper's
numeric_entailment_score uses downstream — see ``_custom_series_ungrounded``),
so a fabricated chart is rejected here instead of surviving to publish and
only being caught by the gatekeeper after a full compose+review cycle
(rug.ninja, 2026-08-18: a 10-coin liquidity chart invented with zero tool-call
grounding).
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_MAX_POINTS = 20
_MAX_SERIES = 3

# The currently-composing session's tool-call trace (same shape appended by
# llm_openai_compatible._run_tool_call: [{"tool", "arguments", "result"}, ...]),
# bound for the duration of one compose via `chart_data_session_trace` below.
# A plain module dict/global would leak across concurrent sessions in the same
# process; a ContextVar isolates it per call stack the same way
# writer_tools._recomposing_article_id already does for recompose self-reference.
# `_tool_chart_data`'s handler signature is `handler(**model_args)` (see
# llm_openai_compatible._run_tool_call), so this is the only way to hand it
# session-scoped state the model never supplies as an argument.
_session_trace: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "_chart_session_trace", default=None
)


@contextmanager
def chart_data_session_trace(trace: list[dict[str, Any]] | None) -> Iterator[None]:
    """Bind ``trace`` for a later ``chart_data(dataset='custom', ...)`` call to verify against.

    ``trace`` is this compose session's tool-call trace so far; the later call
    can then check its numbers were actually seen somewhere earlier in THIS
    session, not invented. No-op if trace is falsy — the provenance check then
    fails closed (see ``_custom_series_ungrounded``), which is correct: no
    trace means no evidence, and a custom chart's whole premise is "numbers
    you already verified via other tools."
    """
    token = _session_trace.set(trace or None)
    try:
        yield
    finally:
        _session_trace.reset(token)


def _session_trace_text() -> str:
    """This session's tool trace, rendered as text for the entailment matcher.

    Same ``tool(args) -> result`` shape the gatekeeper's own stored trace uses
    (investigation_store.load_investigation_trace), so the SAME numbers read
    the SAME way either place.
    """
    trace = _session_trace.get()
    if not trace:
        return ""
    lines: list[str] = []
    for entry in trace:
        tool = entry.get("tool", "")
        try:
            args_text = json.dumps(entry.get("arguments", {}))
            result_text = json.dumps(entry.get("result", {}))
        except (TypeError, ValueError):
            args_text = str(entry.get("arguments", {}))
            result_text = str(entry.get("result", {}))
        lines.append(f"{tool}({args_text}) -> {result_text}")
    return "\n".join(lines)


def _value_grounded(trace_text: str, y: float, entail: Any) -> bool:  # noqa: ANN401 -- entail is numeric_entailment_score, typed loosely to avoid importing it at module scope
    """Whether a chart y-value is entailed by trace_text.

    Tries both a bare and a percent-suffixed reading of the value. A chart y-value is a bare JSON number with no adjacent unit marker, unlike real
    article prose where "43.5%" carries its unit inline — so a value that's a
    genuinely-computed percentage of a grounded plain/ratio trace anchor (43.5 from
    a real 0.435 utilization figure) would never appear verbatim as "43.5" anywhere,
    and checking only the bare reading would wrongly flag it. Trying the
    percent-suffixed reading too lets fact_align._matches' own percent<->plain*100
    special case (built for exactly this: a DeFi ratio rendered as a prose percent)
    grant the same leniency here — never more, never less.
    """
    plain = entail(trace_text, str(y))
    if plain.total and plain.score >= 1.0:
        return True
    percent = entail(trace_text, f"{y}%")
    return bool(percent.total) and percent.score >= 1.0


def _custom_series_ungrounded(series: list[dict[str, Any]]) -> list[str]:
    """Custom-dataset y-values with no anchor in this session's tool-call trace.

    Reuses the gatekeeper's own numeric-entailment matcher
    (``gatekeeper.fact_align.numeric_entailment_score``, built on
    ``extract_numbers``) rather than a second, possibly-disagreeing
    implementation — same unit-compatibility rules and the same 2%-relative
    tolerance for rounding (see ``_value_grounded`` for the percent-reading
    nuance).

    Fails CLOSED on missing/empty trace (no evidence to check against — see
    ``chart_data_session_trace``) but permissive on any unexpected error, so a
    bug in this check can never itself block a legitimate chart.
    """
    try:
        from app.modules.gatekeeper.fact_align import numeric_entailment_score

        trace_text = _session_trace_text()
        problems: list[str] = []
        for item in series:
            name = str(item.get("name") or "").strip() or "series"
            problems.extend(
                f"{y!r} in series {name!r}"
                for y in item.get("y") or []
                if not _value_grounded(trace_text, y, numeric_entailment_score)
            )
        return problems
    except Exception:
        return []


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
    built = build_chart(chart_type=chart_type, title=title, x=x, series=series)
    if "error" in built:
        return built
    ungrounded = _custom_series_ungrounded(built["chart"]["series"])
    if ungrounded:
        return {
            "error": (
                f"{'; '.join(ungrounded[:5])} — not grounded in any tool result from this "
                "session. chart_data's custom dataset doesn't fetch or invent numbers: "
                "verify each value via another tool first (or use dataset='algo_price'), "
                "then call chart_data again with only values you've actually confirmed."
            ),
        }
    return built


CHART_DATA_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "chart_data",
        "description": (
            "Build a validated ```chart JSON block (a line or bar plot/graph) for "
            "the article body. Returns "
            "`chart` (the object), `markdown_fence` (paste verbatim into the body), "
            "and `points`. Use dataset=algo_price for a live ALGO USD line chart "
            "(prefer this over get_price_history when you need a chart). Use "
            "dataset=custom with x labels and series y-values ONLY for numbers you "
            "already verified from other tools — never invent data. This is enforced: "
            "each custom y-value is checked against this session's tool-call trace and "
            "rejected with an error naming the ungrounded value if it has no anchor "
            "there, so call the tool that gets the real number first. At most one chart "
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

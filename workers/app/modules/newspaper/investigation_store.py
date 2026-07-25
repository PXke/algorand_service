"""Persist investigative-tool findings (e.g. percent-suffixed computed stats) for the gatekeeper."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

# Known percentage-shaped fields this codebase computes server-side (never
# left to the model — see chain_tools.py/writer_enrichment/price_analysis.py).
_PERCENT_KEYS = frozenset({"online_pct", "change_24h_pct", "week_change_pct", "share_pct"})


def _stringify_percent_fields(result: Any) -> Any:  # noqa: ANN401 -- recursive walk over arbitrary JSON structure (dict/list/scalar)
    """Render known percentage fields with a literal '%' before storing, so the gatekeeper's numeric-entailment check (fact_align.py) can recognize them as percent-class grounding anchors. A bare JSON float (e.g. "online_pct": 92.35) can never ground a "%"-suffixed article claim under its strict unit-equality rule, so a genuinely-computed percentage was otherwise invisible to grounding — root-caused 2026-07-14 alongside a fabricated "99.99%" holder-concentration claim that should have failed entailment but scored gk_factuality=1.00, because nothing in the trace was recognized as percent-class at all."""
    if not isinstance(result, dict):
        return result
    out = dict(result)
    for key in _PERCENT_KEYS:
        value = out.get(key)
        if isinstance(value, int | float):
            out[key] = f"{value}%"
    return out


def load_investigation_trace(service_id: str, *, limit: int = 25) -> str:
    """Reconstruct the agent's tool trace as a text blob for the gatekeeper.

    Reads the evidence trail stored by ``store_investigation_findings`` (keyed by
    service_id == the compose-time source_url). One line per tool call:
    ``tool(arguments) -> result_json``. Best-effort: returns "" on any error so
    the gate degrades gracefully rather than aborting a publish.
    """
    if not service_id:
        return ""
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import InvestigationStmts

        session = get_cassandra_session()
        rows = session.execute(InvestigationStmts.LIST, (service_id, limit))
        lines = [
            f"{r.tool}({r.arguments}) -> {r.result_json}" for r in rows if getattr(r, "tool", None)
        ]
        return "\n".join(lines)
    except Exception:
        return ""


def store_investigation_findings(
    *, service_id: str, source_url: str, trace: list[dict[str, Any]]
) -> int:
    """Persist the investigative agent's tool calls (Cassandra evidence trail).

    Best-effort: never raises into the compose path.
    """
    if not trace:
        return 0
    try:
        from cassandra.util import uuid_from_time

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import InvestigationStmts

        session = get_cassandra_session()
        now = datetime.now(tz=UTC)
        n = 0
        for entry in trace[:25]:
            session.execute(
                InvestigationStmts.INSERT,
                (
                    service_id or "unknown",
                    now,
                    uuid_from_time(now),
                    source_url[:512],
                    str(entry.get("tool", ""))[:64],
                    json.dumps(entry.get("arguments", {}))[:2000],
                    json.dumps(_stringify_percent_fields(entry.get("result", {})))[:8000],
                ),
            )
            n += 1
        return n
    except Exception:
        return 0

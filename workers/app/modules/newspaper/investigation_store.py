from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


def load_investigation_trace(service_id: str, *, limit: int = 25) -> str:
    """Reconstruct the agent's tool trace as a text blob for the gatekeeper.

    Reads the evidence trail stored by ``store_investigation_findings`` (keyed by
    service_id == the compose-time source_url). One line per tool call:
    ``tool(arguments) -> result_json``. Best-effort: returns "" on any error so
    the gate degrades gracefully rather than aborting a publish."""
    if not service_id:
        return ""
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import InvestigationStmts

        session = get_cassandra_session()
        rows = session.execute(InvestigationStmts.LIST, (service_id, limit))
        lines = [
            f"{r.tool}({r.arguments}) -> {r.result_json}"
            for r in rows
            if getattr(r, "tool", None)
        ]
        return "\n".join(lines)
    except Exception:
        return ""


def store_investigation_findings(
    *, service_id: str, source_url: str, trace: list[dict[str, Any]]
) -> int:
    """Persist the investigative agent's tool calls (Cassandra evidence trail).
    Best-effort: never raises into the compose path."""
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
                    json.dumps(entry.get("result", {}))[:8000],
                ),
            )
            n += 1
        return n
    except Exception:
        return 0

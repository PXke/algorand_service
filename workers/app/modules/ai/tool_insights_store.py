"""Writer introspection signals (best-effort, never raises into the compose path):

  - tool_suggestions (Cassandra): capabilities the model wished it had (via the
    suggest_tool tool), reviewed in the admin "Tool insights" tab so we can add
    tools over time.
  - tool errors → Bugsnag: tool calls that errored are reported to Bugsnag (the
    ops dashboard) rather than a bespoke table, grouped by tool name.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_BUCKET = "all"


def record_tool_suggestion(
    capability: str,
    reason: str = "",
    *,
    service_id: str = "",
    source_url: str = "",
    model: str = "",
) -> bool:
    capability = (capability or "").strip()
    if not capability:
        return False
    try:
        from cassandra.util import uuid_from_time

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ToolInsightStmts

        session = get_cassandra_session()
        now = datetime.now(tz=UTC)
        session.execute(
            ToolInsightStmts.INSERT_SUGGESTION,
            (
                _BUCKET,
                now,
                uuid_from_time(now),
                capability[:200],
                (reason or "")[:2000],
                (service_id or "")[:256],
                (source_url or "")[:512],
                (model or "")[:64],
                False,
            ),
        )
        return True
    except Exception:
        return False


def new_session_ref() -> tuple[Any, datetime]:
    """Stable (session_id, created_at) for ONE compose, generated at its start so
    progress checkpoints all upsert the same compose_sessions row (PK is
    (bucket, created_at, session_id))."""
    from cassandra.util import uuid_from_time

    now = datetime.now(tz=UTC)
    return uuid_from_time(now), now


def record_compose_session(
    *,
    debug: dict[str, Any] | None,
    trace: list[dict[str, Any]] | None,
    service_id: str = "",
    source_url: str = "",
    model: str = "",
    final_output: str = "",
    status: str = "ok",
    duration_ms: int = 0,
    session_id: Any = None,
    created_at: datetime | None = None,
) -> bool:
    """Persist the agentic transcript of one compose (best-effort). Pass a stable
    ``session_id``/``created_at`` (from new_session_ref) to UPSERT the same row at
    each stage so the admin sees progress live (status researching -> writing ->
    ok), instead of the row only appearing at the very end."""
    try:
        debug = debug or {}
        slim: list[dict[str, Any]] = []
        for m in (debug.get("messages") or [])[:60]:
            role = str(m.get("role", ""))
            entry: dict[str, Any] = {"role": role}
            content = m.get("content")
            if content is not None:
                entry["content"] = str(content)[: 1500 if role in ("user", "system") else 4000]
            tcs = m.get("tool_calls")
            if tcs:
                entry["tool_calls"] = [
                    {
                        "name": (tc.get("function") or {}).get("name"),
                        "arguments": str((tc.get("function") or {}).get("arguments") or "")[:600],
                    }
                    for tc in tcs
                ]
            if m.get("name"):
                entry["name"] = str(m.get("name"))[:64]
            slim.append(entry)

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ToolInsightStmts

        session = get_cassandra_session()
        if session_id is None or created_at is None:
            session_id, created_at = new_session_ref()
        session.execute(
            ToolInsightStmts.INSERT_COMPOSE_SESSION,
            (
                _BUCKET,
                created_at,
                session_id,
                (service_id or "")[:256],
                (source_url or "")[:512],
                (model or "")[:64],
                (status or "ok")[:32],
                int(debug.get("rounds", 0) or 0),
                len(trace or []),
                int(duration_ms),
                json.dumps(slim)[:120000],
                str(final_output or "")[:20000],
            ),
        )
        return True
    except Exception:
        return False


def record_tool_usage_from_trace(trace: list[dict[str, Any]] | None) -> bool:
    """Increment durable per-tool, per-day call/error counters (tool_usage_stats)
    from one compose's trace. compose_sessions expires after 7 days, so this is
    the only lasting record of which tools the writer leans on and which keep
    failing. Best-effort: wrapped so it can NEVER raise into the compose path.

    'unknown tool' results are model output-format glitches, not tool failures
    (matching report_tool_errors_from_trace), so they count as neither."""
    if not trace:
        return False
    try:
        from datetime import UTC, datetime

        calls: dict[str, int] = {}
        errors: dict[str, int] = {}
        for entry in trace[:60]:
            tool = str(entry.get("tool", "")).strip()[:64]
            if not tool:
                continue
            result = entry.get("result")
            detail = result.get("error", "") if isinstance(result, dict) else ""
            if isinstance(detail, str) and detail.startswith("unknown tool"):
                continue
            calls[tool] = calls.get(tool, 0) + 1
            if detail:
                errors[tool] = errors.get(tool, 0) + 1
        if not calls:
            return False

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ToolInsightStmts

        session = get_cassandra_session()
        day = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        for tool, n in calls.items():
            session.execute(
                ToolInsightStmts.BUMP_USAGE,
                (n, errors.get(tool, 0), day, tool),
            )
        return True
    except Exception:
        return False


def report_tool_errors_from_trace(
    trace: list[dict[str, Any]] | None,
    *,
    service_id: str = "",
    source_url: str = "",
    model: str = "",
) -> int:
    """Log genuinely-errored tool calls at ERROR level — the celery root
    ERROR-log handler forwards these to Bugsnag. Best-effort: wrapped so it can
    NEVER raise into the compose path. Skips 'unknown tool' results, which are
    model output-format glitches (it emitted its final answer as a bogus tool
    call), not real tool failures."""
    if not trace:
        return 0
    n = 0
    try:
        for entry in trace[:50]:
            tool = str(entry.get("tool", ""))
            if tool == "suggest_tool":
                continue
            result = entry.get("result")
            if not (isinstance(result, dict) and "error" in result):
                continue
            detail = str(result.get("error", ""))
            if detail.startswith("unknown tool"):
                continue
            logger.error(
                "writer tool failed: tool=%s args=%s service=%s url=%s error=%s",
                tool[:64],
                json.dumps(entry.get("arguments", {}))[:300],
                service_id[:120],
                source_url[:200],
                detail[:500],
            )
            n += 1
    except Exception:
        return n
    return n

"""Writer introspection signals (best-effort, never raises into the compose path).

- tool_suggestions (Cassandra): capabilities the model wished it had (via the
suggest_tool tool), reviewed in the admin "Tool insights" tab so we can add
tools over time.
- compose_feedback (Cassandra): operational friction the model hit — bad prompts,
source data, tool behavior, pipeline issues — via report_compose_issue.
- tool errors → Bugsnag: tool calls that errored are reported to Bugsnag (the
ops dashboard) rather than a bespoke table, grouped by tool name.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

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
    """Record a writer-suggested missing capability for admin review, best-effort."""
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


_COMPOSE_FEEDBACK_CATEGORIES = frozenset({"prompt", "source_data", "tool", "pipeline", "other"})
_COMPOSE_FEEDBACK_SEVERITIES = frozenset({"low", "medium", "high"})


def record_compose_feedback(
    *,
    category: str,
    summary: str,
    detail: str = "",
    severity: str = "medium",
    related_tool: str = "",
    service_id: str = "",
    source_url: str = "",
    model: str = "",
) -> bool:
    """Persist one writer-reported pipeline issue (report_compose_issue)."""
    cat = (category or "").strip().lower()
    if cat not in _COMPOSE_FEEDBACK_CATEGORIES:
        return False
    headline = (summary or "").strip()
    if not headline:
        return False
    sev = (severity or "medium").strip().lower()
    if sev not in _COMPOSE_FEEDBACK_SEVERITIES:
        sev = "medium"
    try:
        from cassandra.util import uuid_from_time

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ToolInsightStmts

        session = get_cassandra_session()
        now = datetime.now(tz=UTC)
        session.execute(
            ToolInsightStmts.INSERT_COMPOSE_FEEDBACK,
            (
                _BUCKET,
                now,
                uuid_from_time(now),
                cat[:32],
                sev[:16],
                headline[:300],
                (detail or "")[:4000],
                (related_tool or "")[:64],
                (service_id or "")[:256],
                (source_url or "")[:512],
                (model or "")[:64],
            ),
        )
        return True
    except Exception:
        return False


def new_session_ref() -> tuple[UUID, datetime]:
    """Stable (session_id, created_at) for ONE compose, generated at its start so progress checkpoints all upsert the same compose_sessions row (PK is (bucket, created_at, session_id))."""
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
    session_id: UUID | None = None,
    created_at: datetime | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> bool:
    """Persist the agentic transcript of one compose (best-effort). Pass a stable ``session_id``/``created_at`` (from new_session_ref) to UPSERT the same row at each stage so the admin sees progress live (status researching -> writing -> ok), instead of the row only appearing at the very end."""
    try:
        debug = debug or {}
        slim: list[dict[str, Any]] = []
        # No message-count cap: the two-stage pipeline's most diagnostically
        # important turns (the research->write digest handoff,
        # review_draft/LLM-rubric grading, the final write) always come at the
        # END of the transcript, after all research rounds. A research-heavy
        # story can easily exceed 60 messages on tool calls alone (confirmed
        # 2026-07-14: a 4-marketplace NFT story hit 68 tool_calls) — a
        # first-N slice silently dropped the entire handoff+review tail even
        # though the grading itself ran correctly (visible in final_output's
        # heuristic_grade, just invisible in the admin Sessions transcript).
        # Per-message content is still capped below, so row size stays bounded
        # by round count, not unbounded per entry.
        for m in debug.get("messages") or []:
            role = str(m.get("role", ""))
            entry: dict[str, Any] = {"role": role}
            content = m.get("content")
            if content is not None:
                text = str(content)
                # The research→write digest handoff is the one "user" turn worth
                # seeing in full: it's the ONLY place to audit whether a bad fact
                # (wrong math, a fabricated date) originated in digest synthesis
                # or in the write pass itself. The generic 1500-char cap was
                # truncating it mid-sentence, right around where the Liveness
                # Signals section lives — hiding exactly the evidence needed to
                # diagnose a fabrication (2026-07-10, KryptoNurd).
                #
                # Same failure, different message (found 2026-08-02, Messina.one):
                # the FIRST user turn ("Write the article now...") carries the
                # actual scraped source material -- the primary evidence for
                # whether a specific claim (a named protocol, an audit firm) was
                # genuinely grounded or invented. At 1500 chars it cut off after
                # ~30 lines of a multi-page SERVICE WATCH aggregate, making a
                # claim sourced from page 3 of the scrape look unsourced when it
                # wasn't -- nearly produced a false fabrication call on a piece
                # that was actually fine. mistral_compose.py's two known opening
                # templates both start with "Write the article now" (the
                # evolution and standard paths alike), so matching that prefix
                # covers both without needing to know which one fired.
                cap = (
                    6000
                    if text.startswith("[stage 2 handoff]")
                    else (
                        20_000
                        if role == "user" and text.startswith("Write the article now")
                        else (1500 if role in ("user", "system") else 4000)
                    )
                )
                entry["content"] = text[:cap]
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

        # Drop from the FRONT (oldest research rounds first) until the
        # serialized transcript fits the storage cap, instead of blindly
        # slicing the JSON string — a raw string slice cuts mid-object and
        # produces invalid JSON, which would silently break every reader of
        # this column (the Sessions page's json.loads) rather than just
        # dropping the least valuable (earliest) entries cleanly.
        messages_json = json.dumps(slim)
        while len(messages_json) > 120_000 and len(slim) > 1:
            slim.pop(0)
            messages_json = json.dumps(slim)
        messages_json = messages_json[:120_000]

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
                messages_json,
                str(final_output or "")[:20000],
                int(prompt_tokens),
                int(completion_tokens),
                int(total_tokens),
            ),
        )
        return True
    except Exception:
        return False


_NON_TERMINAL_STATUSES = ("researching", "writing")


def reap_stale_compose_sessions(*, stale_minutes: int | None = None) -> dict[str, int]:
    """Mark any compose_sessions row still stuck in a non-terminal status (researching/writing) past the staleness window as "stale". A crash that skips mistral_compose's own try/except checkpoint finalizers (killed process, OOM, or an exception before the first checkpoint call) otherwise leaves the row looking perpetually in-progress in the admin Sessions view until the table's 7-day TTL quietly drops it. Best-effort, never raises."""
    from datetime import UTC, datetime, timedelta

    from app.core.config import COMPOSE_SESSION_STALE_MINUTES

    threshold_minutes = (
        stale_minutes if stale_minutes is not None else COMPOSE_SESSION_STALE_MINUTES
    )
    cutoff = datetime.now(tz=UTC) - timedelta(minutes=threshold_minutes)

    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ToolInsightStmts

        session = get_cassandra_session()
        rows = session.execute(ToolInsightStmts.LIST_ALL_SUMMARY, (_BUCKET,))
        checked = 0
        reaped = 0
        for row in rows:
            checked += 1
            if row.status not in _NON_TERMINAL_STATUSES:
                continue
            created_at = row.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            if created_at >= cutoff:
                continue
            session.execute(
                ToolInsightStmts.MARK_STALE, ("stale", _BUCKET, row.created_at, row.session_id)
            )
            reaped += 1
        return {"checked": checked, "reaped": reaped}
    except Exception:
        logger.warning("failed to reap stale compose sessions", exc_info=True)
        return {"checked": 0, "reaped": 0}


def record_tool_usage_from_trace(trace: list[dict[str, Any]] | None) -> bool:
    """Increment durable per-tool, per-day call/error counters (tool_usage_stats) from one compose's trace. compose_sessions expires after 7 days, so this is the only lasting record of which tools the writer leans on and which keep failing. Best-effort: wrapped so it can NEVER raise into the compose path.

    'unknown tool' results are model output-format glitches, not tool failures
    (matching report_tool_errors_from_trace), so they count as neither.
    """
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
    model: str = "",  # noqa: ARG001 -- name must match the real callee's keyword arg
) -> int:
    """Log genuinely-errored tool calls at ERROR level — the celery root ERROR-log handler forwards these to Bugsnag. Best-effort: wrapped so it can NEVER raise into the compose path. Skips 'unknown tool' results, which are model output-format glitches (it emitted its final answer as a bogus tool call), not real tool failures."""
    if not trace:
        return 0
    n = 0
    try:
        for entry in trace[:50]:
            tool = str(entry.get("tool", ""))
            if tool == "suggest_tool":
                continue
            if tool == "report_compose_issue":
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

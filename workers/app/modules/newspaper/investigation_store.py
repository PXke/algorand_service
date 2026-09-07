"""Persist investigative-tool findings (e.g. percent-suffixed computed stats) for the gatekeeper."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Known percentage-shaped fields this codebase computes server-side (never
# left to the model — see chain_tools.py/writer_enrichment/price_analysis.py).
_PERCENT_KEYS = frozenset({"online_pct", "change_24h_pct", "week_change_pct", "share_pct"})

# A recompose's reinjected-prior-search_x block (see load_prior_search_x_findings
# / format_prior_search_x_block below) is capped at this many distinct queries so
# it can't dominate the enrichment_block prompt slot -- the caller also clips the
# whole rendered block to a hard char limit (llm_compose.py's enrichment_block
# slot), this is a second, cheaper guard against building an oversized string in
# the first place when a service has a long search_x history.
_PRIOR_SEARCH_X_MAX_QUERIES = 8


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


def load_investigation_trace(service_id: str, *, limit: int | None = None) -> str:
    """Reconstruct the agent's tool trace as a text blob for the gatekeeper.

    Reads the evidence trail stored by ``store_investigation_findings`` (keyed by
    service_id == the compose-time source_url). One line per tool call:
    ``tool(arguments) -> result_json``. Best-effort: returns "" on any error so
    the gate degrades gracefully rather than aborting a publish.

    ``limit`` defaults to ``config.INVESTIGATION_TRACE_MAX_ENTRIES`` -- must
    stay >= whatever ``store_investigation_findings`` actually wrote, or the
    read side re-introduces the same first-N-slice truncation the write side
    was just fixed for.
    """
    if not service_id:
        return ""
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.config import INVESTIGATION_TRACE_MAX_ENTRIES
        from app.core.statements import InvestigationStmts

        session = get_cassandra_session()
        rows = session.execute(
            InvestigationStmts.LIST, (service_id, limit or INVESTIGATION_TRACE_MAX_ENTRIES)
        )
        lines = [
            f"{r.tool}({r.arguments}) -> {r.result_json}" for r in rows if getattr(r, "tool", None)
        ]
        return "\n".join(lines)
    except Exception:
        return ""


def load_prior_search_x_findings(
    service_id: str, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Prior search_x results already found for this compose's service_id (== source_url), newest first, deduplicated by normalized query text (only the most recent result per query is kept).

    2026-09-02: reads the SAME evidence trail ``load_investigation_trace``
    reads -- every tool call a compose made, search_x included, already gets
    durably persisted by ``store_investigation_findings`` after every compose
    (has since search_x shipped 2026-08-21). This is a new READ path over
    existing data, not a new write path: search_x results were already
    stored, just never read back to seed a recompose's own research context,
    so a recompose re-ran the whole research loop from scratch and re-paid
    X for a question an earlier compose of the same article already
    answered.

    Skips rows whose stored result carries an "error" key (CLAUDE.md
    invariant 2.8: an error is not a reusable finding to hand back as if it
    were a real answer) and rows with unparseable JSON.

    Best-effort / fails OPEN: returns [] on any Cassandra or parse error, so
    a read failure here only means "nothing to reinject" -- the recompose
    proceeds exactly as it did before this existed (a fresh research loop),
    it is never blocked or failed by this call.
    """
    if not service_id:
        return []
    # The whole read + parse pass lives in ONE try/except (not just the
    # session.execute() call): a fake/misconfigured session in a caller's
    # test double, or a genuinely malformed row, must degrade the same way a
    # connection failure does -- "nothing to reinject", never an uncaught
    # exception into the recompose this is meant to help.
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.config import INVESTIGATION_TRACE_MAX_ENTRIES
        from app.core.statements import InvestigationStmts

        session = get_cassandra_session()
        rows = session.execute(
            InvestigationStmts.LIST, (service_id, limit or INVESTIGATION_TRACE_MAX_ENTRIES)
        )
        findings: list[dict[str, Any]] = []
        seen_queries: set[str] = set()
        for row in rows:
            if getattr(row, "tool", None) != "search_x":
                continue
            try:
                arguments = json.loads(row.arguments) if row.arguments else {}
                result = json.loads(row.result_json) if row.result_json else {}
            except (TypeError, ValueError):
                logger.warning(
                    "load_prior_search_x_findings: skipping a search_x row for %s with unparseable JSON",
                    service_id,
                )
                continue
            if not isinstance(result, dict) or "error" in result:
                continue
            query = str((arguments or {}).get("query") or "").strip()
            if not query:
                continue
            key = re.sub(r"\s+", " ", query.lower())
            if key in seen_queries:
                continue  # rows arrive newest-first; only the latest result per query is worth keeping
            seen_queries.add(key)
            findings.append(
                {
                    "query": query,
                    "result": result,
                    # When this result was actually fetched (the row's own
                    # clustering timestamp) -- format_prior_search_x_block
                    # renders it so the reinjected block's "judge this
                    # against today's date" instruction has the one fact it
                    # depends on. getattr-guarded for older test doubles.
                    "created_at": getattr(row, "created_at", None),
                }
            )
        return findings
    except Exception:
        logger.warning(
            "load_prior_search_x_findings: read failed for %s, reinjecting nothing",
            service_id,
            exc_info=True,
        )
        return []


def format_prior_search_x_block(findings: list[dict[str, Any]]) -> str:
    """Render ``load_prior_search_x_findings``' output as a labeled block for the writer's ``enrichment_block`` prompt slot, so a recompose actually sees its own prior search_x findings as usable research (not just a signal that a duplicate call was skipped).

    Empty string when there is nothing to show -- the normal case for most
    composes (no prior search_x calls for this service_id), so the caller
    can pass the return value straight through as ``enrichment_block``
    without a conditional.
    """
    if not findings:
        return ""
    sections = []
    for f in findings[:_PRIOR_SEARCH_X_MAX_QUERIES]:
        posts = (f["result"].get("posts") or [])[:5]
        if not posts:
            continue
        lines = [
            f'- "{(p.get("text") or "")[:280]}" '
            f"({p.get('likes', 0)} likes, {p.get('reposts', 0)} reposts, {p.get('replies', 0)} replies)"
            for p in posts
        ]
        fetched = _fetched_date_label(f.get("created_at"))
        header = f'### X search: "{f["query"]}"' + (f" (fetched {fetched} UTC)" if fetched else "")
        sections.append(header + "\n" + "\n".join(lines))
    if not sections:
        return ""
    body = "\n\n".join(sections)
    return (
        "\n\n## PRIOR X (TWITTER) RESEARCH FROM AN EARLIER COMPOSE OF THIS ARTICLE\n"
        "The results below were already found by search_x during an earlier research "
        "pass on this same article -- reuse them instead of calling search_x again for "
        "the same or an equivalent question; only call search_x for a genuinely new "
        "question these don't answer. This is real data, but it may be DAYS OR WEEKS "
        "OLD by now -- each query below is labeled with the UTC date it was actually "
        "fetched; judge that date against today's date like any other source, and note "
        "that no fresh live check was made for it this time.\n\n"
        f"{body}"
    )


def _fetched_date_label(created_at: Any) -> str:  # noqa: ANN401 -- Cassandra rows and test doubles hand back datetime, str, or None
    """The 'YYYY-MM-DD' label for a prior finding's fetch time, or "" when the row carries none (an old test double, a missing attribute) -- the block then simply omits the per-query date rather than fabricating one."""
    if created_at is None:
        return ""
    date = getattr(created_at, "date", None)
    if callable(date):
        try:
            return date().isoformat()
        except Exception:
            return ""
    return str(created_at)[:10]


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
        from app.core.config import INVESTIGATION_RESULT_MAX_CHARS, INVESTIGATION_TRACE_MAX_ENTRIES
        from app.core.statements import InvestigationStmts

        session = get_cassandra_session()
        now = datetime.now(tz=UTC)
        n = 0
        for entry in trace[:INVESTIGATION_TRACE_MAX_ENTRIES]:
            session.execute(
                InvestigationStmts.INSERT,
                (
                    service_id or "unknown",
                    now,
                    uuid_from_time(now),
                    source_url[:512],
                    str(entry.get("tool", ""))[:64],
                    json.dumps(entry.get("arguments", {}))[:2000],
                    json.dumps(_stringify_percent_fields(entry.get("result", {})))[
                        :INVESTIGATION_RESULT_MAX_CHARS
                    ],
                ),
            )
            n += 1
        return n
    except Exception:
        return 0

"""Read-only analysis of writer agentic transcripts (compose_sessions).

Answers three questions that `suggest_tool` cannot, because the model only asks
for capabilities it can name:

  1. TOOL USAGE   — how often each tool is called, and its error rate.
  2. DEAD TOOLS   — registered tools the writer never calls (schema-budget waste).
  3. SILENT ON-CHAIN DEMAND — articles that reference an Algorand account / asset /
     app / txid in their body but where NO tool ever verified it on-chain (the
     gap the writer routes around instead of suggesting).

Run on a host with the workers env (same env deploy.sh uses for Cassandra):

    cd workers && python -m scripts.analyze_compose_sessions [--limit 2000]

It is strictly read-only (SELECT only) and never writes.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# compose_sessions has a single 'all' bucket (see 029_compose_sessions.cql); the
# transcript writer uses the same constant.
_BUCKET = "all"

# Algorand identifiers as they appear in prose. Addresses are 58-char base32,
# txids 52-char base32; asset/app ids are large integers introduced by a keyword
# so we don't match every number (rounds, prices, dates).
_RE_ADDRESS = re.compile(r"\b[A-Z2-7]{58}\b")
_RE_TXID = re.compile(r"\b[A-Z2-7]{52}\b")
_RE_ASSET = re.compile(r"\b(?:asset|ASA)\s*(?:id|ID|#|number)?\s*[:#]?\s*(\d{4,})", re.I)
_RE_APP = re.compile(
    r"\b(?:app(?:lication)?|smart contract)\s*(?:id|ID|#)?\s*[:#]?\s*(\d{4,})", re.I
)

# Tools that, once they exist, would satisfy the silent demand above. Listing
# them lets the report say "of N stories wanting on-chain data, M had an on-chain
# tool available and used it" once these ship.
_ONCHAIN_TOOLS = {
    "lookup_account",
    "lookup_asset",
    "lookup_application",
    "lookup_transaction",
}


def _known_tools() -> set[str]:
    """Live tool registry, so DEAD-tool detection tracks additions/removals."""
    try:
        from app.modules.ai.writer_tools import all_tools

        schemas, _ = all_tools(context=None)
        return {s["function"]["name"] for s in schemas if s.get("function")}
    except Exception as exc:  # pragma: no cover - env-dependent
        logger.warning("could not load live tool registry: %s; DEAD-tools skipped", exc)
        return set()


def _iter_sessions(limit: int) -> Iterator[Any]:
    from cassandra.query import SimpleStatement

    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    stmt = SimpleStatement(
        "SELECT created_at, model, status, source_url, messages, final_output "
        "FROM compose_sessions WHERE bucket=%s",
        fetch_size=200,
    )
    for seen, row in enumerate(session.execute(stmt, (_BUCKET,)), start=1):
        yield row
        if seen >= limit:
            break


def _result_is_error(content: str) -> bool:
    """A role='tool' message's content is the json.dumps'd result (truncated to 4000 chars). Errored handlers return {"error": ...}; detect that robustly."""
    if not content:
        return False
    try:
        obj = json.loads(content)
        if isinstance(obj, dict):
            return bool(obj.get("error"))
    except Exception:
        pass
    return '"error"' in content[:300]


@dataclass
class _ScanStats:
    """Accumulated counters and examples for one full compose_sessions scan."""

    calls: Counter[str] = field(default_factory=Counter)
    errors: Counter[str] = field(default_factory=Counter)
    by_model: Counter[str] = field(default_factory=Counter)
    n_sessions: int = 0
    n_failed_status: int = 0
    onchain_refs: int = 0  # stories whose body references an on-chain id
    onchain_verified: int = 0  # ... and that called an on-chain tool
    onchain_examples: list[str] = field(default_factory=list)


def _onchain_hits(body: str) -> list[str]:
    hits = []
    if _RE_ADDRESS.search(body):
        hits.append("address")
    if _RE_TXID.search(body):
        hits.append("txid")
    am = _RE_ASSET.search(body)
    if am:
        hits.append(f"asset#{am.group(1)}")
    pm = _RE_APP.search(body)
    if pm:
        hits.append(f"app#{pm.group(1)}")
    return hits


def _scan_session_messages(row: Any, stats: _ScanStats) -> bool:  # noqa: ANN401 -- duck-typed Cassandra driver row, no formal class
    """Tally tool calls/errors from one session's transcript. Returns whether an on-chain tool was used."""
    used_onchain_tool = False
    try:
        messages = json.loads(row.messages or "[]")
    except Exception:
        messages = []
    for m in messages:
        for tc in m.get("tool_calls") or []:
            name = tc.get("name") or "?"
            stats.calls[name] += 1
            if name in _ONCHAIN_TOOLS:
                used_onchain_tool = True
        if m.get("role") == "tool":
            name = m.get("name") or "?"
            if _result_is_error(m.get("content") or ""):
                stats.errors[name] += 1
    return used_onchain_tool


def _scan_session(row: Any, stats: _ScanStats) -> None:  # noqa: ANN401 -- duck-typed Cassandra driver row, no formal class
    """Fold one compose_sessions row into the running scan stats."""
    stats.n_sessions += 1
    stats.by_model[row.model or "?"] += 1
    if (row.status or "") not in ("ok", "researching", "writing"):
        stats.n_failed_status += 1

    used_onchain_tool = _scan_session_messages(row, stats)

    hits = _onchain_hits(row.final_output or "")
    if not hits:
        return
    stats.onchain_refs += 1
    if used_onchain_tool:
        stats.onchain_verified += 1
    elif len(stats.onchain_examples) < 12:
        ts = row.created_at.strftime("%Y-%m-%d") if row.created_at else "?"
        src = (row.source_url or "")[:60]
        stats.onchain_examples.append(f"    {ts}  [{', '.join(hits)}]  {src}")


def _report_tool_usage(stats: _ScanStats) -> None:
    logger.info("\n== TOOL USAGE (calls, errors, error-rate) ==")
    if not stats.calls:
        logger.info("  no tool calls recorded")
    for name, c in stats.calls.most_common():
        e = stats.errors.get(name, 0)
        rate = f"{100 * e / c:4.0f}%" if c else "  - "
        logger.info("  %-28s %6d calls  %5d err  %s", name, c, e, rate)
    # tools that ONLY appear as errored results (rare) still show via errors map
    for name in sorted(set(stats.errors) - set(stats.calls)):
        logger.info("  %-28s %6s        %5d err  (results only)", name, "-", stats.errors[name])


def _report_dead_tools(known: set[str], stats: _ScanStats) -> None:
    if not known:
        return
    dead = sorted(known - set(stats.calls))
    logger.info("\n== DEAD TOOLS (registered, never called) ==")
    logger.info("  %s", ", ".join(dead) if dead else "none — every registered tool was used")


def _report_onchain_demand(stats: _ScanStats) -> None:
    logger.info("\n== SILENT ON-CHAIN DEMAND ==")
    logger.info("  %d stories reference an on-chain id in the body", stats.onchain_refs)
    logger.info(
        "  %d of those used an on-chain tool (%d such tools exist)",
        stats.onchain_verified,
        len(_ONCHAIN_TOOLS),
    )
    unverified = stats.onchain_refs - stats.onchain_verified
    logger.info(
        "  -> %d stories wrote about on-chain entities with NO way to verify them", unverified
    )
    if stats.onchain_examples:
        logger.info("  examples (date, what was referenced, source):")
        logger.info("%s", "\n".join(stats.onchain_examples))


def main() -> None:
    """CLI entrypoint: scan recent compose sessions and print tool-usage/error stats."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=2000, help="max sessions to scan (newest first)")
    args = ap.parse_args()

    known = _known_tools()
    stats = _ScanStats()
    for row in _iter_sessions(args.limit):
        _scan_session(row, stats)

    logger.info(
        "\nScanned %d compose sessions  (%d non-ok status)", stats.n_sessions, stats.n_failed_status
    )
    logger.info("Models: %s", ", ".join(f"{m}={c}" for m, c in stats.by_model.most_common()))
    _report_tool_usage(stats)
    _report_dead_tools(known, stats)
    _report_onchain_demand(stats)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()

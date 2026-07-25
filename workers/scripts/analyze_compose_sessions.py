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


def main() -> None:
    """CLI entrypoint: scan recent compose sessions and print tool-usage/error stats."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=2000, help="max sessions to scan (newest first)")
    args = ap.parse_args()

    known = _known_tools()
    calls: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    by_model: Counter[str] = Counter()

    n_sessions = 0
    n_failed_status = 0
    onchain_refs = 0  # stories whose body references an on-chain id
    onchain_verified = 0  # ... and that called an on-chain tool
    onchain_examples: list[str] = []

    for row in _iter_sessions(args.limit):
        n_sessions += 1
        by_model[row.model or "?"] += 1
        if (row.status or "") not in ("ok", "researching", "writing"):
            n_failed_status += 1

        used_onchain_tool = False
        try:
            messages = json.loads(row.messages or "[]")
        except Exception:
            messages = []
        for m in messages:
            for tc in m.get("tool_calls") or []:
                name = tc.get("name") or "?"
                calls[name] += 1
                if name in _ONCHAIN_TOOLS:
                    used_onchain_tool = True
            if m.get("role") == "tool":
                name = m.get("name") or "?"
                if _result_is_error(m.get("content") or ""):
                    errors[name] += 1

        body = row.final_output or ""
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
        if hits:
            onchain_refs += 1
            if used_onchain_tool:
                onchain_verified += 1
            elif len(onchain_examples) < 12:
                ts = row.created_at.strftime("%Y-%m-%d") if row.created_at else "?"
                src = (row.source_url or "")[:60]
                onchain_examples.append(f"    {ts}  [{', '.join(hits)}]  {src}")

    # ---- report ----------------------------------------------------------
    logger.info("\nScanned %d compose sessions  (%d non-ok status)", n_sessions, n_failed_status)
    logger.info("Models: %s", ", ".join(f"{m}={c}" for m, c in by_model.most_common()))

    logger.info("\n== TOOL USAGE (calls, errors, error-rate) ==")
    if not calls:
        logger.info("  no tool calls recorded")
    for name, c in calls.most_common():
        e = errors.get(name, 0)
        rate = f"{100 * e / c:4.0f}%" if c else "  - "
        logger.info("  %-28s %6d calls  %5d err  %s", name, c, e, rate)
    # tools that ONLY appear as errored results (rare) still show via errors map
    for name in sorted(set(errors) - set(calls)):
        logger.info("  %-28s %6s        %5d err  (results only)", name, "-", errors[name])

    if known:
        dead = sorted(known - set(calls))
        logger.info("\n== DEAD TOOLS (registered, never called) ==")
        logger.info("  %s", ", ".join(dead) if dead else "none — every registered tool was used")

    logger.info("\n== SILENT ON-CHAIN DEMAND ==")
    logger.info("  %d stories reference an on-chain id in the body", onchain_refs)
    logger.info(
        "  %d of those used an on-chain tool (%d such tools exist)",
        onchain_verified,
        len(_ONCHAIN_TOOLS),
    )
    unverified = onchain_refs - onchain_verified
    logger.info(
        "  -> %d stories wrote about on-chain entities with NO way to verify them", unverified
    )
    if onchain_examples:
        logger.info("  examples (date, what was referenced, source):")
        logger.info("%s", "\n".join(onchain_examples))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()

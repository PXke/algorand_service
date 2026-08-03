"""Find a compose session's transcript by matching its source_url, bypassing the admin UI's LIST_COMPOSE_SESSIONS_SUMMARY (capped at the 20 most recent — useless once the article in question has scrolled off that window).

Run on the prod host, in the workers env (same Cassandra creds deploy.sh uses):

    cd workers && python -m scripts.find_compose_session algorank
    cd workers && python -m scripts.find_compose_session algorank --grep pera,wallet

--grep filters the printed tool-call messages to ones mentioning any of the
given comma-separated keywords, instead of dumping the whole transcript —
handy for "did any fetch actually see X" questions.

Every message prints in FULL, never truncated. A prior 2000/5000-char
display cap here caused three separate near-miss false-fabrication calls
this session (Messina.one, museum.datahistory.org, lora.algokit.io) — each
time a claim looked ungrounded only because the tool result or final_output
that actually sourced it had scrolled past the cap, not because the writer
invented it. Forensic questions ("is X actually grounded?") need the whole
story, not a preview of it.

Strictly read-only (SELECT only), never writes.
"""

from __future__ import annotations

import argparse
import json
import logging

logger = logging.getLogger(__name__)

_BUCKET = "all"

# No LIMIT — the admin API caps at 20 rows for its polled list view, which is
# useless once the session you want has scrolled off. This scans the whole
# partition; compose_sessions' clustering is created_at (newest first), so
# results come back newest-first same as the admin UI.
_SELECT_ALL = (
    "SELECT created_at, session_id, service_id, source_url, model, status, "
    "rounds, tool_calls, messages, final_output "
    "FROM algorand_platform.compose_sessions WHERE bucket = %s"
)


def main() -> None:
    """Parse CLI args, scan compose_sessions for source_url matches, and print each transcript."""
    parser = argparse.ArgumentParser()
    parser.add_argument("match", help="substring to match against source_url (case-insensitive)")
    parser.add_argument(
        "--grep",
        default="",
        help="comma-separated keywords; only print tool messages containing one of these",
    )
    parser.add_argument("--max-matches", type=int, default=5)
    args = parser.parse_args()

    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    rows = session.execute(_SELECT_ALL, (_BUCKET,))

    keywords = [k.strip().lower() for k in args.grep.split(",") if k.strip()]
    found = 0
    for row in rows:
        if args.match.lower() not in (row.source_url or "").lower():
            continue
        found += 1
        logger.info("=" * 80)
        logger.info(
            "session_id=%s created_at=%s service_id=%s",
            row.session_id,
            row.created_at,
            row.service_id,
        )
        logger.info(
            "source_url=%s status=%s rounds=%s tool_calls=%s",
            row.source_url,
            row.status,
            row.rounds,
            row.tool_calls,
        )
        try:
            messages = json.loads(row.messages) if row.messages else []
        except Exception:
            messages = []
        for msg in messages:
            text = json.dumps(msg)
            if keywords and not any(k in text.lower() for k in keywords):
                continue
            role = msg.get("role", "?")
            name = msg.get("name", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = json.dumps(content)
            logger.info("--- [%s%s] ---", role, f" {name}" if name else "")
            logger.info("%s", content or "")
        if not keywords:
            logger.info("--- final_output ---")
            logger.info("%s", row.final_output or "")
        if found >= args.max_matches:
            break

    if not found:
        logger.info("No compose sessions found with source_url matching %r", args.match)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()

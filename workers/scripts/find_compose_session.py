"""Find a compose session's transcript by matching its source_url, bypassing
the admin UI's LIST_COMPOSE_SESSIONS_SUMMARY (capped at the 20 most recent —
useless once the article in question has scrolled off that window).

Run on the prod host, in the workers env (same Cassandra creds deploy.sh uses):

    cd workers && python -m scripts.find_compose_session algorank
    cd workers && python -m scripts.find_compose_session algorank --grep pera,wallet

--grep filters the printed tool-call messages to ones mentioning any of the
given comma-separated keywords, instead of dumping the whole transcript —
handy for "did any fetch actually see X" questions.

Strictly read-only (SELECT only), never writes.
"""

from __future__ import annotations

import argparse
import json

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
    parser = argparse.ArgumentParser()
    parser.add_argument("match", help="substring to match against source_url (case-insensitive)")
    parser.add_argument(
        "--grep", default="",
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
        print("=" * 80)
        print(
            f"session_id={row.session_id} created_at={row.created_at} "
            f"service_id={row.service_id}"
        )
        print(
            f"source_url={row.source_url} status={row.status} "
            f"rounds={row.rounds} tool_calls={row.tool_calls}"
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
            print(f"--- [{role}{' ' + name if name else ''}] ---")
            print((content or "")[:2000])
        if not keywords:
            print("--- final_output ---")
            print((row.final_output or "")[:5000])
        if found >= args.max_matches:
            break

    if not found:
        print(f"No compose sessions found with source_url matching {args.match!r}")


if __name__ == "__main__":
    main()

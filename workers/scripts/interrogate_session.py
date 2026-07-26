"""Revive a compose session and talk to the writer that produced it.

Replays the stored transcript (everything the model saw) back into the SAME
model and lets you ask it about the article — an interactive post-mortem. By
default it first confronts the writer with fresh ground truth (live DNS on every
domain the article links, plus the fetch failures already in its own transcript)
so it must reconcile its draft with reality instead of just re-defending it.

Run on the prod host, in the workers env (same Cassandra + Mistral creds
deploy.sh uses):

    cd workers && python -m scripts.interrogate_session goplausible
    cd workers && python -m scripts.interrogate_session 19e2cc05-c7da-43b0-86b0-46931ee37a28
    cd workers && python -m scripts.interrogate_session goplausible \
        -q "where did the '1,000 issuers' figure come from? was it in your sources?"

Positional TARGET is a source_url substring, or an article id (resolved to its
source_url). --question/-q runs one question and exits; with no -q it drops into
an interactive REPL (blank line or Ctrl-D to quit). --no-ground-truth replays the
transcript verbatim and just appends your question, with nothing challenging it.

Read-only against Cassandra. Each question is one billed Mistral call.

IMPORTANT: an LLM cannot truly introspect its past reasoning — under pressure it
rationalises or capitulates. Treat its answers as leads to verify against the
trace and live sources, not as verdicts.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.ai.interrogate import RevivedSession

logger = logging.getLogger(__name__)


def _print_header(rev: RevivedSession) -> None:
    logger.info("=" * 78)
    logger.info("revived compose session for: %s", rev.source_url)
    logger.info(
        "  service=%s  model=%s  status=%s  rounds=%s  tool_calls=%s",
        rev.service_id,
        rev.model,
        rev.status,
        rev.rounds,
        rev.tool_calls,
    )
    logger.info(
        "  composed_at=%s  transcript_msgs=%d (flattened to %d replay turns)",
        rev.created_at,
        len(rev.messages),
        len(rev.replay),
    )
    logger.info("=" * 78)


def _revive_target(target: str, *, session_id: str) -> RevivedSession:
    """Revive the compose session for `target` (an article id or source_url substring), falling back to a plain source_url match if an article-id lookup misses. Exits the process if nothing matches."""
    from app.modules.ai.interrogate import _looks_like_uuid, revive_session

    kwargs: dict = {}
    if session_id:
        kwargs["session_id"] = session_id
    if _looks_like_uuid(target):
        # Could be an article id OR (rarely) a session id substring; try article
        # first, fall back to source_url substring match.
        kwargs["article_id"] = target
    else:
        kwargs["source_url"] = target

    try:
        return revive_session(**kwargs)
    except LookupError:
        # article-id path missed → try it as a plain source_url substring
        if "article_id" in kwargs and not session_id:
            try:
                return revive_session(source_url=target)
            except LookupError as exc:
                logger.error("%s", exc)
                sys.exit(1)
        logger.error("no matching compose session")
        sys.exit(1)


def _print_ground_truth(rev: RevivedSession, *, ground_truth: bool) -> None:
    if not ground_truth:
        return
    from app.modules.ai.interrogate import ground_truth_note

    note = ground_truth_note(rev)
    if note:
        logger.info("\n%s\n", note)
    else:
        logger.info(
            "\n(no ground-truth flags: every linked domain resolves and no "
            "fetch failures in the transcript)\n"
        )


def _run_repl(rev: RevivedSession, *, ground_truth: bool) -> None:
    from app.modules.ai.interrogate import interrogate

    history: list[dict] = []

    def ask(q: str) -> None:
        nonlocal history
        answer, history = interrogate(rev, q, ground_truth=ground_truth, history=history)
        logger.info("\nwriter> %s\n", answer)

    logger.info("Interactive interrogation. Blank line or Ctrl-D to quit.\n")
    while True:
        try:
            q = input("you> ").strip()
        except EOFError:
            logger.info("")
            break
        if not q:
            break
        ask(q)


def main() -> None:
    """Parse CLI args, revive the target compose session, and run the Q&A loop."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="source_url substring, or an article id")
    parser.add_argument(
        "-q", "--question", default="", help="ask one question and exit (otherwise interactive)"
    )
    parser.add_argument(
        "--no-ground-truth",
        action="store_true",
        help="replay verbatim; don't confront with live DNS / fetch-failure facts",
    )
    parser.add_argument(
        "--session-id", default="", help="pin an exact session_id instead of newest-match"
    )
    args = parser.parse_args()

    rev = _revive_target(args.target.strip(), session_id=args.session_id)
    _print_header(rev)

    ground_truth = not args.no_ground_truth
    _print_ground_truth(rev, ground_truth=ground_truth)

    if args.question:
        from app.modules.ai.interrogate import interrogate

        answer, _history = interrogate(rev, args.question, ground_truth=ground_truth, history=[])
        logger.info("\nwriter> %s\n", answer)
        return

    _run_repl(rev, ground_truth=ground_truth)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()

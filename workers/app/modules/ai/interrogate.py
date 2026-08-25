"""Revive a stored compose session and interrogate the writer about it.

Every article's compose run is persisted to ``compose_sessions`` with the full
transcript the writer model saw (system prompt + research + every tool result)
and the model id it ran on. This module "revives" that run: it replays the
transcript back into the same model and lets a human ask follow-up questions —
"where did the 1,000-issuers figure come from?", "you fetched myalgo.com and got
a DNS failure, why recommend it anyway?".

Honest caveat baked into the framing: an LLM has no introspective access to its
past reasoning; asked "why did you do X" it will *rationalise* a plausible
answer, not read out its weights. The value is in confronting it with fresh
ground truth (live DNS on the domains it linked, an index of the fetch failures
already in its own transcript) so it must reconcile its output against reality
instead of confidently re-defending it. That is what ``ground_truth=True`` does.

Strictly read-only against Cassandra: this reads compose_sessions, never writes.
The only outbound effect is LLM chat calls (billed like any compose call).

The transcript is *flattened* before replay rather than replayed verbatim: the
stored messages carry the tool-call/tool-result protocol (assistant.tool_calls +
tool-role replies keyed by id), and the persisted copy may have been trimmed to
fit the context window mid-compose, which can break that id pairing and 400 the
API. Flattening tool calls/results into plain narrated turns preserves
everything the model saw while sidestepping the fragile pairing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.celery_app import celery_app
from app.modules.newspaper import defunct_entity_gate as _dg

if TYPE_CHECKING:
    from cassandra.cluster import Session as CassandraSession

    from app.modules.ai.llm_openai_compatible import MistralProvider

logger = logging.getLogger(__name__)

# Real month buckets (see algorand_shared.feed_bucket) since the 2026-08-24
# TTL/bucketing cutover -- "all" is the legacy pre-cutover partition, scanned
# last/permanently. Read a whole partition at a time and filter in python:
# each bucket is clustered by created_at DESC, so within one bucket the first
# row matching is the newest compose for it. _RECENT_MONTHS candidate buckets
# are visited NEWEST FIRST (current month ... N months back ... "all" last)
# so the very first match found across the whole scan is guaranteed to be the
# overall newest, without needing to collect and sort every candidate.
_RECENT_MONTHS = 3
_SELECT_ALL = (
    "SELECT created_at, session_id, service_id, source_url, model, status, "
    "rounds, tool_calls, messages, final_output "
    "FROM algorand_platform.compose_sessions WHERE bucket = %s"
)

# A single flattened tool result can be tens of KB; cap each so a full replay
# stays comfortably inside even the smaller models' context windows. The head of
# a tool result carries the substantive fetched content.
_TOOL_RESULT_CAP = 6000
_MSG_CAP = 12000

_SYSTEM_FRAMING = (
    "You are being shown the COMPLETE transcript of an article you composed "
    "earlier: the instructions you were given, every research tool call you "
    "made, and every result those tools returned. This was the ENTIRETY of your "
    "evidence — you had no other sources.\n\n"
    "A human editor is now reviewing that article and will ask you about it. "
    "Answer plainly and honestly. If a statement in your draft was NOT supported "
    "by a tool result in this transcript, say so directly — do not invent a "
    "source or justify it after the fact. It is more useful to admit "
    "'that figure was not in my sources' than to defend it."
)


@dataclass
class RevivedSession:
    """A compose session's transcript, replayed and ready for interrogation."""

    source_url: str
    service_id: str
    model: str
    status: str
    rounds: int
    tool_calls: int
    created_at: Any
    session_id: Any
    messages: list[dict[str, Any]]
    final_output: str
    replay: list[dict[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #
def _looks_like_uuid(s: str) -> bool:
    parts = s.split("-")
    return len(parts) == 5 and all(all(c in "0123456789abcdef" for c in p.lower()) for p in parts)


def _resolve_article_source_url(session: CassandraSession, article_id: str) -> str | None:
    """Map an article id to the source_url its compose session was keyed on. 2026-08-24: reads `articles` directly (was `articles_by_id`)."""
    from uuid import UUID

    from algorand_shared.article_statements import ArticlesStmts

    try:
        aid = UUID(article_id)
    except ValueError:
        return None
    row = session.execute(ArticlesStmts.GET_FULL_BY_ID, (aid,)).one()
    return getattr(row, "source_url", None) if row else None


def _find_newest_matching_session(
    session: CassandraSession, *, needle: str, session_id: Any | None  # noqa: ANN401
) -> Any:  # noqa: ANN401 -- raw Cassandra driver row or None
    """Scan buckets newest-first ("all" legacy partition last) for the newest row matching session_id (exact) or needle (source_url substring). Each bucket is itself created_at DESC, so the first match found is the overall newest."""
    from datetime import UTC, datetime

    from algorand_shared.feed_bucket import months_back

    buckets = [*months_back(datetime.now(tz=UTC), _RECENT_MONTHS), "all"]
    for bucket in buckets:
        for row in session.execute(_SELECT_ALL, (bucket,)):
            if session_id is not None:
                if str(row.session_id) == str(session_id):
                    return row
                continue
            if needle and needle in (row.source_url or "").lower():
                return row
    return None


def revive_session(
    *,
    source_url: str | None = None,
    article_id: str | None = None,
    session_id: Any | None = None,  # noqa: ANN401 -- raw Cassandra driver UUID value, passed through unmodified
    _session: CassandraSession | None = None,
) -> RevivedSession:
    """Load the newest compose session matching a source_url substring (or the session behind an article id, or an exact session_id) and prepare it for interrogation. Raises LookupError if nothing matches."""
    session = _session
    if session is None:
        from app.core.cassandra import get_cassandra_session

        session = get_cassandra_session()

    if article_id and not source_url:
        source_url = _resolve_article_source_url(session, article_id)
        if not source_url:
            raise LookupError(f"no article {article_id!r} (or it has no source_url)")

    needle = (source_url or "").lower()
    chosen = _find_newest_matching_session(session, needle=needle, session_id=session_id)
    if chosen is None:
        raise LookupError(f"no compose session matching {source_url or session_id!r}")

    try:
        messages = json.loads(chosen.messages) if chosen.messages else []
    except (TypeError, ValueError):
        messages = []

    rev = RevivedSession(
        source_url=chosen.source_url or "",
        service_id=chosen.service_id or "",
        model=chosen.model or "",
        status=chosen.status or "",
        rounds=int(chosen.rounds or 0),
        tool_calls=int(chosen.tool_calls or 0),
        created_at=chosen.created_at,
        session_id=chosen.session_id,
        messages=messages,
        final_output=chosen.final_output or "",
    )
    rev.replay = _flatten(messages)
    return rev


# --------------------------------------------------------------------------- #
# transcript flattening
# --------------------------------------------------------------------------- #
def _text_of(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content)
    return "" if content is None else str(content)


def _flatten(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Collapse the tool-call protocol transcript into plain narrated turns the chat API will accept unconditionally (system/user/assistant only, no tool_call ids to keep paired). Everything the model saw is preserved as readable text; oversized tool results are head-truncated."""
    out: list[dict[str, str]] = []
    for m in messages:
        role = m.get("role", "user")
        text = _text_of(m).strip()
        if role == "tool":
            name = m.get("name") or "tool"
            body = text[:_TOOL_RESULT_CAP]
            if len(text) > _TOOL_RESULT_CAP:
                body += "\n…[tool result truncated]"
            out.append({"role": "user", "content": f"[RESULT of tool `{name}`]\n{body}"})
            continue
        if role == "assistant":
            calls = m.get("tool_calls") or []
            narrated = []
            for c in calls:
                fn = (c.get("function") or {}) if isinstance(c, dict) else {}
                nm = fn.get("name", "?")
                args = fn.get("arguments", "")
                if isinstance(args, (dict, list)):
                    args = json.dumps(args)
                narrated.append(f"[you called tool `{nm}`({str(args)[:400]})]")
            joined = "\n".join(filter(None, [text, *narrated]))
            if joined:
                out.append({"role": "assistant", "content": joined[:_MSG_CAP]})
            continue
        # system / user
        if text:
            out.append(
                {"role": role if role in ("system", "user") else "user", "content": text[:_MSG_CAP]}
            )
    return out


# --------------------------------------------------------------------------- #
# ground truth
# --------------------------------------------------------------------------- #
def ground_truth_note(rev: RevivedSession) -> str | None:
    """Build an evidence-only note confronting the writer with current reality: a live DNS status for every domain it linked in the final draft, plus an index of the fetch failures already present in its own transcript.

    Returns None if there is nothing notable to confront it with.
    """
    lines: list[str] = []

    # live DNS on every linked domain of the FINAL body
    hosts = _dg._linked_hosts(rev.final_output)
    if hosts:
        dns_lines = []
        for h in hosts[: _dg._MAX_DNS_CHECKS]:
            alive = _dg._resolves(h)
            dns_lines.append(f"  - {h}: {'resolves' if alive else 'DOES NOT RESOLVE (no address)'}")
        dead = [ln for ln in dns_lines if "DOES NOT" in ln]
        if dead:
            lines.append("Live DNS check, just now, on the domains your article links:")
            lines.extend(dns_lines)

    # fetch failures already in the transcript
    failed = sorted(_dg._dns_failed_hosts(rev.messages))
    if failed:
        lines.append(
            "Your own research in this transcript recorded DNS/fetch failures for: "
            + ", ".join(failed)
        )

    if not lines:
        return None
    return "GROUND-TRUTH CHECK (facts, verified independently of your transcript):\n" + "\n".join(
        lines
    )


# --------------------------------------------------------------------------- #
# interrogation
# --------------------------------------------------------------------------- #
def interrogate(
    rev: RevivedSession,
    question: str,
    *,
    ground_truth: bool = True,
    history: list[dict[str, str]] | None = None,
    client: MistralProvider | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """Ask the revived writer one question and return (answer, updated_history).

    ``history`` carries the running interrogation Q&A across turns so a REPL can
    keep the thread; pass the returned history back on the next call. The heavy
    replayed transcript is prepended fresh each call and is NOT stored in history
    (it does not change), so history stays small.
    """
    if client is None:
        # Interrogate on the SAME model that composed, so the answers come from
        # the same weights that produced the draft. Fall back to the writer model
        # if the stored session predates model capture.
        from app.core.config import MISTRAL_MODEL_WRITER
        from app.modules.ai.llm_openai_compatible import MistralProvider

        client = MistralProvider(model=rev.model or MISTRAL_MODEL_WRITER)

    convo: list[dict[str, str]] = [{"role": "system", "content": _SYSTEM_FRAMING}]
    convo.extend(rev.replay)
    if history:
        convo.extend(history)
    if ground_truth:
        note = ground_truth_note(rev)
        if note:
            convo.append({"role": "user", "content": note})
    convo.append({"role": "user", "content": question})

    answer = client.chat_completion(convo, json_object=False, temperature=0.3)
    answer = (answer or "").strip()

    new_history = list(history or [])
    # Record the ground-truth note in history too, so a follow-up question keeps
    # the confrontation in context without re-running the DNS checks every turn.
    if ground_truth:
        note = ground_truth_note(rev)
        if note and not any(h.get("content") == note for h in new_history):
            new_history.append({"role": "user", "content": note})
    new_history.append({"role": "user", "content": question})
    new_history.append({"role": "assistant", "content": answer})
    return answer, new_history


# --------------------------------------------------------------------------- #
# admin UI entry point (2026-08-05)
# --------------------------------------------------------------------------- #
# The admin backend is a separate service/codebase from workers (only
# connected via Celery/Redis + shared Cassandra), so it cannot import this
# module directly. Unlike a compose (which can run 20+ minutes and must be
# fire-and-forget), one interrogation turn is a single bounded chat_completion
# call, so the backend route dispatches this task and waits on it directly
# rather than polling — same reasoning as admin_compose_next's short .get(),
# just with a longer timeout sized for one LLM call instead of a cheap read.
@celery_app.task(name="app.tasks.newspaper.interrogate_compose_session")
def interrogate_compose_session_task(
    *,
    target: str,
    question: str,
    ground_truth: bool = True,
    history: list[dict[str, str]] | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    """Revive a compose session (by source_url substring, article id, or exact session_id) and ask it one question. Returns {ok: True, answer, history, header} or {ok: False, error}."""
    kwargs: dict[str, Any] = {}
    if session_id:
        kwargs["session_id"] = session_id
    elif _looks_like_uuid(target):
        kwargs["article_id"] = target
    else:
        kwargs["source_url"] = target

    try:
        rev = revive_session(**kwargs)
    except LookupError as exc:
        if "article_id" in kwargs and not session_id:
            try:
                rev = revive_session(source_url=target)
            except LookupError as exc2:
                return {"ok": False, "error": str(exc2)}
        else:
            return {"ok": False, "error": str(exc)}

    try:
        answer, new_history = interrogate(
            rev, question, ground_truth=ground_truth, history=history or []
        )
    except Exception as exc:
        logger.warning("interrogation call failed for %s", rev.source_url, exc_info=True)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "ok": True,
        "answer": answer,
        "history": new_history,
        "header": {
            "source_url": rev.source_url,
            "service_id": rev.service_id,
            "model": rev.model,
            "status": rev.status,
            "rounds": rev.rounds,
            "tool_calls": rev.tool_calls,
            "session_id": str(rev.session_id),
        },
    }

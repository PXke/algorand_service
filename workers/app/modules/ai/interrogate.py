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
The only outbound effect is Mistral chat calls (billed like any compose call).

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
from typing import Any

from app.modules.newspaper import defunct_entity_gate as _dg

logger = logging.getLogger(__name__)

_BUCKET = "all"
# Read the whole partition and filter in python: compose_sessions is a single
# 'all' bucket clustered by created_at DESC (7-day TTL, never large), so the
# first row matching a source_url is the newest compose for it.
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


def _resolve_article_source_url(session, article_id: str) -> str | None:
    """Map an article id to the source_url its compose session was keyed on."""
    from app.core.statements import ArticleStmts

    row = session.execute(ArticleStmts.GET_IMAGE_META, (article_id,)).one()
    return getattr(row, "source_url", None) if row else None


def revive_session(
    *,
    source_url: str | None = None,
    article_id: str | None = None,
    session_id: Any | None = None,
    _session=None,
) -> RevivedSession:
    """Load the newest compose session matching a source_url substring (or the
    session behind an article id, or an exact session_id) and prepare it for
    interrogation. Raises LookupError if nothing matches."""
    session = _session
    if session is None:
        from app.core.cassandra import get_cassandra_session

        session = get_cassandra_session()

    if article_id and not source_url:
        source_url = _resolve_article_source_url(session, article_id)
        if not source_url:
            raise LookupError(f"no article {article_id!r} (or it has no source_url)")

    needle = (source_url or "").lower()
    rows = session.execute(_SELECT_ALL, (_BUCKET,))
    chosen = None
    for row in rows:  # newest-first
        if session_id is not None:
            if str(row.session_id) == str(session_id):
                chosen = row
                break
            continue
        if needle and needle in (row.source_url or "").lower():
            chosen = row  # first (newest) match wins
            break
    if chosen is None:
        raise LookupError(
            f"no compose session matching {source_url or session_id!r}"
        )

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
        return "".join(
            str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content
        )
    return "" if content is None else str(content)


def _flatten(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Collapse the tool-call protocol transcript into plain narrated turns the
    chat API will accept unconditionally (system/user/assistant only, no
    tool_call ids to keep paired). Everything the model saw is preserved as
    readable text; oversized tool results are head-truncated."""
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
            out.append({"role": role if role in ("system", "user") else "user",
                        "content": text[:_MSG_CAP]})
    return out


# --------------------------------------------------------------------------- #
# ground truth
# --------------------------------------------------------------------------- #
def ground_truth_note(rev: RevivedSession) -> str | None:
    """Build an evidence-only note confronting the writer with current reality:
    a live DNS status for every domain it linked in the final draft, plus an
    index of the fetch failures already present in its own transcript. Returns
    None if there is nothing notable to confront it with."""
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
            lines.append(
                "Live DNS check, just now, on the domains your article links:")
            lines.extend(dns_lines)

    # fetch failures already in the transcript
    failed = sorted(_dg._dns_failed_hosts(rev.messages))
    if failed:
        lines.append(
            "Your own research in this transcript recorded DNS/fetch failures for: "
            + ", ".join(failed))

    if not lines:
        return None
    return (
        "GROUND-TRUTH CHECK (facts, verified independently of your transcript):\n"
        + "\n".join(lines)
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
    client=None,
) -> tuple[str, list[dict[str, str]]]:
    """Ask the revived writer one question and return (answer, updated_history).

    ``history`` carries the running interrogation Q&A across turns so a REPL can
    keep the thread; pass the returned history back on the next call. The heavy
    replayed transcript is prepended fresh each call and is NOT stored in history
    (it does not change), so history stays small.
    """
    if client is None:
        from app.modules.ai.mistral_client import MistralClient

        # Interrogate on the SAME model that composed, so the answers come from
        # the same weights that produced the draft. Fall back to the writer model
        # if the stored session predates model capture.
        from app.core.config import MISTRAL_MODEL_WRITER

        client = MistralClient(model=rev.model or MISTRAL_MODEL_WRITER)

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

"""Admin-only, grounded blurb-draft helper (design doc section 6.3).

Grounded ONLY in text this crawler has already harvested for the entry's
domain (`crawled_pages_by_domain`/`crawled_pages_by_id`, the same tables
`newspaper/service_context.py` aggregates from) -- never invented, never
fetched fresh for this call. One short, cheap translate-tier LLM call, the
same shape as `newspaper/glossary_translate.py`'s single JSON-object call.
Writes ONLY `draft_description` (never `description`) -- the newspaper's own
fabrication incidents (CLAUDE.md's audit history) are exactly why this never
auto-publishes; an admin reviews and edits every draft before it goes live.
"""

from __future__ import annotations

import logging
import time

from app.modules.ai.llm_openai_compatible import MistralProvider
from app.modules.ai.llm_purpose_router import get_llm_translate_client

logger = logging.getLogger(__name__)

_MAX_GROUNDING_CHARS = 4000
_MAX_PAGES = 5


def _grounding_text(domain: str) -> str:
    """Newest few crawled pages already harvested for `domain`, title + a text snippet, joined and capped."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import CrawledPageStmts

    session = get_cassandra_session()
    rows = list(session.execute(CrawledPageStmts.LIST_BY_DOMAIN, (domain, _MAX_PAGES)))
    if not rows:
        # www-stripped domains are stored under the exact host they were
        # crawled on (same reasoning ecosystem_sync/service_context both
        # note) -- one retry with the www. twin before giving up.
        rows = list(session.execute(CrawledPageStmts.LIST_BY_DOMAIN, (f"www.{domain}", _MAX_PAGES)))

    chunks: list[str] = []
    budget = _MAX_GROUNDING_CHARS
    for row in rows:
        if budget <= 0:
            break
        body_row = session.execute(CrawledPageStmts.GET_BODY, (row.page_id,)).one()
        if body_row is None:
            continue
        title = (body_row.title or "").strip()
        body = (body_row.body or "").strip()
        snippet = f"{title}\n{body}"[:budget]
        if snippet.strip():
            chunks.append(snippet)
            budget -= len(snippet)
    return "\n\n---\n\n".join(chunks)


def _record_blurb_session(
    llm: MistralProvider, *, slug: str, status: str, duration_ms: int
) -> None:
    """Best-effort compose_sessions row, same accounting mechanism (and same "never raises" contract) as glossary_translate.py's own _record_glossary_translate_session -- see that function's docstring for why this never accounts against an article's own compose session."""
    try:
        from app.modules.ai.session_register import SessionRegisterCassandra

        register = SessionRegisterCassandra()
        session_id, created_at = register.new_ref()
        usage = llm.usage_totals()
        register.upsert(
            debug={"messages": []},
            trace=[],
            service_id=f"ecosystem_blurb:{slug[:200]}",
            source_url="",
            model=llm.model,
            final_output="",
            status=status,
            duration_ms=duration_ms,
            session_id=session_id,
            created_at=created_at,
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            total_tokens=usage["total_tokens"],
            cached_tokens=usage["cached_tokens"],
        )
    except Exception:
        logger.warning("failed to record ecosystem-blurb session for %s", slug, exc_info=True)


def draft_blurb(*, slug: str, url: str, domain: str, client: MistralProvider | None = None) -> str:
    """Return a suggested one-sentence description grounded in already-crawled homepage text, or "" if nothing was harvested yet for this domain."""
    grounding = _grounding_text(domain)
    if not grounding.strip():
        return ""

    llm = client or get_llm_translate_client()
    system = (
        "You write ONE factual, plain sentence (20-200 characters) describing an Algorand "
        "ecosystem project for a directory listing, grounded ONLY in the crawled page text "
        "given to you. No marketing language ('revolutionary', 'the first', 'best'). If the "
        "text does not clearly describe what the project does, say so plainly rather than "
        "guessing."
    )
    user = (
        f"Project url: {url}\n\nCrawled page text (already harvested, do not invent beyond it):\n"
        f'{grounding}\n\nRespond as JSON: {{"description": "..."}}'
    )
    t0 = time.monotonic()
    status = "ok"
    try:
        parsed = llm.chat_json_object(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.2,
            max_tokens=200,
        )
        return str(parsed.get("description", "")).strip()
    except Exception:
        status = "error"
        raise
    finally:
        _record_blurb_session(
            llm, slug=slug, status=status, duration_ms=int((time.monotonic() - t0) * 1000)
        )


__all__ = ["draft_blurb"]

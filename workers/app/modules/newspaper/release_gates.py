"""Release-time re-gating for backlog articles — closes the time capsule.

Articles in pending_feed_queue were composed and stored (unlisted) at some
earlier time, then released to the feed days later by the paced backlog
drain. Every safeguard added BETWEEN compose and release used to be silently
bypassed: in the week of 2026-07-14 alone, two such articles (the
UNDP/Stellar piece and the quantum-rebrand piece) reached or nearly reached
readers carrying defect classes that compose-time gates built days after
their compose would have caught.

This hook runs the current body-only, self-healing gates against the stored
article at the moment of release, persisting any correction before the feed
row is inserted. Only gates that need no research trace can run here (the
trace is not retrievable at release time in general): today that is the
authority gate (excises "industry research suggests"-style sentences). Add
future body-only gates in _BODY_GATES below and they retroactively cover
everything still queued.

Fail-open by design: a crash in a gate must never block a release — the
article was already approved; the gates are hardening, not approval.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Each gate takes the payload dict ({"body": ...}), mutates it in place if it
# finds anything, and records what it did under a payload["_<name>"] key —
# the same contract these gates already honor on the compose path.
_BODY_GATES: tuple[tuple[str, str, str], ...] = (
    # (module, function, audit-note key the gate writes)
    ("app.modules.newspaper.authority_gate", "excise_unattributed_authority", "_authority_removed"),
)


def apply_release_gates(article_id: str) -> dict[str, Any]:
    """Run body-only self-healing gates on a stored article at release time.

    Persists a corrected body (with before/after versions) when a gate fires.
    Returns {"changed": bool, "notes": {gate_key: removals}}; never raises.
    """
    result: dict[str, Any] = {"changed": False, "notes": {}}
    try:
        from app.modules.newspaper.article_store import get_article

        art = get_article(article_id)
        if art is None or not art.body:
            return result

        payload: dict[str, Any] = {"body": art.body}
        for module_name, func_name, note_key in _BODY_GATES:
            try:
                module = __import__(module_name, fromlist=[func_name])
                gate: Callable[[dict], dict] = getattr(module, func_name)
                gate(payload)
                if payload.get(note_key):
                    result["notes"][note_key] = payload[note_key]
            except Exception:
                logger.warning(
                    "release gate %s.%s failed for %s (fail-open)",
                    module_name, func_name, article_id, exc_info=True,
                )

        new_body = payload.get("body")
        if not isinstance(new_body, str) or new_body == art.body:
            return result

        from datetime import UTC, datetime
        from uuid import UUID

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ArticleStmts
        from app.modules.newspaper.article_version_store import save_article_version

        save_article_version(
            article_id=article_id, title=art.title, summary=art.summary,
            body=art.body, edit_reason="before_edit", editor="release_gate",
        )
        # Raw UPDATE, deliberately NOT update_article(): the article is still
        # unlisted at this instant — update_article would upsert a feed row
        # and publish it out-of-band; the caller inserts the real feed row
        # right after this returns.
        get_cassandra_session().execute(
            ArticleStmts.UPDATE,
            (art.title, art.summary, new_body, list(art.tags or []),
             datetime.now(tz=UTC), UUID(article_id)),
        )
        save_article_version(
            article_id=article_id, title=art.title, summary=art.summary,
            body=new_body,
            edit_reason="release_gate:" + ",".join(result["notes"]) if result["notes"]
            else "release_gate",
            editor="release_gate",
        )
        try:
            from app.modules.search.tasks.index_tasks import index_article

            index_article.delay(
                article_id=article_id, title=art.title, summary=art.summary,
                body=new_body, service_id=art.service_id,
                published_at_epoch=art.published_at_epoch or 0,
            )
        except Exception:
            pass
        result["changed"] = True
        logger.warning(
            "release gates corrected article %s before feed release: %s",
            article_id, {k: len(v) for k, v in result["notes"].items()},
        )
    except Exception:
        logger.warning("apply_release_gates failed for %s (fail-open)", article_id, exc_info=True)
    return result

"""Admin-only, grounded blurb-draft helper (design doc section 6.3): fires a Celery task to workers, never runs the LLM call in-process.

Backend has no LLM client of its own (see glossary's own
enqueue_glossary_term_translations, the precedent this mirrors) -- workers
owns the DeepSeek client, the crawled-page grounding data, and the compose-
session accounting this needs. Never auto-publishes: the task only ever
writes `draft_description`, a field distinct from `description`, that an
admin reads in the review queue and copies in (or edits) through the
ordinary approve/edit path.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def request_blurb_draft(slug: str, *, url: str, domain: str) -> bool:
    """Fire-and-forget: ask workers to draft a grounded one-sentence blurb for `slug`. Returns whether the task was enqueued."""
    try:
        from celery import Celery

        from app.core.config import settings

        Celery(broker=settings.celery_broker_url).send_task(
            "app.tasks.ecosystem_probe.draft_ecosystem_blurb",
            args=[slug],
            kwargs={"url": url, "domain": domain},
        )
        return True
    except Exception:
        logger.warning("ecosystem: failed to enqueue blurb draft for %s", slug, exc_info=True)
        return False


__all__ = ["request_blurb_draft"]

"""Apply an in-place edit to an already-published article."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from app.core.config import mistral_configured
from app.modules.newspaper.article_store import get_article, update_article
from app.modules.newspaper.article_tags import derive_article_tags
from app.modules.newspaper.article_version_store import save_article_version
from app.modules.newspaper.compose_lock import ComposeBusyError
from app.modules.newspaper.publish_policy import PublishTopic
from app.modules.newspaper.publish_queue_store import QueuedPublishRow
from app.modules.newspaper.security import sanitize_body
from app.modules.search.tasks.index_tasks import index_article

logger = logging.getLogger(__name__)


def run_article_edit(row: QueuedPublishRow) -> dict[str, str]:
    """Apply follow-up ingest to an existing article (within edit window).

    Saves prior body to article_versions, then updates live article.
    """
    from app.modules.ai.mistral_client import MistralCreditError, MistralError

    payload = row.payload
    linked_id = str(payload.get("linked_article_id", "")).strip()
    if not linked_id:
        return {"status": "skipped", "reason": "missing_linked_article_id"}

    existing = get_article(linked_id)
    if existing is None:
        # Permanent: the linked article was deleted after this row was queued.
        # queue_status retires the row (via _resolve) instead of leaving it
        # pending — a pending row here redrained every breaking beat forever
        # AND starved the service's one-pending-row slot (audit 2026-07-17).
        return {
            "status": "skipped",
            "reason": "article_not_found",
            "queue_status": "expired",
            "linked_article_id": linked_id,
        }

    try:
        topic = PublishTopic(row.topic)
    except ValueError:
        topic = PublishTopic.GENERIC

    enrichment_block = ""
    try:
        from app.core import config as worker_config
        from app.modules.newspaper.writer_enrichment import (
            format_enrichment_for_writer,
            gather_writer_enrichment,
        )

        if worker_config.WRITER_ENRICHMENT_ENABLED:
            bundle = gather_writer_enrichment(
                service_id=row.service_id,
                display_name=row.display_name,
                source_url=row.scrape_url,
                page_text=str(payload.get("page_text", "")),
                page_title=str(payload.get("page_title", "")),
                diff=payload.get("diff"),
                is_first_snapshot=False,
                publish_topic=topic,
                match_kind=str(payload.get("match_kind", "")),
                match_value=str(payload.get("match_value", "")),
            )
            enrichment_block = format_enrichment_for_writer(bundle)
    except Exception:
        enrichment_block = ""

    new_text = str(payload.get("page_text", ""))
    new_title = str(payload.get("page_title", ""))

    try:
        # No template fallback exists (owner decision 2026-07-14: a lesser,
        # robotic article is worse than no article) — Mistral or nothing.
        if not mistral_configured():
            raise MistralError(
                "MISTRAL_ENABLED and MISTRAL_API_KEY required — no template fallback"
            )
        from app.modules.ai.mistral_compose import compose_article_edit_mistral

        fields = compose_article_edit_mistral(
            service_name=row.display_name or existing.service_id,
            source_url=row.scrape_url,
            existing_title=existing.title,
            existing_summary=existing.summary,
            existing_body=existing.body,
            new_page_title=new_title,
            new_page_text=new_text,
            diff=payload.get("diff"),
            enrichment_block=enrichment_block,
        )
        title, summary, body, composer = fields.title, fields.summary, fields.body, "mistral"
    except ComposeBusyError:
        raise
    except MistralError as exc:
        credit_issue = isinstance(exc, MistralCreditError)
        status = "mistral_credit_insufficient" if credit_issue else "mistral_failed"
        logger.error("Mistral article-edit compose failed for %s: %s", linked_id, exc)
        return {
            "status": status,
            "linked_article_id": linked_id,
            "detail": str(exc),
        }

    body = sanitize_body(body)
    edit_reason = f"follow_up_ingest:{row.scrape_url[:120]}"

    save_article_version(
        article_id=linked_id,
        title=existing.title,
        summary=existing.summary,
        body=existing.body,
        edit_reason="before_edit",
        editor="system",
    )

    tags = derive_article_tags(
        service_id=existing.service_id,
        source_kind=None,
        title=title,
        publish_kind=row.publish_kind,
        publish_topic=topic.value,
        publish_tier=str(payload.get("tier", "breaking")),
    )
    if not update_article(
        article_id=linked_id,
        title=title,
        summary=summary,
        body=body,
        tags=tags,
    ):
        return {"status": "failed", "reason": "update_failed", "linked_article_id": linked_id}

    version = save_article_version(
        article_id=linked_id,
        title=title,
        summary=summary,
        body=body,
        edit_reason=edit_reason,
        editor=composer,
    )

    index_article.delay(
        article_id=linked_id,
        title=title,
        summary=summary,
        body=body,
        service_id=existing.service_id,
        published_at_epoch=existing.published_at_epoch or int(time.time()),
    )

    # The article changed at its existing URL — tell IndexNow (Bing guideline:
    # notify on update, not just add). Best-effort, never blocks the edit.
    try:
        from app.modules.newspaper.indexnow import ping_article, translation_lang_codes

        ping_article(
            linked_id,
            translation_langs=translation_lang_codes(existing.translations),
        )
    except Exception:
        pass

    from app.modules.newspaper.article_matching import (
        build_match_keys,
        edit_window_closes_at,
        register_article_match_keys,
    )

    keys = payload.get("match_keys")
    if not isinstance(keys, list) or not keys:
        keys = build_match_keys(
            service_id=row.service_id,
            page_text=new_text,
            source_url=row.scrape_url,
            extra_keywords=("scam",) if topic == PublishTopic.SCAM_ALERT else (),
            topic=topic.value,
            match_kind=str(payload.get("match_kind", "")),
            match_value=str(payload.get("match_value", "")),
        )
    else:
        keys = [
            (str(k[0]), str(k[1])) for k in keys if isinstance(k, (list, tuple)) and len(k) == 2
        ]
    # Anchor the re-registered keys' edit window to the article's ORIGINAL
    # publish time, never "now": the default (now + window) meant every edit
    # rolled the window forward another 24h — and since each edit also adds
    # the editing source's own keys, one stray match could keep an article
    # editable (and accumulating keys) indefinitely. That rolling window is
    # how the 2026-07-17 runaway kept re-opening itself; with this anchor the
    # window converges to publish + ARTICLE_EDIT_WINDOW_HOURS and closes,
    # matching what is_edit_window_open already enforces for explicit edits.
    published_at = None
    if existing.published_at_epoch:
        published_at = datetime.fromtimestamp(existing.published_at_epoch, tz=UTC)
    register_article_match_keys(
        article_id=linked_id,
        keys=keys,
        closes_at=edit_window_closes_at(from_time=published_at) if published_at else None,
    )

    return {
        "status": "edited",
        "article_id": linked_id,
        "version": str(version),
        "composer": composer,
        "publish_mode": "edit",
    }

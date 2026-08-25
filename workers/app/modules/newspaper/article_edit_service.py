"""Apply an in-place edit to an already-published article."""

from __future__ import annotations

import logging
import time

from app.core.config import mistral_configured
from app.modules.newspaper.article_store import get_article, update_article
from app.modules.newspaper.article_tags import derive_article_tags
from app.modules.newspaper.article_version_store import save_article_version
from app.modules.newspaper.compose_lock import ComposeBusyError
from app.modules.newspaper.publish_policy import PublishTopic
from app.modules.newspaper.publish_queue_store import QueuedPublishRow
from app.modules.newspaper.security import sanitize_body
from app.modules.newspaper.writer_enrichment import enrichment_block_for_row
from app.modules.search.tasks.index_tasks import index_article

logger = logging.getLogger(__name__)


def _compose_edit_fields(
    row: QueuedPublishRow,
    existing: object,
    payload: dict,
    *,
    new_title: str,
    new_text: str,
    enrichment_block: str,
    linked_id: str,
) -> tuple[object | None, dict[str, str] | None]:
    """Compose the edited title/summary/body via a FULL recompose -- the same research -> write -> grade/revise pipeline (and every deterministic gate) a fresh article gets, not a "preserve the old body verbatim + append an Updated section" patch. Returns (fields, None) on success, or (None, error_response) on a Mistral failure or the writer's own abort_article call. Raises ComposeBusyError, propagated so the caller (a Celery task) retries.

    Root-caused 2026-08-04 (Humanitarian Network special edition refresh):
    the old approach fed the existing body back into the prompt and told the
    model to preserve it, which is why editorial-brief refreshes (special
    editions included) read as padded restatements of the same facts rather
    than a genuine update -- and meant every pipeline improvement (research
    floor, stale-deadline gate, grading, revision) silently never touched an
    edited article's body. compose_scrape_article is the SAME router
    publish_tasks._compose_or_error calls for a brand-new article; the only
    difference here is first_coverage=False + a prior_coverage_block, same
    as any non-first content_update already gets.
    """
    from app.modules.ai.llm_provider import LLMCreditError, LLMError
    from app.modules.ai.story_spike import StorySpikedError
    from app.modules.newspaper.article_composer import compose_scrape_article
    from app.modules.newspaper.article_grader import prior_service_article_summary
    from app.modules.newspaper.publish_policy import PublishKind, PublishTopic

    try:
        # No template fallback exists (owner decision 2026-07-14: a lesser,
        # robotic article is worse than no article) — Mistral or nothing.
        if not mistral_configured():
            raise LLMError(
                "MISTRAL_ENABLED and MISTRAL_API_KEY required — no template fallback"
            )
        try:
            publish_kind = PublishKind(row.publish_kind)
        except ValueError:
            publish_kind = PublishKind.CONTENT_UPDATE
        try:
            topic = PublishTopic(row.topic)
        except ValueError:
            topic = PublishTopic.GENERIC

        fields = compose_scrape_article(
            service_name=row.display_name or existing.service_id,
            source_url=row.scrape_url,
            page_title=new_title,
            page_text=new_text,
            txid=str(payload.get("txid", "")),
            round_num=int(payload.get("round_num", 0)),
            diff=payload.get("diff"),
            is_first_snapshot=False,
            publish_kind=publish_kind,
            publish_topic=topic,
            enrichment_block=enrichment_block,
            transcript_text=str(payload.get("transcript_text", "")),
            source_links=payload.get("inner_links") or [],
            keywords=str(payload.get("keywords", "")),
            brief_id=str(payload.get("brief_id", "")),
            first_coverage=False,
            prior_coverage_block=prior_service_article_summary(row.service_id),
            is_special_edition=bool(payload.get("is_special_edition", False)),
        )
        # Not yet wired to a review-hold divert (unlike the create path's
        # _determine_review_divert) -- known gap, logged rather than silently
        # dropped so it's at least visible (see ArticleComposeResult's own
        # docstring: silently dropping these fields is exactly the 2026-07-17
        # MyAlgo/GoPlausible bug class).
        if fields.defunct_domains:
            logger.warning(
                "article edit for %s links unreachable domain(s): %s",
                linked_id,
                ", ".join(fields.defunct_domains),
            )
        if fields.unsourced_hold_reason:
            logger.warning(
                "article edit for %s has unsourced specifics: %s",
                linked_id,
                fields.unsourced_hold_reason,
            )
        if fields.broken_link_hold_reason:
            logger.warning(
                "article edit for %s has an unverified broken-link claim: %s",
                linked_id,
                fields.broken_link_hold_reason,
            )
        return fields, None
    except ComposeBusyError:
        raise
    except StorySpikedError as spike:
        # The writer refused to write this edit (abort_article) -- a real
        # research-backed judgment now that this is a full recompose, not
        # the crude preserve-and-append path that never called real tools.
        logger.info(
            "writer spiked article edit for %s (%s): %s",
            linked_id,
            spike.category,
            spike.reason,
        )
        return None, {
            "status": "aborted_by_writer",
            "reason": f"{spike.category}: {spike.reason}",
            "linked_article_id": linked_id,
        }
    except LLMError as exc:
        credit_issue = isinstance(exc, LLMCreditError)
        status = "mistral_credit_insufficient" if credit_issue else "mistral_failed"
        logger.error("LLM article-edit compose failed for %s: %s", linked_id, exc)
        return None, {"status": status, "linked_article_id": linked_id, "detail": str(exc)}


def _merge_extra_tags(tags: list[str], extra_tags: tuple[str, ...] | None) -> list[str]:
    """Append any compose-time extra tags (e.g. "special-edition") not already present, preserving order."""
    for extra_tag in extra_tags or ():
        if extra_tag not in tags:
            tags.append(extra_tag)
    return tags


def run_article_edit(row: QueuedPublishRow) -> dict[str, str]:
    """Apply follow-up ingest to an existing article (within edit window).

    Saves prior body to article_versions, then updates live article.
    """
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

    # An edit is never a first snapshot — the article already exists.
    enrichment_block = enrichment_block_for_row(row, payload, topic, is_first_snapshot=False)
    new_text = str(payload.get("page_text", ""))
    new_title = str(payload.get("page_title", ""))

    fields, error_response = _compose_edit_fields(
        row,
        existing,
        payload,
        new_title=new_title,
        new_text=new_text,
        enrichment_block=enrichment_block,
        linked_id=linked_id,
    )
    if error_response is not None:
        return error_response
    title, summary, body, composer = fields.title, fields.summary, fields.body, fields.composer

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
    tags = _merge_extra_tags(tags, getattr(fields, "extra_tags", ()))
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
            slug=existing.slug,
        )
    except Exception:
        pass

    return {
        "status": "edited",
        "article_id": linked_id,
        "version": str(version),
        "composer": composer,
        "publish_mode": "edit",
    }

from __future__ import annotations

import time

from app.modules.newspaper.article_edit_compose import compose_article_edit
from app.modules.newspaper.article_store import get_article, update_article
from app.modules.newspaper.article_tags import derive_article_tags
from app.modules.newspaper.article_version_store import save_article_version
from app.modules.newspaper.publish_policy import PublishTopic
from app.modules.newspaper.publish_queue_store import QueuedPublishRow
from app.modules.newspaper.security import sanitize_body
from app.modules.search.tasks.index_tasks import index_article


def run_article_edit(row: QueuedPublishRow) -> dict[str, str]:
    """
    Apply follow-up ingest to an existing article (within edit window).
    Saves prior body to article_versions, then updates live article.
    """
    from app.modules.ai.mistral_client import MistralError

    payload = row.payload
    linked_id = str(payload.get("linked_article_id", "")).strip()
    if not linked_id:
        return {"status": "skipped", "reason": "missing_linked_article_id"}

    existing = get_article(linked_id)
    if existing is None:
        return {"status": "skipped", "reason": "article_not_found", "linked_article_id": linked_id}

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
        title, summary, body, composer = compose_article_edit(
            existing=existing,
            new_page_text=new_text,
            new_page_title=new_title,
            source_url=row.scrape_url,
            diff=payload.get("diff"),
            enrichment_block=enrichment_block,
            service_name=row.display_name,
        )
    except MistralError as exc:
        return {
            "status": "mistral_failed",
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
        from app.modules.newspaper.indexnow import article_url, ping

        ping([article_url(linked_id)])
    except Exception:
        pass

    from app.modules.newspaper.article_matching import build_match_keys, register_article_match_keys

    keys = payload.get("match_keys")
    if not isinstance(keys, list) or not keys:
        keys = build_match_keys(
            service_id=row.service_id,
            page_text=new_text,
            source_url=row.scrape_url,
            extra_keywords=("scam",) if topic == PublishTopic.SCAM_ALERT else (),
            match_kind=str(payload.get("match_kind", "")),
            match_value=str(payload.get("match_value", "")),
        )
    else:
        keys = [
            (str(k[0]), str(k[1]))
            for k in keys
            if isinstance(k, (list, tuple)) and len(k) == 2
        ]
    register_article_match_keys(article_id=linked_id, keys=keys)

    return {
        "status": "edited",
        "article_id": linked_id,
        "version": str(version),
        "composer": composer,
        "publish_mode": "edit",
    }

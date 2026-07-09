from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core import config
from app.core.config import MISTRAL_FALLBACK_TEMPLATE, mistral_configured
from app.modules.ai.mistral_client import MistralError
from app.modules.newspaper.compose_lock import ComposeBusyError
from app.modules.ai.mistral_compose import (
    compose_assignment_article_mistral,
    compose_recap_from_transcript_mistral,
    compose_scrape_article_mistral,
    compose_weekly_digest_article_mistral,
    compose_weekly_price_article_mistral,
)
from app.modules.newspaper.article_compose import compose_article
from app.modules.newspaper.community_recap_compose import compose_community_recap_article
from app.modules.newspaper.content_update_compose import (
    compose_content_update_article,
    compose_scam_alert_article,
)
from app.modules.newspaper.price_analysis import WeeklyPriceSnapshot, compose_weekly_price_article
from app.modules.newspaper.publish_policy import PublishKind, PublishTopic, trim_text_to_chars
from app.modules.newspaper.service_discovery_compose import compose_service_discovery_article
from app.modules.newspaper.weekly_digest import WeeklyDigestContext, compose_weekly_digest_article

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArticleComposeResult:
    title: str
    summary: str
    body: str
    composer: str
    publish_kind: str = ""
    extra_tags: tuple[str, ...] = ()
    prompt_version: str = ""


def compose_scrape_article(
    *,
    service_name: str,
    source_url: str,
    page_title: str,
    page_text: str,
    txid: str,
    round_num: int,
    diff: str | None,
    is_first_snapshot: bool,
    publish_kind: PublishKind,
    publish_topic: PublishTopic | None = None,
    mistral_only: bool = False,
    enrichment_block: str = "",
    transcript_text: str = "",
    source_links: list[dict[str, str]] | None = None,
    keywords: str = "",
    brief_id: str = "",
    first_coverage: bool = False,
) -> ArticleComposeResult:
    """Compose by publish kind (discovery vs update) with optional Mistral."""
    topic = publish_topic or PublishTopic.GENERIC

    if topic == PublishTopic.EDITORIAL_ASSIGNMENT and mistral_configured():
        try:
            fields = compose_assignment_article_mistral(
                brief_title=page_title,
                brief_body=page_text,
                keywords=keywords,
                brief_id=brief_id or source_url,
            )
            return ArticleComposeResult(
                title=fields.title,
                summary=fields.summary,
                body=fields.body,
                composer="mistral_assignment",
                publish_kind=publish_kind.value,
                extra_tags=getattr(fields, "tags", ()),
                prompt_version=getattr(fields, "prompt_version", ""),
            )
        except MistralError as exc:
            logger.warning("Editorial assignment compose failed: %s", exc)
            raise

    if topic == PublishTopic.COMMUNITY_RECAP and transcript_text and mistral_configured():
        try:
            fields = compose_recap_from_transcript_mistral(
                service_name=service_name,
                source_url=source_url,
                page_title=page_title,
                transcript_text=transcript_text,
            )
            return ArticleComposeResult(
                title=fields.title,
                summary=fields.summary,
                body=fields.body,
                composer="mistral_transcript",
                publish_kind=publish_kind.value,
                extra_tags=getattr(fields, "tags", ()),
                prompt_version=getattr(fields, "prompt_version", ""),
            )
        except MistralError as exc:
            logger.warning("Transcript recap compose failed, using fallback: %s", exc)
    if mistral_only:
        if not mistral_configured():
            msg = "MISTRAL_ENABLED and MISTRAL_API_KEY required for mistral_only compose"
            raise MistralError(msg)
        try:
            fields = compose_scrape_article_mistral(
                service_name=service_name,
                source_url=source_url,
                page_title=page_title,
                page_text=page_text,
                txid=txid,
                round_num=round_num,
                diff=diff,
                is_first_snapshot=is_first_snapshot,
                enrichment_block=enrichment_block,
                source_links=source_links,
                publish_topic=topic.value,
                first_coverage=first_coverage,
            )
            return ArticleComposeResult(
                title=fields.title,
                summary=fields.summary,
                body=fields.body,
                composer="mistral",
                publish_kind=publish_kind.value,
                extra_tags=getattr(fields, "tags", ()),
                prompt_version=getattr(fields, "prompt_version", ""),
            )
        except ComposeBusyError:
            raise
        except MistralError:
            if MISTRAL_FALLBACK_TEMPLATE:
                logger.warning("mistral_only failed; using template fallback")
            else:
                raise

    if topic == PublishTopic.COMMUNITY_RECAP:
        t_title, t_summary, t_body = compose_community_recap_article(
            service_name=service_name,
            source_url=source_url,
            page_title=page_title,
            page_text=page_text,
        )
    elif topic == PublishTopic.SCAM_ALERT:
        t_title, t_summary, t_body = compose_scam_alert_article(
            service_name=service_name,
            source_url=source_url,
            page_title=page_title,
            page_text=page_text,
        )
    elif publish_kind == PublishKind.SERVICE_DISCOVERY:
        t_title, t_summary, t_body = compose_service_discovery_article(
            service_name=service_name,
            source_url=source_url,
            page_title=page_title,
            page_text=page_text,
        )
    elif publish_kind == PublishKind.CONTENT_UPDATE:
        t_title, t_summary, t_body = compose_content_update_article(
            service_name=service_name,
            source_url=source_url,
            page_title=page_title,
            page_text=page_text,
            diff=diff,
            topic=topic,
        )
    else:
        t_title, t_summary, t_body = compose_article(
            service_name=service_name,
            source_url=source_url,
            page_title=page_title,
            page_text=page_text,
            txid=txid,
            round_num=round_num,
            diff=diff,
            is_first_snapshot=False,
        )
    template_result = ArticleComposeResult(
        title=t_title,
        summary=t_summary,
        body=t_body,
        composer="template",
        publish_kind=publish_kind.value,
    )

    # When mistral_only was requested we already attempted Mistral above; a
    # failure there fell through to this template, so don't fire a second
    # (identical) compose that would just hit the same rate limit.
    if not mistral_configured() or mistral_only:
        return template_result

    try:
        fields = compose_scrape_article_mistral(
            service_name=service_name,
            source_url=source_url,
            page_title=page_title,
            page_text=page_text,
            txid=txid,
            round_num=round_num,
            diff=diff,
            is_first_snapshot=is_first_snapshot,
            enrichment_block=enrichment_block,
            source_links=source_links,
            publish_topic=topic.value,
            first_coverage=first_coverage,
        )
        return ArticleComposeResult(
            title=fields.title,
            summary=fields.summary,
            body=fields.body,
            composer="mistral",
            publish_kind=publish_kind.value,
            extra_tags=getattr(fields, "tags", ()),
            prompt_version=getattr(fields, "prompt_version", ""),
        )
    except ComposeBusyError:
        raise
    except MistralError as exc:
        logger.warning(
            "Mistral scrape compose failed, fallback=%s: %s",
            MISTRAL_FALLBACK_TEMPLATE,
            exc,
        )
        if MISTRAL_FALLBACK_TEMPLATE:
            return template_result
        raise


def compose_weekly_price(
    snapshot: WeeklyPriceSnapshot,
) -> ArticleComposeResult:
    """Price-only article (legacy). Prefer ``compose_weekly_digest``."""
    template = compose_weekly_price_article(snapshot)
    template_result = ArticleComposeResult(
        title=template[0],
        summary=template[1],
        body=template[2],
        composer="template",
    )

    if not mistral_configured():
        return template_result

    try:
        fields = compose_weekly_price_article_mistral(snapshot)
        return ArticleComposeResult(
            title=fields.title,
            summary=fields.summary,
            body=fields.body,
            composer="mistral",
            prompt_version=getattr(fields, "prompt_version", ""),
        )
    except MistralError as exc:
        logger.warning(
            "Mistral price compose failed, fallback=%s: %s",
            MISTRAL_FALLBACK_TEMPLATE,
            exc,
        )
        if MISTRAL_FALLBACK_TEMPLATE:
            return template_result
        raise


def compose_weekly_digest(
    context: WeeklyDigestContext,
    *,
    mistral_only: bool = False,
) -> ArticleComposeResult:
    """Weekly digest: CoinGecko snapshot + recent feed articles (~1.5k chars)."""
    template = compose_weekly_digest_article(context)
    body = trim_text_to_chars(template[2], config.WEEKLY_DIGEST_MAX_BODY_CHARS)
    template_result = ArticleComposeResult(
        title=template[0],
        summary=template[1],
        body=body,
        composer="template",
        publish_kind=PublishKind.WEEKLY_DIGEST.value,
    )

    if mistral_only:
        if not mistral_configured():
            raise MistralError("MISTRAL_ENABLED and MISTRAL_API_KEY required for mistral_only")
        try:
            fields = compose_weekly_digest_article_mistral(context)
            return ArticleComposeResult(
                title=fields.title,
                summary=fields.summary,
                body=fields.body,
                composer="mistral",
                prompt_version=getattr(fields, "prompt_version", ""),
            )
        except MistralError:
            if MISTRAL_FALLBACK_TEMPLATE:
                logger.warning("mistral_only weekly digest failed; using template")
                return template_result
            raise

    if not mistral_configured():
        return template_result

    try:
        fields = compose_weekly_digest_article_mistral(context)
        return ArticleComposeResult(
            title=fields.title,
            summary=fields.summary,
            body=fields.body,
            composer="mistral",
            prompt_version=getattr(fields, "prompt_version", ""),
        )
    except MistralError as exc:
        logger.warning(
            "Mistral weekly digest failed, fallback=%s: %s",
            MISTRAL_FALLBACK_TEMPLATE,
            exc,
        )
        if MISTRAL_FALLBACK_TEMPLATE:
            return template_result
        raise

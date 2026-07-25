"""Compose and publish the weekly digest article."""

from __future__ import annotations

import logging
import time

import httpx

from app.core import config
from app.core.config import PRICE_ANALYSIS_ASSET_ID, PRICE_ANALYSIS_SERVICE_ID
from app.modules.newspaper.article_composer import compose_weekly_digest
from app.modules.newspaper.article_store import insert_article_if_absent
from app.modules.newspaper.article_tags import derive_article_tags
from app.modules.newspaper.price_analysis import PriceAnalysisError
from app.modules.newspaper.security import sanitize_body
from app.modules.newspaper.weekly_digest import (
    build_weekly_digest,
    digest_article_id,
    weekly_digest_trigger_id,
)
from app.modules.search.tasks.index_tasks import index_article

logger = logging.getLogger(__name__)


def run_weekly_digest_publish(
    *,
    asset_id: str = PRICE_ANALYSIS_ASSET_ID,
    service_id: str = PRICE_ANALYSIS_SERVICE_ID,
) -> dict[str, str]:
    """Build and publish one weekly digest issue (idempotent per ISO week)."""
    if not config.PRICE_ANALYSIS_ENABLED:
        return {"status": "skipped", "reason": "PRICE_ANALYSIS_ENABLED=0"}

    try:
        context = build_weekly_digest(asset_id=asset_id)
    except (PriceAnalysisError, httpx.HTTPError) as exc:
        return {"status": "error", "detail": str(exc)}

    week_key = context.week_key
    article_uuid = digest_article_id(week_key)
    txid = weekly_digest_trigger_id(week_key)

    from app.modules.ai.mistral_client import MistralCreditError, MistralError

    try:
        composed = compose_weekly_digest(context)
    except MistralError as exc:
        credit_issue = isinstance(exc, MistralCreditError)
        status = "mistral_credit_insufficient" if credit_issue else "mistral_failed"
        logger.error("Weekly digest compose failed for week %s: %s", week_key, exc)
        return {"status": status, "week": week_key, "detail": str(exc)[:200]}

    title = composed.title
    summary = composed.summary
    body = sanitize_body(composed.body)
    source_url = f"https://www.coingecko.com/en/coins/{context.price.asset_id}"

    article_id, created = insert_article_if_absent(
        article_id=article_uuid,
        service_id=service_id,
        title=title,
        summary=summary,
        body=body,
        trigger_txid=txid,
        trigger_round=0,
        source_url=source_url,
        tags=derive_article_tags(
            service_id=service_id,
            title=title,
            publish_kind="weekly_digest",
        ),
        prompt_version=getattr(composed, "prompt_version", ""),
    )

    if not created:
        return {
            "status": "skipped",
            "reason": "already_published",
            "week": week_key,
            "article_id": article_id,
        }

    index_article.delay(
        article_id=article_id,
        title=title,
        summary=summary,
        body=body,
        service_id=service_id,
        published_at_epoch=int(time.time()),
    )
    return {
        "status": "published",
        "article_id": article_id,
        "week": week_key,
        "change_pct": f"{context.price.week_change_pct:.2f}",
        "feed_articles": str(len(context.articles)),
        "composer": composed.composer,
    }

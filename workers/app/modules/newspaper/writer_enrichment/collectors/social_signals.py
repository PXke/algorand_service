from __future__ import annotations

from typing import Any

from app.core import config
from app.modules.newspaper.writer_enrichment.collectors.internal_search import (
    search_platform_mentions,
)
from app.modules.newspaper.writer_enrichment.collectors.social_posts import enrich_linked_posts


def collect_social_signals(
    *,
    service_id: str,
    primary_domain: str,
    display_name: str = "",
    page_text: str = "",
) -> dict[str, Any]:
    """
    Social context for the writer.

    - X/Twitter: oEmbed for status URLs found in ingest text (e.g. community warnings).
    - Discord/Telegram sentiment: via mirrored ingest + platform article search (not live scrape).
    - Full X search API / Threads: phase 3 (paid API or partner push).
    """
    posts = enrich_linked_posts(
        page_text,
        enabled=config.WRITER_ENRICHMENT_FETCH_TWEETS,
    )
    platform = search_platform_mentions(
        service_id=service_id,
        primary_domain=primary_domain,
        display_name=display_name,
    )

    return {
        "linked_posts": posts.get("linked_posts", []),
        "linked_post_count": posts.get("count", 0),
        "platform_article_mentions": platform.get("matches", []),
        "discord_telegram_sentiment": "index_mirrored_channels_phase_3",
        "x_search_api": "not_implemented",
        "threads": "not_implemented",
        "note": (
            "Paste or mirror posts containing x.com/status/… links; "
            "oEmbed resolves public tweet text for the writer."
        ),
    }

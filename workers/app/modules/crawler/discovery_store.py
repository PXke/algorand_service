"""Store a crawled page's content and score it for the publish pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.ai.content_categorizer import categorize_content
from app.modules.ai.publish_classifier import score_content_for_storage
from app.modules.crawler.domain_tracker import domain_from_url, update_domain_status


@dataclass(frozen=True)
class DiscoveryStoreOutcome:
    """Outcome of storing and scoring one crawled page."""
    status: str
    url: str
    storage_score: float = 0.0
    category: str = ""
    article_id: str = ""
    review_id: str = ""
    reason: str = ""


def store_discovery_content(
    *,
    url: str,
    page_title: str,  # noqa: ARG001 -- name must match the real callee's keyword arg
    page_text: str,
    source: str = "web",  # noqa: ARG001 -- name must match the real callee's keyword arg
    _txid: str = "",
) -> DiscoveryStoreOutcome:
    """Domain-centric discovery.

    Relevance is judged per DOMAIN, not per URL: we score the page only to keep
    the domain's relevance fresh and to surface new domains for the frontier.
    We do NOT compose an article here — content reporting happens through the
    monitored-source pipeline once a domain is approved. This keeps the admin
    judging "is algorand.co worth watching", never "is algorand.co/contact
    publish-worthy", and avoids a Mistral call per crawled page.
    """
    domain = domain_from_url(url)
    storage_score = score_content_for_storage(page_text, url)
    category = categorize_content(page_text, url)

    # Per-page SIGNAL only: refresh the domain's score/category/last-crawled, but
    # never set is_relevant here — a single page must not decide (or flip) a whole
    # domain's relevance. That verdict belongs to the admin or to the deliberate
    # content-relevance task (classify_pending_domains). Passing is_relevant=None
    # preserves the existing decision.
    update_domain_status(
        domain,
        relevance_score=storage_score,
        category=category,
        online=True,
    )

    return DiscoveryStoreOutcome(
        status="domain_scored",
        url=url,
        storage_score=storage_score,
        category=category,
    )

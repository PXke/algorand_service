"""Store a crawled page's content and score it for the publish pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.ai.content_categorizer import categorize_content
from app.modules.ai.publish_classifier import score_content_for_storage
from app.modules.crawler.domain_tracker import (
    domain_from_url,
    get_domain_status,
    update_domain_status,
)


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

    # A domain is crawled page by page across a session, and this is called
    # once per page — so the domain's stored score/category must be the BEST
    # page seen so far, not whichever page happened to be crawled last. A
    # multi-page site routinely mixes real content pages with thin,
    # near-textless ones (an interactive UI screen, a bare game board, an
    # empty state) that legitimately score 0/generic on their own; without
    # this floor, crawling those after a clearly on-topic page silently
    # regresses the whole domain to 0/generic and it never produces an
    # artifact (root-caused 2026-08-31, algochess.org: 16 real pages scored
    # well, but the 17th and LAST-crawled -- a chess-practice board with only
    # move-clock UI text -- overwrote the domain to relevance_score=0,
    # category="generic"). Same reasoning as _best_scored_page in
    # tasks/url_queue_tasks.py's classify_pending_domains path ("one bad page
    # must not sink a domain the sample otherwise shows is relevant"), applied
    # here to this column instead of that one's separate 0-1 metadata verdict.
    existing_score = float((get_domain_status(domain) or {}).get("relevance_score") or 0.0)
    if storage_score < existing_score:
        resolved_score: float | None = None  # preserve the existing (higher) score
        resolved_category = ""  # preserve the existing category alongside it
    else:
        resolved_score = storage_score
        resolved_category = category

    # Per-page SIGNAL only: refresh the domain's score/category/last-crawled, but
    # never set is_relevant here — a single page must not decide (or flip) a whole
    # domain's relevance. That verdict belongs to the admin or to the deliberate
    # content-relevance task (classify_pending_domains). Passing is_relevant=None
    # preserves the existing decision.
    #
    # relevance_score here is storage_score (~0-10, score_content_for_storage) —
    # the same keyword-hit scale register_pending_domain seeds at discovery time.
    # Keep it that way: classify_pending_domains/deep_classify_domain/
    # reevaluate_pending_domains carry their own 0-1 verdict in metadata's
    # content_relevance instead of this column (see update_domain_status's
    # docstring) specifically so this per-page write never clobbers it.
    update_domain_status(
        domain,
        relevance_score=resolved_score,
        category=resolved_category,
        online=True,
    )

    return DiscoveryStoreOutcome(
        status="domain_scored",
        url=url,
        storage_score=storage_score,
        category=category,
    )

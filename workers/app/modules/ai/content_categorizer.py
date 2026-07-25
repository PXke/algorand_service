"""Classify a page's content category, with a keyword fallback when the model doesn't run."""

from __future__ import annotations

from app.modules.search.classifier.score import POSITIVE_KEYWORDS

VALID_CATEGORIES = frozenset(
    {
        "service",
        "news",
        "tool",
        "payment",
        "nft",
        "governance",
        "generic",
    }
)

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "news": ("announcement", "breaking", "launch", "partnership", "update"),
    "tool": (
        "sdk",
        "api",
        "developer",
        "library",
        "cli",
        "indexer",
        "integration",
        "plugin",
        "dashboard",
        "analytics",
        "explorer",
        "oracle",
    ),
    "payment": ("payment", "checkout", "merchant", "invoice", "usdc"),
    "nft": ("nft", "collectible", "arc-3", "marketplace"),
    "governance": ("governance", "vote", "proposal", "dao"),
    # DeFi jargon alone missed non-DeFi Algorand products (loyalty apps,
    # storage, gaming, ticketing) whose landing-page copy never says "staking"
    # or "bridge" but does say "launch"/"update" — which only matched "news"
    # (root-caused 2026-07-25: algofile.io, gramo.io sat mislabeled "news").
    "service": (
        "wallet",
        "exchange",
        "defi",
        "staking",
        "bridge",
        "rewards",
        "loyalty",
        "storage",
        "subscription",
        "membership",
        "platform",
        "dapp",
        "booking",
        "ticketing",
        "gaming",
    ),
}


def _fallback_categories(text: str, url: str, *, max_categories: int = 3) -> list[str]:
    """Keyword heuristic returning every category with a positive score."""
    lowered = f"{text}\n{url}".lower()
    scores: dict[str, int] = dict.fromkeys(_CATEGORY_KEYWORDS, 0)
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in lowered:
                scores[cat] += 1
    algo_hits = sum(1 for kw in POSITIVE_KEYWORDS if kw in lowered)
    ranked = sorted(
        ((cat, sc) for cat, sc in scores.items() if sc > 0),
        key=lambda item: item[1],
        reverse=True,
    )
    if ranked:
        return [cat for cat, _ in ranked[:max_categories]]
    if algo_hits >= 3:
        return ["service"]
    return ["generic"]


def _fallback_category(text: str, url: str) -> str:
    return _fallback_categories(text, url)[0]


def _admin_category_for_domain(url: str) -> str | None:
    """Admin-corrected category for this domain (classifier feedback), if any."""
    from urllib.parse import urlparse

    host = (urlparse(url.strip()).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import DomainTrackingStmts

        row = get_cassandra_session().execute(DomainTrackingStmts.GET_METADATA, (host,)).one()
    except Exception:
        return None
    if row is None:
        return None
    admin = (row.metadata or {}).get("category_admin", "")
    return admin if admin in VALID_CATEGORIES else None


def categorize_content_all(text: str, url: str, *, max_categories: int = 3) -> list[str]:
    """All applicable content categories (primary first), without an LLM call."""
    admin_category = _admin_category_for_domain(url)
    if admin_category:
        return [admin_category]

    from app.modules.ai.publish_classifier import predict_categories

    predicted = predict_categories(text, url, max_categories=max_categories)
    if predicted:
        return predicted

    return _fallback_categories(text, url, max_categories=max_categories)


def categorize_content(text: str, url: str) -> str:
    """Primary category — the first of [categorize_content_all]."""
    return categorize_content_all(text, url)[0]

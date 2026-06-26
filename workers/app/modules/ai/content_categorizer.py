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
    "tool": ("sdk", "api", "developer", "library", "cli", "indexer"),
    "payment": ("payment", "checkout", "merchant", "invoice", "usdc"),
    "nft": ("nft", "collectible", "arc-3", "marketplace"),
    "governance": ("governance", "vote", "proposal", "dao"),
    "service": ("wallet", "exchange", "defi", "staking", "bridge"),
}


def _fallback_category(text: str, url: str) -> str:
    lowered = f"{text}\n{url}".lower()
    scores: dict[str, int] = dict.fromkeys(_CATEGORY_KEYWORDS, 0)
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in lowered:
                scores[cat] += 1
    algo_hits = sum(1 for kw in POSITIVE_KEYWORDS if kw in lowered)
    if algo_hits >= 3 and max(scores.values(), default=0) == 0:
        return "service"
    best = max(scores.items(), key=lambda item: item[1])
    if best[1] > 0:
        return best[0]
    return "generic"


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

        row = get_cassandra_session().execute(
            "SELECT metadata FROM domain_tracking WHERE domain = %s",
            (host,),
        ).one()
    except Exception:
        return None
    if row is None:
        return None
    admin = (row.metadata or {}).get("category_admin", "")
    return admin if admin in VALID_CATEGORIES else None


def categorize_content(text: str, url: str) -> str:
    """Categorize page content with NO LLM call: admin per-domain correction,
    then the trained category model (learned from your corrections), then the
    keyword heuristic. Mistral is reserved for article composition only."""
    admin_category = _admin_category_for_domain(url)
    if admin_category:
        return admin_category

    from app.modules.ai.publish_classifier import predict_category_model

    predicted = predict_category_model(text, url)
    if predicted and predicted in VALID_CATEGORIES:
        return predicted

    return _fallback_category(text, url)

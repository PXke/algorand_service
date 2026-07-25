"""Derive an article's tags from its source kind and content."""

from __future__ import annotations


def derive_article_tags(
    *,
    service_id: str,
    source_kind: str | None = None,
    title: str = "",
    publish_kind: str | None = None,
    publish_topic: str | None = None,
    publish_tier: str | None = None,
) -> list[str]:
    """Build display tags for a feed article from service metadata."""
    tags: list[str] = []
    kind = (source_kind or "").strip().lower()
    if kind:
        tags.append(kind.replace("_", "-"))

    sid = service_id.lower()
    title_l = title.lower()

    if "weekly" in sid or sid.startswith("weekly-"):
        tags.append("weekly")
    if "digest" in sid:
        tags.append("digest")
    if "price" in sid or "market" in title_l:
        tags.append("market")
    if "mistral" in sid:
        tags.append("ai")

    pk = (publish_kind or "").strip().lower()
    if pk == "service_discovery":
        tags.append("discovery")
    elif pk == "content_update":
        tags.append("update")
    elif pk == "weekly_digest":
        tags.append("weekly")
        tags.append("digest")

    topic = (publish_topic or "").strip().lower().replace("_", "-")
    topic_tags = {
        "scam-alert": "scam-alert",
        "network-incident": "outage",
        "sdk-release": "sdk",
        "community-event": "community",
        "community-recap": "recap",
        "pricing-change": "pricing",
        "new-service": "discovery",
    }
    if topic in topic_tags:
        tags.append(topic_tags[topic])

    if (publish_tier or "").strip().lower() == "breaking":
        tags.append("breaking")

    if not tags:
        tags.append("news")

    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out

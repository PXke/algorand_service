"""Derive an article's tags from its source kind and content."""

from __future__ import annotations

_TOPIC_TAGS = {
    "scam-alert": "scam-alert",
    "network-incident": "outage",
    "sdk-release": "sdk",
    "community-event": "community",
    "community-recap": "recap",
    "pricing-change": "pricing",
    "new-service": "discovery",
}


def _service_content_tags(sid: str, title_l: str) -> list[str]:
    """Tags inferred from the service id and title (weekly/digest/market/ai)."""
    tags = []
    if "weekly" in sid or sid.startswith("weekly-"):
        tags.append("weekly")
    if "digest" in sid:
        tags.append("digest")
    if "price" in sid or "market" in title_l:
        tags.append("market")
    if "mistral" in sid:
        tags.append("ai")
    return tags


def _publish_kind_tags(publish_kind: str | None) -> list[str]:
    pk = (publish_kind or "").strip().lower()
    if pk == "service_discovery":
        return ["discovery"]
    if pk == "content_update":
        return ["update"]
    if pk == "weekly_digest":
        return ["weekly", "digest"]
    return []


def _dedupe(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


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

    tags.extend(_service_content_tags(service_id.lower(), title.lower()))
    tags.extend(_publish_kind_tags(publish_kind))

    topic = (publish_topic or "").strip().lower().replace("_", "-")
    if topic in _TOPIC_TAGS:
        tags.append(_TOPIC_TAGS[topic])

    if (publish_tier or "").strip().lower() == "breaking":
        tags.append("breaking")

    if not tags:
        tags.append("news")

    return _dedupe(tags)

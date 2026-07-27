"""Derive and order article tags for reader display."""

from __future__ import annotations

# Provenance / pipeline labels — fine as chips, bad as the lead kicker.
_META_TAGS = frozenset(
    {
        "web",
        "chain",
        "chain-only",
        "onchain",
        "on-chain",
        "mail",
        "discord",
        "telegram",
        "update",
        "discovery",
        "news",
        "ai",
        "generic",
        "algorand",
        "updated",
        "weekly",
        "digest",
    }
)

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


def order_reader_tags(tags: list[str]) -> list[str]:
    """Lead with topical tags; keep provenance/meta chips at the end."""
    topical: list[str] = []
    meta: list[str] = []
    for raw in tags:
        tag = str(raw or "").strip().lower()
        if not tag:
            continue
        if tag in _META_TAGS:
            meta.append(tag)
        else:
            topical.append(tag)
    return _dedupe([*topical, *meta])


def derive_article_tags(
    *,
    service_id: str,
    source_kind: str | None = None,
    title: str = "",
    publish_kind: str | None = None,
    publish_topic: str | None = None,
    publish_tier: str | None = None,
) -> list[str]:
    """Build display tags for a feed article from service metadata.

    Topical labels lead; source-kind / pipeline labels trail so kickers and
    related-topic picks don't collapse to ubiquitous values like ``web``.
    """
    topical: list[str] = []
    meta: list[str] = []

    topical.extend(_service_content_tags(service_id.lower(), title.lower()))

    topic = (publish_topic or "").strip().lower().replace("_", "-")
    if topic in _TOPIC_TAGS:
        mapped = _TOPIC_TAGS[topic]
        (meta if mapped in _META_TAGS else topical).append(mapped)

    if (publish_tier or "").strip().lower() == "breaking":
        topical.append("breaking")

    meta.extend(_publish_kind_tags(publish_kind))

    kind = (source_kind or "").strip().lower().replace("_", "-")
    if kind:
        meta.append(kind)

    ordered = order_reader_tags([*topical, *meta])
    return ordered or ["news"]

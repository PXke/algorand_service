"""Derive and order article tags for reader display."""

from __future__ import annotations

# The provenance list and the ordering rule both live in shared/taxonomy.json.
# This module is the PRODUCER — it decides the tag order that gets persisted,
# and publish_tasks truncates to the first 10 — so a local copy drifting from
# the canon is worse here than anywhere else: it was missing `blockchain` and
# `service`, which meant those led the stored array as if topical while both
# display sides (SSR and SPA) correctly classified them as provenance.
from algorand_shared.taxonomy import is_meta_tag, order_reader_tags

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
        (meta if is_meta_tag(mapped) else topical).append(mapped)

    if (publish_tier or "").strip().lower() == "breaking":
        topical.append("breaking")

    meta.extend(_publish_kind_tags(publish_kind))

    kind = (source_kind or "").strip().lower().replace("_", "-")
    if kind:
        meta.append(kind)

    ordered = order_reader_tags([*topical, *meta])
    return ordered or ["news"]

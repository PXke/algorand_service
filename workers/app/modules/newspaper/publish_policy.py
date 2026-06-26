from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from app.core import config
from app.modules.newspaper.article_store import (
    count_articles_for_service,
    count_articles_published_on_utc_day,
)


class PublishKind(StrEnum):
    WEEKLY_DIGEST = "weekly_digest"
    SERVICE_DISCOVERY = "service_discovery"
    CONTENT_UPDATE = "content_update"


class PublishTier(StrEnum):
    STANDARD = "standard"
    BREAKING = "breaking"


class PublishTopic(StrEnum):
    SCAM_ALERT = "scam_alert"
    NETWORK_INCIDENT = "network_incident"
    SDK_RELEASE = "sdk_release"
    COMMUNITY_EVENT = "community_event"
    COMMUNITY_RECAP = "community_recap"
    PRICING_CHANGE = "pricing_change"
    NEW_SERVICE = "new_service"
    CONTENT_UPDATE = "content_update"
    CHAIN_ACTIVITY = "chain_activity"
    GENERIC = "generic"


TOPIC_PRIORITY: dict[PublishTopic, int] = {
    PublishTopic.SCAM_ALERT: 100,
    PublishTopic.NETWORK_INCIDENT: 98,
    PublishTopic.SDK_RELEASE: 90,
    PublishTopic.COMMUNITY_EVENT: 85,
    PublishTopic.COMMUNITY_RECAP: 82,
    PublishTopic.NEW_SERVICE: 80,
    PublishTopic.PRICING_CHANGE: 75,
    PublishTopic.CHAIN_ACTIVITY: 60,
    PublishTopic.CONTENT_UPDATE: 55,
    PublishTopic.GENERIC: 40,
}


_DISCOVERY_PHRASES = (
    "introducing",
    "announcing",
    "announcement",
    "launch",
    "launched",
    "now live",
    "new service",
    "new product",
    "we built",
    "we're building",
    "pricing",
    "beta access",
    "early access",
    "just released",
    "release notes",
    "github.com",
    "open source",
)

_SCAM_PHRASES = (
    "scam alert",
    "scam warning",
    "phishing",
    "fraud alert",
    "rug pull",
    "exploit",
    "do not send",
    "do not interact",
    "do not connect",
    "malicious app",
    "malicious site",
    "malicious ",
    "impersonat",
    "fake airdrop",
    "wallet drainer",
    "rekey",
    "rekeyed",
    "transaction requests",
    "scam warning",
    "credible reports",
    "asset optin",
    "opt-in",
    "optin",
    ":warning:",
    "🚨",
)

_SDK_PHRASES = (
    "sdk release",
    "new sdk",
    "release notes",
    "changelog",
    "npm publish",
    "pypi",
    "crates.io",
    "github.com/",
    "/releases/",
)

_COMMUNITY_PHRASES = (
    "community call",
    "town hall",
    "ama ",
    " ask me anything",
    "twitter space",
    "discord event",
    "office hours",
    "live stream",
    "webinar",
)

_BREAKING_PHRASES = (
    "chain down",
    "network halt",
    "network outage",
    "consensus failure",
    "mainnet down",
    "testnet down",
    "lost $",
    "lost ",
    "stolen ",
    "100,000",
    "emergency",
)

_PRICING_PHRASES = (
    "pricing",
    "price change",
    "fee update",
    "subscription",
    "per month",
    "per algo",
    "gas fee",
    "transaction fee",
)


@dataclass(frozen=True)
class PublishDecision:
    kind: PublishKind
    allowed: bool
    reason: str


@dataclass(frozen=True)
class PublishIntent:
    kind: PublishKind
    topic: PublishTopic
    tier: PublishTier
    priority: int
    priority_breakdown: str = ""
    event_id: str = ""
    event_phase: str = ""


def utc_day_start_epoch(when: datetime | None = None) -> int:
    moment = when or datetime.now(tz=UTC)
    start = datetime(moment.year, moment.month, moment.day, tzinfo=UTC)
    return int(start.timestamp())


def remaining_daily_publish_slots(*, when: datetime | None = None) -> int:
    """Remaining scheduled (standard-tier) articles for the UTC day."""
    return remaining_standard_publish_slots(when=when)


def remaining_standard_publish_slots(*, when: datetime | None = None) -> int:
    day_start = utc_day_start_epoch(when)
    published = count_standard_articles_on_utc_day(day_start_epoch=day_start)
    return max(0, config.NEWS_MAX_ARTICLES_PER_DAY - published)


def remaining_breaking_publish_slots(*, when: datetime | None = None) -> int:
    day_start = utc_day_start_epoch(when)
    published = count_breaking_articles_on_utc_day(day_start_epoch=day_start)
    return max(0, config.NEWS_MAX_BREAKING_PER_DAY - published)


def count_standard_articles_on_utc_day(*, day_start_epoch: int, limit: int = 500) -> int:

    total = count_articles_published_on_utc_day(day_start_epoch=day_start_epoch, limit=limit)
    breaking = count_breaking_articles_on_utc_day(day_start_epoch=day_start_epoch, limit=limit)
    return max(0, total - breaking)


def count_breaking_articles_on_utc_day(*, day_start_epoch: int, limit: int = 500) -> int:
    from app.modules.newspaper.article_store import count_feed_articles_with_tag_on_day

    return count_feed_articles_with_tag_on_day(
        tag="breaking",
        day_start_epoch=day_start_epoch,
        limit=limit,
    )


def priority_for_topic(topic: PublishTopic) -> int:
    return TOPIC_PRIORITY.get(topic, TOPIC_PRIORITY[PublishTopic.GENERIC])


def classify_scrape_publish(
    *,
    service_id: str,
    page_text: str,
    is_first_snapshot: bool,
    diff: str | None,
) -> PublishKind:
    """Choose discovery vs content-update for a crawl/scrape publish."""
    prior = count_articles_for_service(service_id)
    if prior == 0 or is_first_snapshot:
        return PublishKind.SERVICE_DISCOVERY
    if _looks_like_announcement(page_text):
        return PublishKind.SERVICE_DISCOVERY
    return PublishKind.CONTENT_UPDATE


def classify_publish_topic(
    *,
    page_text: str,
    diff: str | None,
    publish_kind: PublishKind,
    source_kind: str | None = None,
    chain_triggered: bool = False,
) -> PublishTopic:
    """Score editorial topic for queue ordering (scam, SDK, community, pricing, …)."""
    combined = page_text
    if diff:
        combined = f"{page_text}\n{diff}"

    lower = combined.lower()
    if _contains_any(lower, _SCAM_PHRASES):
        return PublishTopic.SCAM_ALERT
    if _contains_any(lower, _BREAKING_PHRASES) and _contains_any(
        lower, ("down", "outage", "halt", "lost", "stolen", "scam", "exploit")
    ) and _contains_any(lower, ("chain", "network", "mainnet", "consensus", "outage", "halt")):
        return PublishTopic.NETWORK_INCIDENT
    if _contains_any(lower, _SDK_PHRASES) or re.search(
        r"\bv\d+\.\d+(\.\d+)?\b", lower
    ):
        return PublishTopic.SDK_RELEASE
    if _contains_any(lower, _COMMUNITY_PHRASES) or re.search(
        r"\b(in|within)\s+\d+\s+days?\b", lower
    ):
        return PublishTopic.COMMUNITY_EVENT
    if _contains_any(lower, _PRICING_PHRASES) or _diff_mentions_pricing(diff):
        return PublishTopic.PRICING_CHANGE
    if publish_kind == PublishKind.SERVICE_DISCOVERY:
        return PublishTopic.NEW_SERVICE
    if chain_triggered:
        return PublishTopic.CHAIN_ACTIVITY
    if publish_kind == PublishKind.CONTENT_UPDATE:
        return PublishTopic.CONTENT_UPDATE
    return PublishTopic.GENERIC


def build_publish_intent(
    *,
    service_id: str,
    page_text: str,
    is_first_snapshot: bool,
    diff: str | None,
    source_kind: str | None = None,
    source_url: str = "",
    page_title: str = "",
    mail_from: str = "",
    stored_service_weight: int = 0,
    chain_triggered: bool = False,
    relevance: float | None = None,
    novelty: float | None = None,
) -> PublishIntent:
    from app.modules.newspaper.event_lifecycle import detect_event_context
    from app.modules.newspaper.publish_score import compute_priority

    kind = classify_scrape_publish(
        service_id=service_id,
        page_text=page_text,
        is_first_snapshot=is_first_snapshot,
        diff=diff,
    )
    topic = classify_publish_topic(
        page_text=page_text,
        diff=diff,
        publish_kind=kind,
        source_kind=source_kind,
        chain_triggered=chain_triggered,
    )
    event_id = ""
    event_phase = ""
    event_ctx = detect_event_context(page_text=page_text, page_title=page_title)
    if event_ctx is not None:
        event_id = event_ctx.event_id
        event_phase = event_ctx.phase.value
        if event_ctx.topic_override is not None:
            topic = event_ctx.topic_override

    breakdown = compute_priority(
        topic=topic,
        publish_kind=kind,
        page_text=page_text,
        diff=diff,
        source_kind=source_kind,
        source_url=source_url,
        mail_from=mail_from,
        stored_service_weight=stored_service_weight,
        relevance=relevance,
        novelty=novelty,
    )
    tier = classify_publish_tier(topic=topic, page_text=page_text)
    return PublishIntent(
        kind=kind,
        topic=topic,
        tier=tier,
        priority=breakdown.total,
        priority_breakdown=(
            f"relevance={breakdown.relevance_bonus}+novelty={breakdown.novelty_bonus} "
            f"(heuristics topic={breakdown.topic_base} trust={breakdown.source_trust} "
            f"service={breakdown.service_weight} no longer ranked)"
        ),
        event_id=event_id,
        event_phase=event_phase,
    )


def classify_publish_tier(*, topic: PublishTopic, page_text: str) -> PublishTier:
    """Breaking tier: scams, network incidents — immediate path, separate daily cap."""
    if topic in (PublishTopic.SCAM_ALERT, PublishTopic.NETWORK_INCIDENT):
        return PublishTier.BREAKING
    lower = page_text.lower()
    if _contains_any(lower, _SCAM_PHRASES):
        return PublishTier.BREAKING
    if _contains_any(lower, _BREAKING_PHRASES) and _contains_any(
        lower, ("chain", "network", "mainnet", "outage", "halt", "down")
    ):
        return PublishTier.BREAKING
    return PublishTier.STANDARD


def is_breaking_topic(topic: PublishTopic) -> bool:
    return topic in (PublishTopic.SCAM_ALERT, PublishTopic.NETWORK_INCIDENT)


def build_dedupe_key(
    *,
    service_id: str,
    topic: str,
    content_hash: str,
    tier: str = "standard",
) -> str:
    short_hash = content_hash[:16] if content_hash else "none"
    return f"{service_id}:{topic}:{tier}:{short_hash}"


def _looks_like_announcement(text: str) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in _DISCOVERY_PHRASES)


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(p in text for p in phrases)


def _diff_mentions_pricing(diff: str | None) -> bool:
    if not diff:
        return False
    lower = diff.lower()
    return any(p in lower for p in _PRICING_PHRASES)


def is_significant_diff(diff: str | None, *, min_lines: int | None = None) -> bool:
    if not diff or not diff.strip():
        return False
    threshold = min_lines if min_lines is not None else config.NEWS_MIN_DIFF_LINES
    added = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
    return added >= threshold


def evaluate_enqueue(
    kind: PublishKind,
    *,
    diff: str | None = None,
) -> PublishDecision:
    """Whether a crawl change should enter the publish queue (no daily cap)."""
    if kind == PublishKind.WEEKLY_DIGEST:
        return PublishDecision(kind=kind, allowed=False, reason="weekly_not_queued")

    if kind == PublishKind.CONTENT_UPDATE and not is_significant_diff(diff):
        return PublishDecision(
            kind=kind,
            allowed=False,
            reason="diff_too_small",
        )

    return PublishDecision(kind=kind, allowed=True, reason="ok")


def evaluate_standard_publish(
    kind: PublishKind,
    *,
    diff: str | None = None,
    when: datetime | None = None,
) -> PublishDecision:
    """Standard queue drain: cap + ~3h spacing between posts."""
    enqueue_decision = evaluate_enqueue(kind, diff=diff)
    if not enqueue_decision.allowed:
        return enqueue_decision

    if remaining_standard_publish_slots(when=when) <= 0:
        return PublishDecision(
            kind=kind,
            allowed=False,
            reason="standard_daily_cap_reached",
        )

    from app.modules.newspaper.publish_schedule import is_standard_publish_due

    due, detail = is_standard_publish_due()
    if not due:
        return PublishDecision(kind=kind, allowed=False, reason=detail)

    return PublishDecision(kind=kind, allowed=True, reason="ok")


def evaluate_breaking_publish(
    kind: PublishKind,
    *,
    diff: str | None = None,
    when: datetime | None = None,
) -> PublishDecision:
    """Breaking drain: separate cap, no interval — publish when credible."""
    enqueue_decision = evaluate_enqueue(kind, diff=diff)
    if not enqueue_decision.allowed:
        return enqueue_decision

    if remaining_breaking_publish_slots(when=when) <= 0:
        return PublishDecision(
            kind=kind,
            allowed=False,
            reason="breaking_daily_cap_reached",
        )

    return PublishDecision(kind=kind, allowed=True, reason="ok")


def evaluate_publish(
    kind: PublishKind,
    *,
    service_id: str = "",
    diff: str | None = None,
    when: datetime | None = None,
    tier: PublishTier = PublishTier.STANDARD,
) -> PublishDecision:
    if tier == PublishTier.BREAKING:
        return evaluate_breaking_publish(kind, diff=diff, when=when)
    return evaluate_standard_publish(kind, diff=diff, when=when)


def trim_text_to_chars(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    for sep in ("\n\n", "\n", ". "):
        idx = cut.rfind(sep)
        if idx > max_chars * 0.6:
            return cut[:idx].rstrip() + "…"
    return cut.rstrip() + "…"


def strip_markdown_for_length_estimate(body: str) -> str:
    """Rough plain length check (markdown kept for storage)."""
    plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", body)
    plain = re.sub(r"[#*_`]", "", plain)
    return plain

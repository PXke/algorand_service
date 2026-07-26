"""Publish kind/tier/topic enums and the policy deciding what to do with a scrape diff."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from app.core import config
from app.modules.newspaper.article_store import count_articles_published_on_utc_day


class PublishKind(StrEnum):
    """What kind of event triggered this publish candidate."""
    WEEKLY_DIGEST = "weekly_digest"
    SERVICE_DISCOVERY = "service_discovery"
    CONTENT_UPDATE = "content_update"
    EDITORIAL_ASSIGNMENT = "editorial_assignment"


class PublishTier(StrEnum):
    """Publish urgency tier (standard vs. breaking)."""
    STANDARD = "standard"
    BREAKING = "breaking"


class PublishTopic(StrEnum):
    """Reader-facing topic classification for tags and match keys."""
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
    EDITORIAL_ASSIGNMENT = "editorial_assignment"


TOPIC_PRIORITY: dict[PublishTopic, int] = {
    PublishTopic.SCAM_ALERT: 100,
    PublishTopic.NETWORK_INCIDENT: 98,
    PublishTopic.SDK_RELEASE: 90,
    PublishTopic.COMMUNITY_EVENT: 85,
    PublishTopic.EDITORIAL_ASSIGNMENT: 85,
    PublishTopic.COMMUNITY_RECAP: 82,
    PublishTopic.NEW_SERVICE: 80,
    PublishTopic.PRICING_CHANGE: 75,
    PublishTopic.CHAIN_ACTIVITY: 60,
    PublishTopic.CONTENT_UPDATE: 55,
    PublishTopic.GENERIC: 40,
}


_SCAM_PHRASES = (
    "scam alert",
    "scam warning",
    "phishing",
    "fraud alert",
    "rug pull",
    "do not send",
    "do not interact",
    "do not connect",
    "malicious app",
    "malicious site",
    "malicious ",
    "impersonat",
    "fake airdrop",
    "wallet drainer",
    ":warning:",
    "🚨",
)

# These read as ordinary Algorand vocabulary on their own (opting in to hold an
# asset, rekeying an account, a routine "fixed an exploit" changelog line) —
# they only signal a scam ALONGSIDE actual alarm language, never bare. Firing
# on the bare word produced false positives: a wallet's opt-in help text, an
# NFT collection's "opt-in to $TOKEN" copy, a rekey-import FAQ entry. Real scam
# posts (e.g. the AlgoBlow $BLOW rekey-drainer) already carry a hard phrase
# above ("scam warning") too, so this is a safety net, not the primary catch.
_SCAM_CONTEXT_PHRASES = (
    "opt-in",
    "optin",
    "asset optin",
    "rekey",
    "rekeyed",
    "exploit",
    "transaction requests",
    "credible reports",
)
_SCAM_ALARM_WORDS = (
    "scam",
    "phishing",
    "fraud",
    "malicious",
    "fake",
    "impersonat",
    "warning",
    "beware",
    "suspicious",
    "caution",
    "stolen",
    "compromised",
    "drainer",
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
    """Whether a publish kind is currently allowed, and why."""
    kind: PublishKind
    allowed: bool
    reason: str


@dataclass(frozen=True)
class PublishIntent:
    """The kind/topic/tier a queued row should publish as."""
    kind: PublishKind
    topic: PublishTopic
    tier: PublishTier
    priority: int
    priority_breakdown: str = ""
    event_id: str = ""
    event_phase: str = ""


def utc_day_start_epoch(when: datetime | None = None) -> int:
    """Return the epoch seconds for UTC midnight on the given (or current) day."""
    moment = when or datetime.now(tz=UTC)
    start = datetime(moment.year, moment.month, moment.day, tzinfo=UTC)
    return int(start.timestamp())


def remaining_standard_publish_slots(*, when: datetime | None = None) -> int:
    """Redundancy pruning (2026-07-18, deferred from the gate consolidation): the daily cap used to have two parallel counting implementations — this advisory read counted Cassandra feed rows while publish_daily_guard's atomic reserve counted Redis reservations, and the two drift intra-day (in-flight reservations are invisible to the feed count; backlog releases used to insert feed rows without reserving).

    One counting authority now: the guard's reservation-aware Redis counter,
    itself initialized from the feed count once per day. The Cassandra
    counters below survive solely as that init source.
    """
    from app.modules.newspaper.publish_daily_guard import published_count_today

    published = published_count_today(tier=PublishTier.STANDARD, when=when)
    return max(0, config.NEWS_MAX_ARTICLES_PER_DAY - published)


def remaining_breaking_publish_slots(*, when: datetime | None = None) -> int:
    """Remaining breaking-tier publish slots left for the UTC day."""
    from app.modules.newspaper.publish_daily_guard import published_count_today

    published = published_count_today(tier=PublishTier.BREAKING, when=when)
    return max(0, config.NEWS_MAX_BREAKING_PER_DAY - published)


def count_standard_articles_on_utc_day(*, day_start_epoch: int, limit: int = 500) -> int:
    """Count articles published that UTC day excluding breaking-tagged ones."""
    total = count_articles_published_on_utc_day(day_start_epoch=day_start_epoch, limit=limit)
    breaking = count_breaking_articles_on_utc_day(day_start_epoch=day_start_epoch, limit=limit)
    return max(0, total - breaking)


def count_breaking_articles_on_utc_day(*, day_start_epoch: int, limit: int = 500) -> int:
    """Count articles tagged "breaking" that were published on the given UTC day."""
    from app.modules.newspaper.article_store import count_feed_articles_with_tag_on_day

    return count_feed_articles_with_tag_on_day(
        tag="breaking",
        day_start_epoch=day_start_epoch,
        limit=limit,
    )


def priority_for_topic(topic: PublishTopic) -> int:
    """Base queue priority for a topic, falling back to the generic weight."""
    return TOPIC_PRIORITY.get(topic, TOPIC_PRIORITY[PublishTopic.GENERIC])


def classify_scrape_publish(
    *,
    service_id: str,  # noqa: ARG001 -- name must match the real callee's keyword arg
    page_text: str,  # noqa: ARG001 -- name must match the real callee's keyword arg
    is_first_snapshot: bool,
    diff: str | None,  # noqa: ARG001 -- name must match the real callee's keyword arg
) -> PublishKind:
    """Choose discovery vs content-update for a crawl/scrape publish.

    Discovery = the service's FIRST snapshot, exactly once per service. Every
    later change is a content update, which only enqueues on a significant diff
    (see evaluate_enqueue) — that diff IS the story ("product X shipped/changed
    Y"). It must never depend on published-article counts or announcement-y
    page text: the old `prior == 0` check re-fired discovery on every homepage
    churn for services that hadn't composed yet (a ~700-row queue flood at
    ~7 published articles/day), and marketing pages permanently
    _looks_like_announcement.
    """
    if is_first_snapshot:
        return PublishKind.SERVICE_DISCOVERY
    return PublishKind.CONTENT_UPDATE


def classify_publish_topic(
    *,
    page_text: str,
    diff: str | None,
    publish_kind: PublishKind,
    source_kind: str | None = None,  # noqa: ARG001 -- name must match the real callee's keyword arg
    chain_triggered: bool = False,
) -> PublishTopic:
    """Score editorial topic for queue ordering (scam, SDK, community, pricing, …)."""
    combined = page_text
    if diff:
        combined = f"{page_text}\n{diff}"

    lower = combined.lower()
    if _contains_any(lower, _SCAM_PHRASES) or _context_and_alarm_nearby(
        lower, _SCAM_CONTEXT_PHRASES, _SCAM_ALARM_WORDS
    ):
        return PublishTopic.SCAM_ALERT
    if (
        _contains_any(lower, _BREAKING_PHRASES)
        and _contains_any(lower, ("down", "outage", "halt", "lost", "stolen", "scam", "exploit"))
        and _contains_any(lower, ("chain", "network", "mainnet", "consensus", "outage", "halt"))
    ):
        return PublishTopic.NETWORK_INCIDENT
    if _contains_any(lower, _SDK_PHRASES) or re.search(r"\bv\d+\.\d+(\.\d+)?\b", lower):
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
    published_at: str = "",
    mail_from: str = "",
    stored_service_weight: int = 0,
    chain_triggered: bool = False,
    relevance: float | None = None,
    novelty: float | None = None,
    classifier_publish: bool | None = None,
    classifier_confidence: float = 0.0,
) -> PublishIntent:
    """Build the full kind/topic/tier/priority decision for a candidate publish."""
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
        page_title=page_title,
        published_at=published_at,
        mail_from=mail_from,
        stored_service_weight=stored_service_weight,
        relevance=relevance,
        novelty=novelty,
        is_event=bool(event_id),
        classifier_publish=classifier_publish,
        classifier_confidence=classifier_confidence,
    )
    tier = classify_publish_tier(topic=topic, page_text=page_text)
    return PublishIntent(
        kind=kind,
        topic=topic,
        tier=tier,
        priority=breakdown.total,
        priority_breakdown=(
            f"relevance={breakdown.relevance_bonus}+novelty={breakdown.novelty_bonus}"
            f"+timeliness={breakdown.timeliness_bonus}"
            f"(score={breakdown.timeliness_score})"
            f"+diff={breakdown.diff_bonus}+announce={breakdown.announce_bonus}"
            f"+classifier={breakdown.classifier_adjust}"
            f"×nov_factor={breakdown.novelty_factor}"
            f"{' [seo_spam]' if breakdown.seo_spam else ''} "
            f"(heuristics topic={breakdown.topic_base} trust={breakdown.source_trust} "
            f"service={breakdown.service_weight} no longer ranked)"
        ),
        event_id=event_id,
        event_phase=event_phase,
    )


def classify_publish_tier(*, topic: PublishTopic, page_text: str) -> PublishTier:
    """Breaking tier: scams, network incidents — immediate path, separate daily cap. Disabled by default (BREAKING_TIER_ENABLED, 2026-07-17): the keyword scan below false-positived on ordinary positive infrastructure claims (see config.py) — the topic-classification short-circuits above it stay live (SCAM_ALERT/NETWORK_INCIDENT still force human review), only the "skip the queue, prepend Breaking:" escalation is off."""
    if not config.BREAKING_TIER_ENABLED:
        return PublishTier.STANDARD
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
    """True for topics that always force human review (scam alert / network incident)."""
    return topic in (PublishTopic.SCAM_ALERT, PublishTopic.NETWORK_INCIDENT)


def build_dedupe_key(
    *,
    service_id: str,
    topic: str,
    content_hash: str,
    tier: str = "standard",
) -> str:
    """Build the dedupe key used to suppress repeat queue entries for the same content."""
    short_hash = content_hash[:16] if content_hash else "none"
    return f"{service_id}:{topic}:{tier}:{short_hash}"


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(p in text for p in phrases)


# How close a context phrase and an alarm word must be (characters) to count
# as one signal. _SCAM_CONTEXT_PHRASES' own docstring already says these "only
# signal a scam ALONGSIDE actual alarm language" — but the check was anywhere-
# in-the-page, not actually alongside, so e.g. a wallet's "Opt-in to Asset"
# feature docs plus an unrelated ToS "risk warnings" clause thousands of
# characters later (2026-07-10: perawallet.app, misfired as SCAM_ALERT/breaking
# tier every ~5min via the breaking-queue drain) counted as a match. A same-
# paragraph window catches genuine cases ("beware this rekey scam") without
# the whole-document false positive.
_SCAM_PROXIMITY_WINDOW = 200


def _context_and_alarm_nearby(
    text: str, context_phrases: tuple[str, ...], alarm_words: tuple[str, ...]
) -> bool:
    for ctx in context_phrases:
        start = 0
        while True:
            idx = text.find(ctx, start)
            if idx == -1:
                break
            lo = max(0, idx - _SCAM_PROXIMITY_WINDOW)
            hi = idx + len(ctx) + _SCAM_PROXIMITY_WINDOW
            if any(alarm in text[lo:hi] for alarm in alarm_words):
                return True
            start = idx + len(ctx)
    return False


def _diff_mentions_pricing(diff: str | None) -> bool:
    if not diff:
        return False
    lower = diff.lower()
    return any(p in lower for p in _PRICING_PHRASES)


def is_significant_diff(diff: str | None, *, min_lines: int | None = None) -> bool:
    """True when the diff adds at least the configured (or given) number of lines."""
    if not diff or not diff.strip():
        return False
    threshold = min_lines if min_lines is not None else config.NEWS_MIN_DIFF_LINES
    added = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
    return added >= threshold


def diff_is_reformat(diff: str | None) -> bool:
    """True when the diff's added text is mostly the SAME WORDS as its removed text — a page redesign/reflow, not new information. Line counting can't see this (a reformat trivially clears NEWS_MIN_DIFF_LINES), so compare the two sides' token sets: high overlap = reshuffled content, no story.

    Conservative by construction: pure additions (nothing removed) are never a
    reformat, and a small removed side (< 20 distinct tokens) is too little
    evidence to veto on.
    """
    threshold = config.NEWS_REFORMAT_SIMILARITY
    if threshold <= 0 or not diff:
        return False
    added_tokens: set[str] = set()
    removed_tokens: set[str] = set()
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added_tokens.update(line[1:].lower().split())
        elif line.startswith("-"):
            removed_tokens.update(line[1:].lower().split())
    if len(removed_tokens) < 20 or not added_tokens:
        return False
    union = added_tokens | removed_tokens
    overlap = len(added_tokens & removed_tokens) / len(union)
    return overlap >= threshold


def evaluate_enqueue(
    kind: PublishKind,
    *,
    diff: str | None = None,
    source_kind: str | None = None,
    relevance: float | None = None,
) -> PublishDecision:
    """Whether a crawl change should enter the publish queue (no daily cap).

    The diff-size floor assumes a page with a real "previous version" to
    compare against — meaningless for a standalone social post (diffed
    against an empty previous, so a short single-paragraph post rarely
    reaches NEWS_MIN_DIFF_LINES). The post's existence is the signal, same as
    discovery; source_kind="bluesky" is exempt.

    The relevance floor only applies to CONTENT_UPDATE: a service is already
    vetted (as a domain, not per-diff) at discovery, so a low-relevance diff
    would otherwise sail into the queue on relevance alone — priority scoring
    scales by relevance but never gates on it, so a barely-relevant service
    can still win a drain slot simply by being the best (or only) candidate
    ready at that moment.
    """
    if kind == PublishKind.WEEKLY_DIGEST:
        return PublishDecision(kind=kind, allowed=False, reason="weekly_not_queued")

    if kind == PublishKind.CONTENT_UPDATE and source_kind != "bluesky":
        if not is_significant_diff(diff):
            return PublishDecision(
                kind=kind,
                allowed=False,
                reason="diff_too_small",
            )
        if diff_is_reformat(diff):
            return PublishDecision(
                kind=kind,
                allowed=False,
                reason="diff_reformat_only",
            )
        if relevance is not None and relevance < config.CONTENT_UPDATE_RELEVANCE_FLOOR:
            return PublishDecision(
                kind=kind,
                allowed=False,
                reason="relevance_too_low",
            )

    return PublishDecision(kind=kind, allowed=True, reason="ok")


def evaluate_standard_publish(
    kind: PublishKind,
    *,
    diff: str | None = None,
    when: datetime | None = None,
    source_kind: str | None = None,
) -> PublishDecision:
    """Standard queue drain: cap + ~3h spacing between posts."""
    enqueue_decision = evaluate_enqueue(kind, diff=diff, source_kind=source_kind)
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
    source_kind: str | None = None,
) -> PublishDecision:
    """Breaking drain: separate cap, no interval — publish when credible."""
    enqueue_decision = evaluate_enqueue(kind, diff=diff, source_kind=source_kind)
    if not enqueue_decision.allowed:
        return enqueue_decision

    if remaining_breaking_publish_slots(when=when) <= 0:
        return PublishDecision(
            kind=kind,
            allowed=False,
            reason="breaking_daily_cap_reached",
        )

    return PublishDecision(kind=kind, allowed=True, reason="ok")


def trim_text_to_chars(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, preferring a paragraph/sentence boundary and appending an ellipsis."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    for sep in ("\n\n", "\n", ". "):
        idx = cut.rfind(sep)
        if idx > max_chars * 0.6:
            return cut[:idx].rstrip() + "…"
    return cut.rstrip() + "…"



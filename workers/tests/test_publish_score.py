from datetime import date

from app.modules.newspaper.publish_policy import PublishKind, PublishTopic
from app.modules.newspaper.publish_score import compute_priority
from app.modules.newspaper.source_trust import source_trust_bonus


def test_official_mail_high_trust(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modules.newspaper.source_trust.config.OFFICIAL_MAIL_FROM_DOMAINS",
        "algorand.foundation",
    )
    bonus = source_trust_bonus(
        source_kind="mail",
        mail_from="news@algorand.foundation",
    )
    assert bonus == 25


def test_relevant_page_outranks_offtopic() -> None:
    # Selection is driven by relevance (+ novelty/timeliness when scored): an
    # on-topic Algorand page must outrank an off-topic one.
    relevant = compute_priority(
        topic=PublishTopic.NEW_SERVICE,
        publish_kind=PublishKind.SERVICE_DISCOVERY,
        page_text="Algorand mainnet ASA governance staking update for the Algorand ecosystem.",
        diff=None,
        source_kind="web",
    )
    offtopic = compute_priority(
        topic=PublishTopic.NEW_SERVICE,
        publish_kind=PublishKind.SERVICE_DISCOVERY,
        page_text="GPU computing leader. Buy our graphics cards. Coming soon.",
        diff=None,
        source_kind="web",
    )
    assert relevant.relevance_bonus > offtopic.relevance_bonus
    assert relevant.total > offtopic.total


def test_priority_is_relevance_novelty_and_timeliness() -> None:
    # Total must equal relevance + novelty + timeliness bonuses — heuristic
    # sub-scores (topic_base/trust/service_weight) are reported but not ranked.
    b = compute_priority(
        topic=PublishTopic.SDK_RELEASE,
        publish_kind=PublishKind.CONTENT_UPDATE,
        page_text="Algorand SDK release notes.",
        diff="+++ a\n+ x\n+ y\n+ z",
        source_kind="web",
        relevance=0.5,
        novelty=0.4,
        timeliness=0.6,
    )
    assert b.total == b.relevance_bonus + b.novelty_bonus + b.timeliness_bonus
    assert b.topic_base > 0  # still computed for observability


def test_fresh_story_outranks_stale_pr_at_equal_relevance() -> None:
    """Timeliness bonus must pull a fresh item ahead of a stale one."""
    body = (
        "Noah partners with Algorand for regulated fiat rails across the ecosystem. "
        "Initial implementations planned for 2026."
    )
    today = date(2026, 6, 29)
    stale = compute_priority(
        topic=PublishTopic.NEW_SERVICE,
        publish_kind=PublishKind.SERVICE_DISCOVERY,
        page_title="Noah and Algorand partnership",
        page_text=body,
        published_at="2025-11-15T10:00:00Z",
        diff=None,
        source_kind="web",
        relevance=0.9,
        novelty=1.0,
        today=today,
    )
    fresh = compute_priority(
        topic=PublishTopic.NEW_SERVICE,
        publish_kind=PublishKind.SERVICE_DISCOVERY,
        page_title="New Algorand DeFi integration",
        page_text="A new Algorand DeFi integration launches this week on mainnet.",
        published_at="2026-06-29T10:00:00Z",
        diff=None,
        source_kind="web",
        relevance=0.9,
        novelty=1.0,
        today=today,
    )
    assert stale.timeliness_score == 0.0
    assert fresh.timeliness_score == 1.0
    assert fresh.total > stale.total
    assert fresh.timeliness_bonus > stale.timeliness_bonus


def test_sdk_topic_base_high() -> None:
    breakdown = compute_priority(
        topic=PublishTopic.SDK_RELEASE,
        publish_kind=PublishKind.CONTENT_UPDATE,
        page_text="SDK v2 release",
        diff="+++ a\n+ line\n+ two\n+ three",
        source_kind="web",
    )
    assert breakdown.topic_base == 90

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


def test_priority_components_sum_for_updates() -> None:
    # For content updates the total is the sum of the ranked components —
    # heuristic sub-scores (topic_base/trust/service_weight) are reported but
    # not ranked.
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
    assert b.total == (
        b.relevance_bonus + b.novelty_bonus + b.timeliness_bonus
        + b.diff_bonus + b.announce_bonus
    )
    assert b.diff_bonus > 0  # 3 added lines earn partial diff credit
    assert b.topic_base > 0  # still computed for observability


def test_fresh_story_outranks_stale_pr_at_equal_relevance() -> None:
    """Timeliness bonus must pull a fresh update ahead of a stale one."""
    body = (
        "Noah partners with Algorand for regulated fiat rails across the ecosystem. "
        "Initial implementations planned for 2026."
    )
    today = date(2026, 6, 29)
    stale = compute_priority(
        topic=PublishTopic.NEW_SERVICE,
        publish_kind=PublishKind.CONTENT_UPDATE,
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
        publish_kind=PublishKind.CONTENT_UPDATE,
        page_title="Noah and Algorand partnership",
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


def test_seo_spam_forfeits_timeliness_and_announce() -> None:
    """A price-prediction farm page keeps relevance but earns no freshness or
    announcement credit — real news at equal relevance must outrank it."""
    spam = compute_priority(
        topic=PublishTopic.NEW_SERVICE,
        publish_kind=PublishKind.CONTENT_UPDATE,
        page_title="Algorand (ALGO) Price Prediction 2024, 2025 - 2030",
        page_text="Algorand price prediction and forecast. Should you buy ALGO?",
        diff=None,
        source_kind="web",
        relevance=0.4,
        novelty=1.0,
        timeliness=1.0,
    )
    news = compute_priority(
        topic=PublishTopic.NEW_SERVICE,
        publish_kind=PublishKind.CONTENT_UPDATE,
        page_title="Brale Brings Regulated Stablecoin Issuance to Algorand",
        page_text="Brale brings regulated stablecoin issuance to Algorand mainnet.",
        diff=None,
        source_kind="web",
        relevance=0.4,
        novelty=1.0,
        timeliness=0.5,
    )
    assert spam.seo_spam and not news.seo_spam
    assert spam.timeliness_bonus == 0 and spam.announce_bonus == 0
    assert news.announce_bonus > 0  # "Brings" is announcement-shaped
    assert news.total > spam.total


def test_discovery_is_flat_and_below_substantive_update() -> None:
    """Discoveries score flat relevance-scaled points (one shot each); a real
    diff-driven update at similar relevance must outrank them."""
    discovery = compute_priority(
        topic=PublishTopic.NEW_SERVICE,
        publish_kind=PublishKind.SERVICE_DISCOVERY,
        page_title="AlgoLeagues NFT Trading Card Game",
        page_text="Algorand NFT trading card game arena.",
        diff=None,
        source_kind="web",
        relevance=0.4,
        novelty=1.0,
        timeliness=1.0,
    )
    update = compute_priority(
        topic=PublishTopic.CONTENT_UPDATE,
        publish_kind=PublishKind.CONTENT_UPDATE,
        page_title="Changelog",
        page_text="Algorand wallet adds passkey support in this update.",
        diff="+++ a\n" + "\n".join(f"+ line {i}" for i in range(40)),
        source_kind="web",
        relevance=0.4,
        novelty=1.0,
        timeliness=0.5,
    )
    assert discovery.diff_bonus == 0
    assert update.diff_bonus > 0
    assert update.total > discovery.total


def test_classifier_verdict_nudges_priority() -> None:
    """Confident learned verdicts adjust rank; None (training mode) is inert."""
    kwargs = dict(
        topic=PublishTopic.CONTENT_UPDATE,
        publish_kind=PublishKind.CONTENT_UPDATE,
        page_title="Update",
        page_text="Algorand mainnet update.",
        diff=None,
        source_kind="web",
        relevance=0.5,
        novelty=1.0,
        timeliness=0.5,
    )
    neutral = compute_priority(**kwargs)
    boosted = compute_priority(
        **kwargs, classifier_publish=True, classifier_confidence=0.9
    )
    demoted = compute_priority(
        **kwargs, classifier_publish=False, classifier_confidence=0.9
    )
    inert = compute_priority(
        **kwargs, classifier_publish=None, classifier_confidence=0.9
    )
    assert boosted.total > neutral.total
    assert demoted.total < neutral.total
    assert inert.total == neutral.total
    assert demoted.classifier_adjust < 0 < boosted.classifier_adjust


def test_sdk_topic_base_high() -> None:
    breakdown = compute_priority(
        topic=PublishTopic.SDK_RELEASE,
        publish_kind=PublishKind.CONTENT_UPDATE,
        page_text="SDK v2 release",
        diff="+++ a\n+ line\n+ two\n+ three",
        source_kind="web",
    )
    assert breakdown.topic_base == 90

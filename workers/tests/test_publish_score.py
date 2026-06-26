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
    # Selection is driven by relevance + novelty only: an on-topic Algorand page
    # must outrank an off-topic one regardless of length/topic heuristics.
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


def test_priority_is_relevance_plus_novelty_only() -> None:
    # Total must equal relevance_bonus + novelty_bonus — the heuristic sub-scores
    # (topic_base/trust/service_weight) are reported but no longer ranked.
    b = compute_priority(
        topic=PublishTopic.SDK_RELEASE,  # high topic_base, must NOT affect total
        publish_kind=PublishKind.CONTENT_UPDATE,
        page_text="Algorand SDK release notes.",
        diff="+++ a\n+ x\n+ y\n+ z",
        source_kind="web",
        relevance=0.5,
        novelty=0.4,
    )
    assert b.total == b.relevance_bonus + b.novelty_bonus
    assert b.topic_base > 0  # still computed for observability


def test_sdk_topic_base_high() -> None:
    breakdown = compute_priority(
        topic=PublishTopic.SDK_RELEASE,
        publish_kind=PublishKind.CONTENT_UPDATE,
        page_text="SDK v2 release",
        diff="+++ a\n+ line\n+ two\n+ three",
        source_kind="web",
    )
    assert breakdown.topic_base == 90

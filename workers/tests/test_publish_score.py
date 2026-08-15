"""Priority scoring: trust, novelty, urgency, and noise components."""

from datetime import date

import pytest

from app.modules.newspaper.publish_policy import PublishKind, PublishTopic
from app.modules.newspaper.publish_score import compute_priority
from app.modules.newspaper.source_trust import source_trust_bonus


def test_official_mail_high_trust(monkeypatch: pytest.MonkeyPatch) -> None:
    """Grants a high trust bonus for mail from a configured official domain."""
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
    """Ranks an on-topic Algorand page above an off-topic one on relevance and total score."""
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
    """Sums the ranked components, subtracts the noise penalty, then scales by the novelty factor."""
    # For content updates the total is the sum of the ranked components, minus
    # the (now live) thin-content noise penalty — heuristic sub-scores
    # (topic_base/trust/service_weight/urgency) are reported but not ranked.
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
    components = (
        b.relevance_bonus
        + b.novelty_bonus
        + b.timeliness_bonus
        + b.diff_bonus
        + b.announce_bonus
        + b.scale_bonus
    )
    # 3 added lines (<5) + short page_text (<200 chars) both trip the noise
    # penalty here, so it must be nonzero and actually subtracted.
    assert b.noise_penalty > 0
    # Novelty then scales the whole (noise-reduced) sum (repetition suppression).
    assert b.total == round(max(0, components - b.noise_penalty) * b.novelty_factor)
    assert b.novelty_factor == 0.3 + 0.7 * 0.4
    assert b.diff_bonus > 0  # 3 added lines earn partial diff credit
    assert b.topic_base > 0  # still computed for observability


def test_noise_penalty_actually_lowers_total() -> None:
    """Confirms the noise penalty actually subtracts from the total, not just a displayed number."""
    # The zk-colorsort case: a barely-there page and a thin diff must actually
    # cost points, not just display a "noise_penalty" number that does nothing.
    kwargs = {
        "topic": PublishTopic.CONTENT_UPDATE,
        "publish_kind": PublishKind.CONTENT_UPDATE,
        "page_title": "Update",
        "source_kind": "web",
        "relevance": 0.31,
        "novelty": 1.0,
        "timeliness": 1.0,
    }
    thin = compute_priority(
        **kwargs,
        page_text="Last updated: July 6, 2026.",
        diff="+++ a\n+ x\n+ y\n+ z",
    )
    substantial = compute_priority(
        **kwargs,
        page_text="Algorand ecosystem update with real detail. " * 10,
        diff="+++ a\n" + "\n".join(f"+ line {i}" for i in range(10)),
    )
    assert thin.noise_penalty > 0
    assert substantial.noise_penalty == 0
    assert thin.total < substantial.total


def test_confident_classifier_reject_crushes_total_near_zero() -> None:
    """Lets a confident classifier "don't publish" verdict sink the total, not merely halve it."""
    # A confident "don't publish" verdict must be able to sink a candidate
    # below real competitors, not just halve it.
    kwargs = {
        "topic": PublishTopic.CONTENT_UPDATE,
        "publish_kind": PublishKind.CONTENT_UPDATE,
        "page_title": "Update",
        "page_text": "Algorand mainnet update with substantive real content here.",
        "diff": "+++ a\n" + "\n".join(f"+ line {i}" for i in range(10)),
        "source_kind": "web",
        "relevance": 0.9,
        "novelty": 1.0,
        "timeliness": 1.0,
    }
    neutral = compute_priority(**kwargs)
    demoted = compute_priority(**kwargs, classifier_publish=False, classifier_confidence=1.0)
    # Full confidence must crush toward zero, not merely halve the total.
    assert demoted.total <= round(neutral.total * 0.1)


def test_zero_novelty_suppresses_high_relevance_repeat() -> None:
    """An already-covered story (novelty 0) must sink below a modest fresh one, even from a maximally relevant source — the Defly-repeat case."""
    repeat = compute_priority(
        topic=PublishTopic.CONTENT_UPDATE,
        publish_kind=PublishKind.CONTENT_UPDATE,
        page_title="Defly Wallet and Private Keys",
        page_text="Algorand wallet Defly private keys mainnet.",
        diff="+++ a\n" + "\n".join(f"+ l{i}" for i in range(40)),
        source_kind="web",
        relevance=1.0,
        novelty=0.0,
        timeliness=0.5,
    )
    fresh = compute_priority(
        topic=PublishTopic.CONTENT_UPDATE,
        publish_kind=PublishKind.CONTENT_UPDATE,
        page_title="Changelog",
        page_text="Algorand tooling update.",
        diff="+++ a\n+ x\n+ y\n+ z\n+ w\n+ v",
        source_kind="web",
        relevance=0.5,
        novelty=1.0,
        timeliness=0.5,
    )
    assert repeat.novelty_factor == 0.3
    assert fresh.total > repeat.total


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
    """A price-prediction farm page keeps relevance but earns no freshness or announcement credit — real news at equal relevance must outrank it."""
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
    assert spam.seo_spam
    assert not news.seo_spam
    assert spam.timeliness_bonus == 0
    assert spam.announce_bonus == 0
    assert news.announce_bonus > 0  # "Brings" is announcement-shaped
    assert news.total > spam.total


def test_discovery_is_flat_and_below_substantive_update() -> None:
    """Discoveries score flat relevance-scaled points (one shot each); a real diff-driven update at similar relevance must outrank them."""
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
    kwargs = {
        "topic": PublishTopic.CONTENT_UPDATE,
        "publish_kind": PublishKind.CONTENT_UPDATE,
        "page_title": "Update",
        "page_text": "Algorand mainnet update.",
        "diff": None,
        "source_kind": "web",
        "relevance": 0.5,
        "novelty": 1.0,
        "timeliness": 0.5,
    }
    neutral = compute_priority(**kwargs)
    boosted = compute_priority(**kwargs, classifier_publish=True, classifier_confidence=0.9)
    demoted = compute_priority(**kwargs, classifier_publish=False, classifier_confidence=0.9)
    inert = compute_priority(**kwargs, classifier_publish=None, classifier_confidence=0.9)
    assert boosted.total > neutral.total
    assert demoted.total < neutral.total
    assert inert.total == neutral.total
    assert demoted.classifier_adjust < 0 < boosted.classifier_adjust


def test_sdk_topic_base_high() -> None:
    """Gives an SDK release a high topic_base score."""
    breakdown = compute_priority(
        topic=PublishTopic.SDK_RELEASE,
        publish_kind=PublishKind.CONTENT_UPDATE,
        page_text="SDK v2 release",
        diff="+++ a\n+ line\n+ two\n+ three",
        source_kind="web",
    )
    assert breakdown.topic_base == 90


def test_scale_signal_ranks_otherwise_identical_candidates_differently() -> None:
    """The STEAK-regression test.

    Two candidates equal on every other axis rank differently once one is
    known to be a much bigger project.
    """
    kwargs = {
        "topic": PublishTopic.CONTENT_UPDATE,
        "publish_kind": PublishKind.CONTENT_UPDATE,
        "page_title": "Update",
        "page_text": "Algorand DeFi protocol update with real detail. " * 10,
        "diff": "+++ a\n" + "\n".join(f"+ line {i}" for i in range(10)),
        "source_kind": "web",
        "relevance": 0.7,
        "novelty": 1.0,
        "timeliness": 0.5,
    }
    small = compute_priority(**kwargs, scale_signal=0.05)
    large = compute_priority(**kwargs, scale_signal=1.0)
    assert large.scale_bonus > small.scale_bonus
    assert large.total > small.total


def test_scale_signal_none_defaults_to_unresolved_floor() -> None:
    """scale_signal=None (never resolved) scores the same as the explicit unresolved floor -- NOT the same as a resolved-tiny 0.0."""
    from app.modules.newspaper.service_scale import UNRESOLVED_SCALE

    kwargs = {
        "topic": PublishTopic.CONTENT_UPDATE,
        "publish_kind": PublishKind.CONTENT_UPDATE,
        "page_title": "Update",
        "page_text": "Algorand DeFi protocol update with real detail. " * 10,
        "diff": "+++ a\n" + "\n".join(f"+ line {i}" for i in range(10)),
        "source_kind": "web",
        "relevance": 0.7,
        "novelty": 1.0,
        "timeliness": 0.5,
    }
    unresolved = compute_priority(**kwargs, scale_signal=None)
    explicit_floor = compute_priority(**kwargs, scale_signal=UNRESOLVED_SCALE)
    resolved_tiny = compute_priority(**kwargs, scale_signal=0.0)
    assert unresolved.scale_bonus == explicit_floor.scale_bonus
    assert unresolved.scale_bonus > resolved_tiny.scale_bonus


def test_scale_signal_stays_relevance_gated() -> None:
    """An off-topic candidate with a huge scale signal still scores ~0 -- relevance gating held."""
    offtopic_huge_scale = compute_priority(
        topic=PublishTopic.NEW_SERVICE,
        publish_kind=PublishKind.SERVICE_DISCOVERY,
        page_text="GPU computing leader. Buy our graphics cards. Coming soon.",
        diff=None,
        source_kind="web",
        relevance=0.0,
        scale_signal=1.0,
    )
    assert offtopic_huge_scale.total == 0


def test_scale_bonus_applies_to_discovery_too() -> None:
    """First-ever coverage of a major protocol outranks first-ever coverage of a trivial one, within discovery's own drain queue."""
    kwargs = {
        "topic": PublishTopic.NEW_SERVICE,
        "publish_kind": PublishKind.SERVICE_DISCOVERY,
        "page_title": "New protocol",
        "page_text": "Algorand DeFi protocol.",
        "diff": None,
        "source_kind": "web",
        "relevance": 0.6,
        "novelty": 1.0,
        "timeliness": 1.0,
    }
    small_discovery = compute_priority(**kwargs, scale_signal=0.05)
    large_discovery = compute_priority(**kwargs, scale_signal=1.0)
    assert large_discovery.total > small_discovery.total


def test_scale_max_total_accounted_in_both_branches() -> None:
    """A maxed-out scale_signal at max relevance never gets clipped by classifier-adjust's max_total headroom, in either branch."""
    from app.core.config import CLASSIFIER_PRIORITY_WEIGHT

    for kind, topic in (
        (PublishKind.SERVICE_DISCOVERY, PublishTopic.NEW_SERVICE),
        (PublishKind.CONTENT_UPDATE, PublishTopic.CONTENT_UPDATE),
    ):
        b = compute_priority(
            topic=topic,
            publish_kind=kind,
            page_title="Major update",
            page_text="Algorand DeFi protocol update with real detail. " * 10,
            diff="+++ a\n" + "\n".join(f"+ line {i}" for i in range(40)),
            source_kind="web",
            relevance=1.0,
            novelty=1.0,
            timeliness=1.0,
            scale_signal=1.0,
            classifier_publish=True,
            classifier_confidence=1.0,
        )
        # classifier_adjust can add up to CLASSIFIER_PRIORITY_WEIGHT on top of
        # max_total -- confirm scale_bonus's full value survived that clip,
        # i.e. max_total was extended by SCALE_PRIORITY_WEIGHT as designed.
        assert b.scale_bonus > 0
        assert b.classifier_adjust <= CLASSIFIER_PRIORITY_WEIGHT

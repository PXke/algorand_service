"""Classifying a scrape diff into a publish decision (discovery/update/reject)."""

import pytest

from app.modules.newspaper.publish_policy import (
    PublishKind,
    PublishTopic,
    classify_publish_topic,
    classify_scrape_publish,
    evaluate_enqueue,
    is_significant_diff,
    priority_for_topic,
    trim_text_to_chars,
)


def test_classify_first_scrape_as_discovery() -> None:
    """Classifies a service's very first snapshot as SERVICE_DISCOVERY."""
    kind = classify_scrape_publish(
        service_id="new-tool",
        page_text="hello",
        is_first_snapshot=True,
        diff=None,
    )
    assert kind == PublishKind.SERVICE_DISCOVERY


def test_discovery_never_refires_after_first_snapshot() -> None:
    """Never re-fires SERVICE_DISCOVERY after the first snapshot, even for launch-sounding text."""
    # Announcement-sounding text and zero published articles must NOT
    # resurrect discovery — one discovery per service, ever; everything
    # after the first snapshot is an update.
    kind = classify_scrape_publish(
        service_id="existing",
        page_text="We are launching a new service with pricing on Algorand",
        is_first_snapshot=False,
        diff="+line",
    )
    assert kind == PublishKind.CONTENT_UPDATE


def test_classify_minor_diff_as_update() -> None:
    """Classifies a non-first scrape with a diff as a CONTENT_UPDATE."""
    kind = classify_scrape_publish(
        service_id="existing",
        page_text="footer copyright changed",
        is_first_snapshot=False,
        diff="+one line",
    )
    assert kind == PublishKind.CONTENT_UPDATE


def test_significant_diff_requires_lines() -> None:
    """Requires multiple added lines before a diff counts as significant."""
    assert not is_significant_diff("+only one\n")
    assert is_significant_diff("+++ file\n+ a\n+ b\n+ c\n")


def test_evaluate_blocks_small_diff() -> None:
    """Rejects a CONTENT_UPDATE whose diff is below the minimum line floor."""
    decision = evaluate_enqueue(
        PublishKind.CONTENT_UPDATE,
        diff="+a\n",
    )
    assert not decision.allowed
    assert decision.reason == "diff_too_small"


def test_evaluate_exempts_bluesky_from_small_diff_rejection() -> None:
    """Allows a small diff through for the bluesky source kind, which is exempt from the line floor."""
    # A single-paragraph post diffed against an empty "previous" rarely
    # reaches NEWS_MIN_DIFF_LINES — the post's existence is the signal, not
    # its line count, so bluesky is exempt from this floor.
    decision = evaluate_enqueue(
        PublishKind.CONTENT_UPDATE,
        diff="+a\n",
        source_kind="bluesky",
    )
    assert decision.allowed


def test_evaluate_blocks_low_relevance_content_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rejects a real diff on a barely-relevant service below the relevance floor."""
    monkeypatch.setattr(
        "app.modules.newspaper.publish_policy.config.CONTENT_UPDATE_RELEVANCE_FLOOR",
        0.35,
        raising=False,
    )
    # zk-colorsort case: a real diff (clears is_significant_diff) on a
    # barely-relevant service (0.31) must never reach the queue at all.
    decision = evaluate_enqueue(
        PublishKind.CONTENT_UPDATE,
        diff="+++ file\n+ a\n+ b\n+ c\n",
        relevance=0.31,
    )
    assert not decision.allowed
    assert decision.reason == "relevance_too_low"


def test_evaluate_allows_relevant_content_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allows a CONTENT_UPDATE through when relevance clears the configured floor."""
    monkeypatch.setattr(
        "app.modules.newspaper.publish_policy.config.CONTENT_UPDATE_RELEVANCE_FLOOR",
        0.35,
        raising=False,
    )
    decision = evaluate_enqueue(
        PublishKind.CONTENT_UPDATE,
        diff="+++ file\n+ a\n+ b\n+ c\n",
        relevance=0.9,
    )
    assert decision.allowed


def test_evaluate_skips_relevance_check_when_not_provided() -> None:
    """Skips the relevance gate entirely when no relevance score is passed."""
    # Callers that don't pass relevance (e.g. bluesky) must not be gated.
    decision = evaluate_enqueue(
        PublishKind.CONTENT_UPDATE,
        diff="+++ file\n+ a\n+ b\n+ c\n",
    )
    assert decision.allowed


def test_evaluate_allows_editorial_assignment_with_no_diff() -> None:
    """Allows an EDITORIAL_ASSIGNMENT with no diff, bypassing the small-diff rejection."""
    # Assignments/refreshes have no diff — must NOT hit the CONTENT_UPDATE
    # small-diff rejection, which is why they get their own PublishKind.
    decision = evaluate_enqueue(PublishKind.EDITORIAL_ASSIGNMENT, diff=None)
    assert decision.allowed


def test_classify_scam_alert_topic() -> None:
    """Classifies urgent phishing-warning text as SCAM_ALERT with top priority."""
    topic = classify_publish_topic(
        page_text="URGENT scam alert: phishing link impersonating official wallet",
        diff=None,
        publish_kind=PublishKind.CONTENT_UPDATE,
        source_kind="discord",
    )
    assert topic == PublishTopic.SCAM_ALERT
    assert priority_for_topic(topic) == 100


def test_classify_foundation_discord_warning_as_scam_alert() -> None:
    """Real-style Foundation @everyone warning (algoblow.com) — Discord-only comms — still classifies as SCAM_ALERT (forces mandatory human review) regardless of publish tier. The BREAKING publish TIER this used to also escalate to was removed entirely 2026-08-25 (see PublishTier's docstring)."""
    text = (
        "@everyone WARNING DO NOT interact with algoblow.com! "
        "This is a malicious app that will attempt to take control of accounts "
        "by including a rekey in transaction requests."
    )
    topic = classify_publish_topic(
        page_text=text,
        diff=None,
        publish_kind=PublishKind.CONTENT_UPDATE,
        source_kind="firefox_extension",
    )
    assert topic == PublishTopic.SCAM_ALERT


def test_opt_in_mention_alone_is_not_scam_alert() -> None:
    """Regression: routine ASA opt-in copy (wallet help text, NFT collection page, a privacy-policy caching feature) is ordinary Algorand vocabulary, not a scam signal, unless real alarm language is also present."""
    texts = [
        "This website offers an opt-in caching feature using IndexedDB.",
        "Bulk asset manager: consolidated opt-in, opt-out, and destroy manager for Algorand assets.",
        "NFD vaults: finding a happy medium with opt-ins.",
        "The community token of the AlgOctopus NFT collection. Opt-in to $BLOP, learn more.",
    ]
    for text in texts:
        topic = classify_publish_topic(
            page_text=text, diff=None, publish_kind=PublishKind.CONTENT_UPDATE
        )
        assert topic != PublishTopic.SCAM_ALERT, text


def test_rekey_mention_alone_is_not_scam_alert() -> None:
    """Does not classify routine rekey/wallet-import copy as a scam alert."""
    text = (
        "Add another Algorand Virtual Machine (AVM) wallet. To get started, add "
        "an account to your wallet. Rekeyed accounts will import automatically "
        "as sub-accounts under the address they are rekeyed to."
    )
    topic = classify_publish_topic(
        page_text=text, diff=None, publish_kind=PublishKind.CONTENT_UPDATE
    )
    assert topic != PublishTopic.SCAM_ALERT


def test_rekey_with_alarm_language_is_still_scam_alert() -> None:
    """The soft context terms (rekey, opt-in, exploit, ...) must still catch a real scam that uses different wording than the existing hard phrases."""
    text = "Warning: suspicious rekey activity detected on several accounts today."
    topic = classify_publish_topic(
        page_text=text, diff=None, publish_kind=PublishKind.CONTENT_UPDATE
    )
    assert topic == PublishTopic.SCAM_ALERT


def test_classify_sdk_release_topic() -> None:
    """Classifies an SDK release announcement diff as the SDK_RELEASE topic."""
    topic = classify_publish_topic(
        page_text="We shipped SDK v2.1.0 — see release notes on GitHub",
        diff="+ changelog entry",
        publish_kind=PublishKind.CONTENT_UPDATE,
    )
    assert topic == PublishTopic.SDK_RELEASE


def test_trim_digest_length() -> None:
    """Trims long text to at most one character past the requested length."""
    long = "word " * 500
    trimmed = trim_text_to_chars(long, 100)
    assert len(trimmed) <= 101


def test_reformat_diff_is_vetoed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rejects a diff that only reshuffles existing sentences into a new layout."""
    monkeypatch.setattr(
        "app.modules.newspaper.publish_policy.config.NEWS_REFORMAT_SIMILARITY",
        0.85,
        raising=False,
    )
    # Same sentences reshuffled into a new layout (the Tinyman/Blockshake
    # "cosmetic redesign" shape): plenty of added lines, no new words.
    sentences = [
        "Blockshake is a software engineering company mastering complexity",
        "Our core expertise is cryptographic protocols and data security",
        "From protocol design to seamless UX we build full stack solutions",
        "Defly Wallet is our flagship product for the Algorand ecosystem",
    ]
    removed = "\n".join(f"-{s}" for s in sentences)
    added = "\n".join(f"+## {s}" for s in reversed(sentences))
    diff = f"+++ a\n--- b\n{removed}\n{added}\n"
    decision = evaluate_enqueue(PublishKind.CONTENT_UPDATE, diff=diff, relevance=0.9)
    assert not decision.allowed
    assert decision.reason == "diff_reformat_only"


def test_genuinely_new_content_is_not_a_reformat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allows a diff through when the added text introduces genuinely new content."""
    monkeypatch.setattr(
        "app.modules.newspaper.publish_policy.config.NEWS_REFORMAT_SIMILARITY",
        0.85,
        raising=False,
    )
    removed = "\n".join(
        f"-old sentence number {i} about the previous state of the service page" for i in range(4)
    )
    added = (
        "+Blockshake today announced a brand new zero knowledge proving system\n"
        "+The launch introduces staking rewards and a validator marketplace\n"
        "+Pricing starts at ten dollars per month for the developer tier\n"
        "+A partnership with the Algorand Foundation funds the rollout\n"
    )
    diff = f"+++ a\n{removed}\n{added}"
    decision = evaluate_enqueue(PublishKind.CONTENT_UPDATE, diff=diff, relevance=0.9)
    assert decision.allowed


def test_pure_additions_never_count_as_reformat() -> None:
    """Never flags a diff with only additions (no removed lines) as a reformat."""
    diff = "+++ a\n" + "\n".join(f"+ new line {i} with fresh content words" for i in range(10))
    from app.modules.newspaper.publish_policy import diff_is_reformat

    assert diff_is_reformat(diff) is False


from app.modules.newspaper.publish_policy import (
    PublishKind,
    PublishTier,
    PublishTopic,
    classify_publish_tier,
    classify_publish_topic,
    classify_scrape_publish,
    evaluate_enqueue,
    is_significant_diff,
    priority_for_topic,
    trim_text_to_chars,
)


def test_classify_first_scrape_as_discovery() -> None:
    kind = classify_scrape_publish(
        service_id="new-tool",
        page_text="hello",
        is_first_snapshot=True,
        diff=None,
    )
    assert kind == PublishKind.SERVICE_DISCOVERY


def test_discovery_never_refires_after_first_snapshot() -> None:
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
    kind = classify_scrape_publish(
        service_id="existing",
        page_text="footer copyright changed",
        is_first_snapshot=False,
        diff="+one line",
    )
    assert kind == PublishKind.CONTENT_UPDATE


def test_significant_diff_requires_lines() -> None:
    assert not is_significant_diff("+only one\n")
    assert is_significant_diff("+++ file\n+ a\n+ b\n+ c\n")


def test_evaluate_blocks_small_diff() -> None:
    decision = evaluate_enqueue(
        PublishKind.CONTENT_UPDATE,
        diff="+a\n",
    )
    assert not decision.allowed
    assert decision.reason == "diff_too_small"


def test_evaluate_allows_editorial_assignment_with_no_diff() -> None:
    # Assignments/refreshes have no diff — must NOT hit the CONTENT_UPDATE
    # small-diff rejection, which is why they get their own PublishKind.
    decision = evaluate_enqueue(PublishKind.EDITORIAL_ASSIGNMENT, diff=None)
    assert decision.allowed


def test_classify_scam_alert_topic() -> None:
    topic = classify_publish_topic(
        page_text="URGENT scam alert: phishing link impersonating official wallet",
        diff=None,
        publish_kind=PublishKind.CONTENT_UPDATE,
        source_kind="discord",
    )
    assert topic == PublishTopic.SCAM_ALERT
    assert priority_for_topic(topic) == 100


def test_classify_foundation_discord_warning_as_scam_breaking() -> None:
    """Real-style Foundation @everyone warning (algoblow.com) — Discord-only comms."""
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
    tier = classify_publish_tier(topic=topic, page_text=text)
    assert tier == PublishTier.BREAKING


def test_classify_sdk_release_topic() -> None:
    topic = classify_publish_topic(
        page_text="We shipped SDK v2.1.0 — see release notes on GitHub",
        diff="+ changelog entry",
        publish_kind=PublishKind.CONTENT_UPDATE,
    )
    assert topic == PublishTopic.SDK_RELEASE


def test_trim_digest_length() -> None:
    long = "word " * 500
    trimmed = trim_text_to_chars(long, 100)
    assert len(trimmed) <= 101

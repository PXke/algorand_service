"""Bluesky posts are ingested as a plain https://bsky.app/... URL. Without an explicit exemption, _compose_domain_for_row would misclassify every monitored Bluesky account as sharing ONE "bsky.app" domain cap/cooldown, throttling unrelated accounts against each other."""

from app.modules.newspaper.publish_queue_store import QueuedPublishRow
from app.modules.newspaper.tasks.publish_tasks import _compose_domain_for_row


def _row(*, source_kind: str, scrape_url: str) -> QueuedPublishRow:
    return QueuedPublishRow(
        queue_id="q1",
        priority=5,
        topic="",
        publish_kind="content_update",
        service_id="algorand-foundation-bsky:abc123",
        display_name="",
        scrape_url=scrape_url,
        payload={"source_kind": source_kind},
        created_at_epoch=0,
    )


def test_bluesky_post_is_exempt_from_domain_cap() -> None:
    """Returns no domain for a bluesky-kind row, exempting it from the per-domain cooldown cap."""
    row = _row(
        source_kind="bluesky",
        scrape_url="https://bsky.app/profile/algorand.foundation/post/abc123",
    )
    assert _compose_domain_for_row(row) == ""


def test_real_web_source_still_capped() -> None:
    """Returns the real domain for a plain web source, so its per-domain cap still applies."""
    row = _row(source_kind="web", scrape_url="https://perawallet.app/blog/post")
    assert _compose_domain_for_row(row) == "perawallet.app"

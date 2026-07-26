"""Detecting an event's lifecycle phase and topic framing."""

from app.modules.newspaper.event_lifecycle import EventPhase, detect_event_context
from app.modules.newspaper.publish_policy import PublishTopic, build_publish_intent


def test_community_announce_phase() -> None:
    """A page about an upcoming community call detects the announce phase and community-event topic."""
    ctx = detect_event_context(
        page_title="Community call",
        page_text="Join our community call in 2 days on governance.",
    )
    assert ctx is not None
    assert ctx.phase == EventPhase.ANNOUNCE
    assert ctx.topic_override == PublishTopic.COMMUNITY_EVENT


def test_community_recap_with_video() -> None:
    """A recap page with a video link detects the recap phase and community-recap topic."""
    ctx = detect_event_context(
        page_title="Recap",
        page_text="Recording: https://www.youtube.com/watch?v=abc123 recap of town hall",
    )
    assert ctx is not None
    assert ctx.phase == EventPhase.RECAP
    assert ctx.topic_override == PublishTopic.COMMUNITY_RECAP


def test_build_intent_uses_recap_topic() -> None:
    """build_publish_intent surfaces the recap topic and event phase for a recap-shaped page."""
    intent = build_publish_intent(
        service_id="foundation",
        page_text="Watch recap https://youtu.be/xyz town hall recording",
        page_title="Recap posted",
        is_first_snapshot=False,
        diff="+ video link",
        source_kind="mail",
        mail_from="news@algorand.foundation",
    )
    assert intent.topic == PublishTopic.COMMUNITY_RECAP
    assert intent.event_phase == "recap"

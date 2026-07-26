"""Detect an event's lifecycle phase (announce/live/recap) for topic framing."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from app.modules.newspaper.publish_policy import PublishTopic


class EventPhase(StrEnum):
    """Lifecycle phase of a detected event (announce/live/recap)."""

    ANNOUNCE = "announce"
    RECAP = "recap"


_VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com")


@dataclass(frozen=True)
class EventContext:
    """Detected event phase and topic override for one draft."""

    event_id: str
    phase: EventPhase
    topic_override: PublishTopic | None


def detect_event_context(*, page_text: str, page_title: str) -> EventContext | None:
    """Community calls: separate announce vs recap (video link after call)."""
    combined = f"{page_title}\n{page_text}"
    lower = combined.lower()

    has_call = any(
        p in lower for p in ("community call", "town hall", "ama", "office hours", "webinar")
    )
    has_video = _contains_video_url(combined)

    if has_call and not has_video:
        eid = _stable_event_id(combined, prefix="community")
        return EventContext(
            event_id=eid,
            phase=EventPhase.ANNOUNCE,
            topic_override=PublishTopic.COMMUNITY_EVENT,
        )

    if has_video and (has_call or "recap" in lower or "recording" in lower):
        eid = _stable_event_id(combined, prefix="community")
        return EventContext(
            event_id=eid,
            phase=EventPhase.RECAP,
            topic_override=PublishTopic.COMMUNITY_RECAP,
        )

    return None


def _contains_video_url(text: str) -> bool:
    lower = text.lower()
    return any(host in lower for host in _VIDEO_HOSTS)


def _stable_event_id(text: str, *, prefix: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def build_event_dedupe_key(
    *,
    service_id: str,
    event_id: str,
    phase: EventPhase,
    content_hash: str,
) -> str:
    """Build the compose-cooldown dedupe key for one service/event/phase/content combination."""
    short_hash = content_hash[:16] if content_hash else "none"
    return f"{service_id}:event:{event_id}:{phase.value}:{short_hash}"

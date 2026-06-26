from __future__ import annotations

import hashlib
import re

from app.core import config
from app.modules.newspaper.article_matching import resolve_publish_mode
from app.modules.newspaper.event_lifecycle import EventPhase, build_event_dedupe_key
from app.modules.newspaper.publish_policy import (
    PublishTier,
    build_dedupe_key,
    build_publish_intent,
    evaluate_enqueue,
)
from app.modules.newspaper.publish_queue_store import enqueue_publish
from app.modules.newspaper.service_profile_store import (
    get_stored_service_weight,
    upsert_service_profile,
)
from app.modules.newspaper.snapshot_store import (
    get_latest_snapshot,
    insert_snapshot,
    source_id_for_service,
)
from app.modules.pipeline.core.diffing import build_text_diff

# Volatile tokens that change on a page without the story changing: numbers,
# money amounts, percentages, clock times, and "N minutes/hours ago" phrasing.
# Stripped before hashing so a live price/counter tick is not seen as a content
# change (which would otherwise re-enqueue the same page every poll).
_VOLATILE_PATTERNS = (
    re.compile(r"\d+\s*(?:seconds?|minutes?|mins?|hours?|days?)\s+ago", re.IGNORECASE),
    re.compile(r"\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?", re.IGNORECASE),
    re.compile(r"[$€£]\s?\d[\d,]*(?:\.\d+)?"),
    re.compile(r"\d[\d,]*(?:\.\d+)?\s*%"),
    re.compile(r"\d[\d,]*(?:\.\d+)?"),
)


def _stable_content_hash(text: str) -> str:
    """Hash of the page with volatile numeric/time tokens removed, so pages that
    only update live data (prices, counters, timestamps) hash the same across
    polls and are correctly treated as ``unchanged``."""
    normalized = text
    for pat in _VOLATILE_PATTERNS:
        normalized = pat.sub("", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def ingest_publish_signal(
    *,
    service_id: str,
    display_name: str,
    source_url: str,
    page_title: str,
    page_text: str,
    source_kind: str | None,
    match_kind: str = "ingest",
    match_value: str = "",
    txid: str,
    round_num: int = 0,
    mail_from: str = "",
    mistral_only: bool = False,
    transcript_text: str = "",
    publish_mode: str = "",
    linked_article_id: str = "",
    og_image: str = "",
    published_at: str = "",
    inner_links: list[dict[str, str]] | None = None,
) -> dict[str, str]:
    """
    Shared enqueue path for crawl, mail, and other lanes after content is fetched.
    Updates snapshot, service profile, and publish queue.
    """
    from app.modules.ai.content_signals import compute_content_signals
    from app.modules.crawler.domain_tracker import url_recently_rejected
    from app.modules.newspaper.article_store import record_service_event
    from app.modules.newspaper.tasks.queue_drain_tasks import drain_breaking_publish_queue

    def _record_event() -> None:
        record_service_event(
            service_id=service_id,
            txid=txid,
            round_num=round_num,
            match_kind=match_kind,
            match_value=match_value,
        )

    def _skip(reason: str) -> dict[str, str]:
        _record_event()
        return {"status": "skipped", "reason": reason, "txid": txid}

    # Hash with volatile tokens stripped: a page that only ticks a price/counter
    # must not look "changed" and re-enter the queue every poll.
    content_hash = _stable_content_hash(page_text)
    snapshot_source_id = source_id_for_service(service_id)
    previous = get_latest_snapshot(snapshot_source_id)
    previous_body = previous[2] if previous else ""
    is_first = previous is None

    if previous and previous[0] == content_hash:
        _record_event()
        return {"status": "unchanged", "txid": txid}

    # Classifier verdicts for this page, computed once here and carried on the
    # queue payload so the drain and compose step never recompute (or disagree).
    # Relevance feeds novelty/priority ranking — it is NOT an enqueue gate.
    # Whether a domain is worth monitoring is decided upstream (Classifier A at
    # discovery); by the time content reaches here the source is already
    # approved, so the only enqueue veto is an explicit admin reject.
    signals = compute_content_signals(page_text, source_url)

    if url_recently_rejected(source_url):
        return _skip("recently_rejected")

    # Novelty for selection ranking: how different this headline is from what
    # we've published recently (1.0 = nothing close). Together with relevance it
    # is the ONLY thing that sets queue priority.
    from app.modules.newspaper.article_grader import (
        recent_content_similarity,
        recent_title_similarity,
    )

    title_sim, _title_match = recent_title_similarity(page_title)
    # Also compare by CONTENT (Typesense full-text retrieval over recent article
    # bodies) so a reworded headline about the same story still reads as low-novelty.
    content_sim, _content_match = recent_content_similarity(page_title, page_text)
    closest_sim = max(title_sim, content_sim)
    novelty = max(0.0, 1.0 - closest_sim)

    diff = None
    if not is_first:
        diff = build_text_diff(previous=previous_body, current=page_text)

    upsert_service_profile(
        service_id=service_id,
        page_text=page_text,
        source_url=source_url,
    )
    stored_weight = get_stored_service_weight(service_id)

    intent = build_publish_intent(
        service_id=service_id,
        page_text=page_text,
        page_title=page_title,
        is_first_snapshot=is_first,
        diff=diff,
        source_kind=source_kind,
        source_url=source_url,
        mail_from=mail_from,
        stored_service_weight=stored_weight,
        chain_triggered=round_num > 0,
        relevance=signals.relevance,
        novelty=novelty,
    )
    enqueue_decision = evaluate_enqueue(intent.kind, diff=diff)
    if not enqueue_decision.allowed:
        return _skip(enqueue_decision.reason)

    insert_snapshot(
        source_id=snapshot_source_id,
        service_id=service_id,
        url=source_url,
        content_hash=content_hash,
        title=page_title,
        body=page_text,
    )

    mode_info = resolve_publish_mode(
        service_id=service_id,
        page_text=page_text,
        source_url=source_url,
        topic=intent.topic.value if intent.topic else "",
        requested_mode=publish_mode,
        requested_article_id=linked_article_id,
        match_kind=match_kind,
        match_value=match_value,
    )
    if mode_info["publish_mode"] == "edit" and mode_info.get("linked_article_id"):
        dedupe_key = f"edit:{mode_info['linked_article_id']}:{content_hash[:16]}"
    elif intent.event_id and intent.event_phase:
        dedupe_key = build_event_dedupe_key(
            service_id=service_id,
            event_id=intent.event_id,
            phase=EventPhase(intent.event_phase),
            content_hash=content_hash,
        )
    else:
        dedupe_key = build_dedupe_key(
            service_id=service_id,
            topic=intent.topic.value,
            content_hash=content_hash,
            tier=intent.tier.value,
        )

    queue_id, created = enqueue_publish(
        service_id=service_id,
        display_name=display_name,
        scrape_url=source_url,
        payload={
            "page_text": page_text,
            "page_title": page_title,
            "diff": diff,
            "is_first_snapshot": is_first,
            "txid": txid,
            "round_num": round_num,
            "source_kind": source_kind or "",
            "match_kind": match_kind,
            "match_value": match_value,
            "og_image": og_image,
            "published_at": published_at,
            "inner_links": inner_links or [],
            "mail_from": mail_from,
            "mistral_only": mistral_only,
            "transcript_text": transcript_text,
            "tier": intent.tier.value,
            "publish_kind": intent.kind.value,
            "topic": intent.topic.value if intent.topic else "",
            "event_id": intent.event_id,
            "event_phase": intent.event_phase,
            "publish_mode": mode_info["publish_mode"],
            "linked_article_id": mode_info.get("linked_article_id") or "",
            "match_keys": [list(pair) for pair in mode_info.get("match_keys", [])],
            "signals": signals.to_payload(),
        },
        priority=intent.priority,
        publish_kind=intent.kind,
        topic=intent.topic,
        dedupe_key=dedupe_key,
    )
    _record_event()
    if not created:
        return {"status": "duplicate", "queue_id": queue_id, "txid": txid}

    if intent.tier == PublishTier.BREAKING and config.BREAKING_INLINE_DRAIN:
        drain_breaking_publish_queue.delay()

    return {"status": "enqueued", "queue_id": queue_id, "txid": txid}
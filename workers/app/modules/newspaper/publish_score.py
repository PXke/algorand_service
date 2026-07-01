from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.modules.newspaper.publish_policy import PublishKind, PublishTopic, priority_for_topic
from app.modules.newspaper.service_profile import score_service_impressiveness
from app.modules.newspaper.source_trust import source_trust_bonus


@dataclass(frozen=True)
class PriorityBreakdown:
    total: int
    topic_base: int
    source_trust: int
    service_weight: int
    urgency_bonus: int
    noise_penalty: int
    relevance_bonus: int = 0
    novelty_bonus: int = 0
    timeliness_bonus: int = 0
    timeliness_score: float = 0.5


def compute_priority(
    *,
    topic: PublishTopic,
    publish_kind: PublishKind,
    page_text: str,
    diff: str | None,
    source_kind: str | None,
    source_url: str = "",
    page_title: str = "",
    published_at: str = "",
    mail_from: str = "",
    stored_service_weight: int = 0,
    relevance: float | None = None,
    novelty: float | None = None,
    timeliness: float | None = None,
    today: date | None = None,
) -> PriorityBreakdown:
    # Sub-scores kept for observability on the admin/debug view, but they no
    # longer decide selection — see `total` below.
    topic_base = priority_for_topic(topic)
    trust = source_trust_bonus(
        source_kind=source_kind,
        source_url=source_url,
        mail_from=mail_from,
    )
    impress, _ = score_service_impressiveness(page_text=page_text, source_url=source_url)
    service_weight = impress if stored_service_weight == 0 else stored_service_weight
    urgency = _urgency_bonus(page_text)
    noise = _noise_penalty(publish_kind=publish_kind, diff=diff, page_text=page_text)

    # Selection criteria: relevance, novelty, and source timeliness. Relevance
    # is the spine — it MULTIPLIES, so an off-topic page scores ~0 no matter how
    # "novel" it looks. Among relevant candidates, novelty and timeliness add up
    # to their respective weights so fresh angles outrank stale rehashes.
    from app.core.config import (
        NOVELTY_PRIORITY_WEIGHT,
        RECENCY_PRIORITY_WEIGHT,
        RELEVANCE_PRIORITY_WEIGHT,
    )
    from app.modules.ai.publish_classifier import relevance_score
    from app.modules.gatekeeper.fact_align import source_timeliness_score

    rel = relevance if relevance is not None else relevance_score(page_text, source_url)
    rel = max(0.0, min(1.0, rel))
    nov = 1.0 if novelty is None else max(0.0, min(1.0, novelty))
    timeliness_score = (
        timeliness
        if timeliness is not None
        else source_timeliness_score(
            published_at=published_at,
            page_title=page_title,
            page_text=page_text,
            today=today,
        )
    )
    timeliness_score = max(0.0, min(1.0, timeliness_score))
    relevance_pts = round(rel * RELEVANCE_PRIORITY_WEIGHT)
    novelty_pts = round(rel * nov * NOVELTY_PRIORITY_WEIGHT)
    timeliness_pts = round(rel * timeliness_score * RECENCY_PRIORITY_WEIGHT)
    # Ceiling tracks the actual weight sum (not a stale hardcoded 200) so a
    # candidate strong on all three axes doesn't saturate and lose its
    # timeliness edge to random same-priority tiebreaking downstream.
    max_total = RELEVANCE_PRIORITY_WEIGHT + NOVELTY_PRIORITY_WEIGHT + RECENCY_PRIORITY_WEIGHT
    total = max(0, min(max_total, relevance_pts + novelty_pts + timeliness_pts))

    return PriorityBreakdown(
        total=total,
        topic_base=topic_base,
        source_trust=trust,
        service_weight=service_weight,
        urgency_bonus=urgency,
        noise_penalty=noise,
        relevance_bonus=relevance_pts,
        novelty_bonus=novelty_pts,
        timeliness_bonus=timeliness_pts,
        timeliness_score=round(timeliness_score, 2),
    )


def _urgency_bonus(page_text: str) -> int:
    lower = page_text.lower()
    if re.search(r"\b(in|within)\s+[12]\s+days?\b", lower):
        return 10
    if re.search(r"\b(today|tonight|tomorrow)\b", lower) and "call" in lower:
        return 8
    return 0


def _noise_penalty(
    *,
    publish_kind: PublishKind,
    diff: str | None,
    page_text: str,
) -> int:
    penalty = 0
    if publish_kind == PublishKind.CONTENT_UPDATE and diff:
        added = sum(
            1
            for line in diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        if added < 5:
            penalty += 5
    if len(page_text.strip()) < 200:
        penalty += 10
    return penalty

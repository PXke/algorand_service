from __future__ import annotations

import re
from dataclasses import dataclass

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


def compute_priority(
    *,
    topic: PublishTopic,
    publish_kind: PublishKind,
    page_text: str,
    diff: str | None,
    source_kind: str | None,
    source_url: str = "",
    mail_from: str = "",
    stored_service_weight: int = 0,
    relevance: float | None = None,
    novelty: float | None = None,
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

    # Selection criteria: relevance and novelty ONLY. Relevance is the spine —
    # it MULTIPLIES, so an off-topic page scores ~0 no matter how "novel" it
    # looks (a random vendor page trivially differs from recent articles, but we
    # still must not write about it). Among relevant candidates, novelty adds up
    # to NOVELTY_PRIORITY_WEIGHT more, so fresh angles rank above rehashes.
    from app.core.config import NOVELTY_PRIORITY_WEIGHT, RELEVANCE_PRIORITY_WEIGHT
    from app.modules.ai.publish_classifier import relevance_score

    rel = relevance if relevance is not None else relevance_score(page_text, source_url)
    rel = max(0.0, min(1.0, rel))
    nov = 1.0 if novelty is None else max(0.0, min(1.0, novelty))
    relevance_pts = round(rel * RELEVANCE_PRIORITY_WEIGHT)
    novelty_pts = round(rel * nov * NOVELTY_PRIORITY_WEIGHT)
    total = max(0, min(200, relevance_pts + novelty_pts))

    return PriorityBreakdown(
        total=total,
        topic_base=topic_base,
        source_trust=trust,
        service_weight=service_weight,
        urgency_bonus=urgency,
        noise_penalty=noise,
        relevance_bonus=relevance_pts,
        novelty_bonus=novelty_pts,
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

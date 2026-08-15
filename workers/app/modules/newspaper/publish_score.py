"""Compute a publish-queue row's priority from relevance, novelty, and signal bonuses/penalties."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.modules.newspaper.publish_policy import PublishKind, PublishTopic, priority_for_topic
from app.modules.newspaper.service_profile import score_service_impressiveness
from app.modules.newspaper.source_trust import source_trust_bonus


@dataclass(frozen=True)
class PriorityBreakdown:
    """A publish-queue row's priority score and its components."""

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
    diff_bonus: int = 0
    announce_bonus: int = 0
    scale_bonus: int = 0
    classifier_adjust: int = 0
    seo_spam: bool = False
    novelty_factor: float = 1.0


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
    scale_signal: float | None = None,
    relevance: float | None = None,
    novelty: float | None = None,
    timeliness: float | None = None,
    is_event: bool = False,
    classifier_publish: bool | None = None,
    classifier_confidence: float = 0.0,
    today: date | None = None,
) -> PriorityBreakdown:
    """Compute a publish-queue row's priority breakdown from topic, trust, and signal inputs."""
    # topic_base/trust/service_weight/urgency are kept for observability on the
    # admin/debug view only — they do NOT decide selection (see `total` below).
    # `noise` is the one exception: it's subtracted from `total` further down.
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

    from app.core.config import (
        ANNOUNCE_PRIORITY_BONUS,
        CLASSIFIER_PRIORITY_WEIGHT,
        DIFF_PRIORITY_WEIGHT,
        DIFF_SIGNIFICANCE_NORM_LINES,
        DISCOVERY_PRIORITY_WEIGHT,
        SCALE_PRIORITY_WEIGHT,
    )
    from app.modules.newspaper.service_scale import UNRESOLVED_SCALE
    from app.modules.search.classifier.score import seo_spam_hits

    # Evergreen SEO farms ("price prediction 2024-2030") game freshness stamps,
    # so a spam-shaped page forfeits its timeliness credit and cannot earn the
    # announcement bonus. Its relevance is already penalized in score_page.
    spam = seo_spam_hits(f"{page_title}\n{page_text[:5000]}") > 0

    relevance_pts = round(rel * RELEVANCE_PRIORITY_WEIGHT)
    novelty_pts = round(rel * nov * NOVELTY_PRIORITY_WEIGHT)
    timeliness_pts = 0 if spam else round(rel * timeliness_score * RECENCY_PRIORITY_WEIGHT)

    # Real-world project scale (DeFiLlama TVL / GitHub stars) — a secondary
    # modifier, not on par with relevance/novelty. A candidate the resolver
    # never reached defaults to the same neutral floor resolve_service_scale
    # itself returns on failure (see service_scale.py) — unresolved must never
    # score below a genuinely small, resolved project.
    scale = UNRESOLVED_SCALE if scale_signal is None else max(0.0, min(1.0, scale_signal))
    scale_pts = round(rel * scale * SCALE_PRIORITY_WEIGHT)

    # "Something happened" beats "this page exists": a detected event, urgency
    # phrasing, or an announcement-shaped TITLE earns a relevance-gated bonus
    # (title only — body text mentions "launch" too freely).
    announce = is_event or urgency > 0 or _looks_like_announcement_title(page_title)
    announce_pts = round(rel * ANNOUNCE_PRIORITY_BONUS) if announce and not spam else 0

    if publish_kind == PublishKind.SERVICE_DISCOVERY:
        # One shot per service ever — precise ordering among discoveries is
        # low-stakes, so a flat relevance-scaled score (junk still lands at ~0)
        # keeps them below any substantive update. Scale still applies here
        # (not just content updates) so first-ever coverage of a major new
        # protocol outranks first-ever coverage of a trivial one within
        # discovery's own drain queue.
        diff_pts = 0
        total = round(rel * DISCOVERY_PRIORITY_WEIGHT) + announce_pts + scale_pts
        max_total = DISCOVERY_PRIORITY_WEIGHT + ANNOUNCE_PRIORITY_BONUS + SCALE_PRIORITY_WEIGHT
    else:
        # For updates the diff IS the event: credit scales with how much of the
        # service's page actually changed, saturating at the norm line count.
        diff_sig = 0.0
        if diff:
            added = sum(
                1
                for line in diff.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
            diff_sig = min(1.0, added / max(1, DIFF_SIGNIFICANCE_NORM_LINES))
        diff_pts = round(rel * diff_sig * DIFF_PRIORITY_WEIGHT)
        total = relevance_pts + novelty_pts + timeliness_pts + diff_pts + announce_pts + scale_pts
        max_total = (
            RELEVANCE_PRIORITY_WEIGHT
            + NOVELTY_PRIORITY_WEIGHT
            + RECENCY_PRIORITY_WEIGHT
            + DIFF_PRIORITY_WEIGHT
            + ANNOUNCE_PRIORITY_BONUS
            + SCALE_PRIORITY_WEIGHT
        )

    # Thin-content penalty is LIVE (unlike topic_base/trust/service_weight/
    # urgency below, which stay observability-only): a short diff or a barely-
    # there page must actually lose points, not just display a number that
    # looks like it did.
    total = max(0, total - noise)

    # Learned signal: a confident classifier verdict nudges the heuristic rank.
    # None (training mode / low confidence) leaves the total untouched, so this
    # activates gradually as the model earns confidence from review feedback.
    # A confident reject can crush the total toward zero (not just halve it) —
    # otherwise even a maximally-confident "don't publish" verdict lets a
    # high-scoring candidate survive with half its points and still win a drain.
    classifier_adjust = 0
    if classifier_publish is True:
        classifier_adjust = round(classifier_confidence * CLASSIFIER_PRIORITY_WEIGHT)
    elif classifier_publish is False:
        classifier_adjust = -round(total * max(0.0, min(1.0, classifier_confidence)))
    total = max(0, min(max_total + CLASSIFIER_PRIORITY_WEIGHT, total + classifier_adjust))

    # Repetition suppression: novelty scales the WHOLE score. The additive
    # novelty term rewards fresh angles among competitors, but on its own a
    # zero-novelty candidate only lost that one term — a high-relevance repeat
    # (e.g. a newsletter re-covering a wallet we just published about) still
    # outranked fresh stories. The floor keeps legitimate weekly service
    # updates alive (they always resemble the service's previous article);
    # unscored candidates (novelty=None → 1.0) are untouched.
    from app.core.config import NOVELTY_SUPPRESSION_FLOOR

    floor = max(0.0, min(1.0, NOVELTY_SUPPRESSION_FLOOR))
    novelty_factor = floor + (1.0 - floor) * nov
    total = round(total * novelty_factor)

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
        diff_bonus=diff_pts,
        announce_bonus=announce_pts,
        scale_bonus=scale_pts,
        classifier_adjust=classifier_adjust,
        seo_spam=spam,
        novelty_factor=round(novelty_factor, 2),
    )


# Title shapes that signal a concrete happening (launch, partnership, ship,
# migration) as opposed to a page that merely exists. Stem-matched, title-only.
_ANNOUNCE_TITLE_STEMS = (
    # "introducing/introduces", never the bare stem — "Introduction | Pera Docs"
    # is a docs landing page, not an announcement.
    "introducing",
    "introduces",
    "launch",
    "announc",
    "unveil",
    "partner",
    "integrat",
    "release",
    "ships",
    "goes live",
    "now live",
    "brings",
    "acquir",
    "rebrand",
    "migrat",
    "upgrad",
    "raises",
    "secures",
    "debut",
)


def _looks_like_announcement_title(title: str) -> bool:
    lower = (title or "").lower()
    return any(stem in lower for stem in _ANNOUNCE_TITLE_STEMS)


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
            1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
        )
        if added < 5:
            penalty += 5
    if len(page_text.strip()) < 200:
        penalty += 10
    return penalty

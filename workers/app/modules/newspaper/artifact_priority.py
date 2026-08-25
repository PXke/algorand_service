"""Priority scoring for editorial-room `artifacts` -- feeds the LIVE day-ahead to_compose selection (see to_compose_selection.py / queue_drain_tasks.select_to_compose_for_today_task).

Architected as `priority = sum(component_score(artifact, content) for
component_score in SCORE_COMPONENTS)` so a future signal (sentiment, on-chain
activity, page-rank-style authority) plugs in as one more function in the
tuple below without restructuring `sweep_artifact_priorities`, the daily beat
that recomputes every PENDING artifact's score.

This is a deliberately separate, simpler formula from
`publish_score.compute_priority` (which still exists, still scores
publish_queue's own dual-written rows for rollback-safety observability, but
no longer drives any live selection decision) -- same general shape (a
handful of additive components, one of them an age-decay curve) but its own
scale and its own three v1 components:

  1. word_count_score      -- substantial content scores higher, diminishing
                               returns past ARTIFACT_WORD_COUNT_CAP words.
  2. timeliness_score       -- exponential half-life decay from event_date
                               (falling back to created_at), asymptotically
                               approaching (never reaching) a floor > 0.
  3. ecosystem_listed_score -- flat bonus for a URL whose domain is in the
                               SAME ecosystem_listed directory registry the
                               crawler-discovery scorer already uses.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.modules.newspaper.artifact_store import Artifact, ArtifactContent


def word_count_score(content: str) -> float:
    """0..ARTIFACT_WORD_COUNT_MAX_SCORE, a sqrt curve of word count capped at ARTIFACT_WORD_COUNT_CAP words.

    sqrt (rather than linear) gives strictly-increasing-but-diminishing
    returns well before the cap -- a 600-word diff already earns ~71% of the
    max score, not 50%, so a "substantial but not enormous" artifact isn't
    unfairly close to a barely-there one. Past the cap the score is flat: a
    huge wall of text can't keep buying more priority purely on size.
    """
    from app.core.config import ARTIFACT_WORD_COUNT_CAP, ARTIFACT_WORD_COUNT_MAX_SCORE

    words = len((content or "").split())
    if words <= 0:
        return 0.0
    cap = max(1, ARTIFACT_WORD_COUNT_CAP)
    ratio = min(1.0, words / cap) ** 0.5
    return round(ratio * ARTIFACT_WORD_COUNT_MAX_SCORE, 4)


def timeliness_score(
    event_date: datetime | None,
    created_at: datetime,
    *,
    today: datetime | None = None,
) -> float:
    """ARTIFACT_TIMELINESS_FLOOR..ARTIFACT_TIMELINESS_MAX_SCORE, exponential half-life decay from the event anchor (event_date, falling back to created_at per the artifacts-table fallback rule).

    Shape mirrors gatekeeper/fact_align.py's age-based decay curves (a
    precedent already proven in this codebase for "recency scores higher,
    decaying smoothly as it ages") but deliberately never hits a hard floor
    of zero the way that module's linear stale_days cutoff does -- an
    explicit owner instruction: old-but-real content must stay theoretically
    reachable "except when we have nothing else to report". At age=0 this
    returns the max score; as age -> infinity it approaches (never reaches)
    the floor, halving every ARTIFACT_TIMELINESS_HALF_LIFE_DAYS.
    """
    from app.core.config import (
        ARTIFACT_TIMELINESS_FLOOR,
        ARTIFACT_TIMELINESS_HALF_LIFE_DAYS,
        ARTIFACT_TIMELINESS_MAX_SCORE,
    )

    anchor = event_date or created_at
    now = today or datetime.now(tz=UTC)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    age_days = max(0.0, (now - anchor).total_seconds() / 86400.0)
    half_life = max(0.001, ARTIFACT_TIMELINESS_HALF_LIFE_DAYS)
    span = max(0.0, ARTIFACT_TIMELINESS_MAX_SCORE - ARTIFACT_TIMELINESS_FLOOR)
    decayed = span * (0.5 ** (age_days / half_life))
    return round(ARTIFACT_TIMELINESS_FLOOR + decayed, 4)


def ecosystem_listed_score(url: str | None) -> float:
    """Flat ARTIFACT_ECOSYSTEM_LISTED_BOOST when the artifact's URL domain is directory-listed (the SAME `ecosystem_listed_domains()` registry the crawler-discovery scorer uses for the identical class of problem -- a chain-silent-but-important service keyword scoring would otherwise miss), else 0.0. An artifact with no URL (a brief, a mail message) never earns this bonus."""
    from app.core.config import ARTIFACT_ECOSYSTEM_LISTED_BOOST

    if not url:
        return 0.0
    try:
        from app.modules.crawler.domain_tracker import domain_from_url
        from app.modules.crawler.ecosystem_sync import ecosystem_listed_domains

        domain = domain_from_url(url)
        if domain and domain in ecosystem_listed_domains():
            return float(ARTIFACT_ECOSYSTEM_LISTED_BOOST)
    except Exception:
        return 0.0
    return 0.0


def _word_count_component(_artifact: Artifact, content: ArtifactContent | None) -> float:
    return word_count_score(content.content if content else "")


def _timeliness_component(artifact: Artifact, _content: ArtifactContent | None) -> float:
    return timeliness_score(artifact.event_date, artifact.created_at)


def _ecosystem_component(artifact: Artifact, _content: ArtifactContent | None) -> float:
    return ecosystem_listed_score(artifact.url)


# Pluggable score components -- the sweep sums these. Add a new signal
# (sentiment, on-chain activity, authority) by appending one more
# `(artifact, content) -> float` function here; nothing else needs to change.
SCORE_COMPONENTS: tuple[Callable[[Artifact, ArtifactContent | None], float], ...] = (
    _word_count_component,
    _timeliness_component,
    _ecosystem_component,
)


def compute_artifact_priority(artifact: Artifact, content: ArtifactContent | None) -> float:
    """Sum every SCORE_COMPONENTS entry for one artifact."""
    total = sum(component(artifact, content) for component in SCORE_COMPONENTS)
    # math.fsum-grade precision isn't needed at this scale; round for a stable,
    # human-legible stored value (matches the round()s inside each component).
    return round(total, 4)


def sweep_artifact_priorities() -> dict[str, int]:
    """Recompute and persist `priority` for every PENDING artifact -- the body of the daily beat task (see tasks/artifact_tasks.py). Pure function: safe to call directly from a test or a manual trigger."""
    from app.modules.newspaper.artifact_store import (
        get_artifact_content,
        list_pending_artifacts,
        update_artifact_priority,
    )

    pending = list_pending_artifacts()
    for artifact in pending:
        content = get_artifact_content(artifact.artifact_id)
        priority = compute_artifact_priority(artifact, content)
        update_artifact_priority(artifact.artifact_id, priority)
    return {"status": "ok", "swept": len(pending), "updated": len(pending)}

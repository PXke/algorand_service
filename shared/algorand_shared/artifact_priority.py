"""Priority scoring for editorial-room `artifacts` -- feeds the LIVE day-ahead to_compose selection (see algorand_shared.to_compose_selection / workers' queue_drain_tasks.select_to_compose_for_today_task).

Moved here from `workers/app/modules/newspaper/artifact_priority.py`
(2026-08-26) alongside `artifact_store.py` / `to_compose_selection.py`, so
backend's admin to-compose preview route can recompute the same per-artifact
priority breakdown it displays without a Celery round-trip. Workers' daily
sweep beat (`sweep_artifact_priorities`, still called from
`tasks/artifact_tasks.py`) imports it from here now too -- same functions,
one location.

Note on `ecosystem_listed_score` specifically: its directory-listed bonus
depends on workers-only crawler/classifier modules
(`app.modules.crawler.domain_tracker`, `app.modules.crawler.ecosystem_sync`,
`app.modules.search.classifier.score`), which don't exist in backend's
codebase. It already fails open to 0.0 on any import error (a pre-existing
defensive pattern, not new here), so a backend-computed preview shows this
component as 0 rather than raising -- an accepted, documented gap (the
preview route's own docstring already notes its recomputed total is a live
estimate that can drift from the stored value) rather than a full
centralization of the ecosystem-directory machinery, which is out of scope
for this move.

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
scale and its own four components:

  1. word_count_score      -- substantial content scores higher, diminishing
                               returns past ARTIFACT_WORD_COUNT_CAP words.
  2. timeliness_score       -- exponential half-life decay from event_date
                               (falling back to created_at), asymptotically
                               approaching (never reaching) a floor > 0.
  3. ecosystem_listed_score -- flat bonus for a URL whose domain is in the
                               SAME ecosystem_listed directory registry the
                               crawler-discovery scorer already uses (or the
                               hardcoded chain-silent KNOWN_DOMAINS anchor
                               list, which is exempt from the next clause).
                               2026-08-26: for the directory registry (not
                               KNOWN_DOMAINS) also requires the artifact's
                               OWN content to clear keyword_hits() > 0 --  a
                               domain's directory listing is history, not
                               proof today's scored content is still
                               on-topic (see ecosystem_listed_score's own
                               docstring, root-caused on ulam.io).
  4. skip_count_score       -- (2026-08-27) direct linear reward for how many
                               times this service's pending artifact has been
                               superseded-by-concatenation without ever being
                               composed, read straight from
                               metadata["segments"]. Added because
                               word_count_score -- the only channel through
                               which the concatenation mechanism was meant to
                               "compound" priority for a chronically-ignored
                               service (see artifact_store.insert_artifact's
                               docstring) -- saturates at ARTIFACT_WORD_COUNT_CAP
                               words well before ARTIFACT_CONCAT_MAX_OLD_CHARS
                               (concatenation's own, much larger, ceiling) is
                               reached, silently stalling that compounding for
                               exactly the services ignored longest. See
                               skip_count_score's own docstring for the numbers.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from algorand_shared.artifact_store import Artifact, ArtifactContent


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

    Known limitation (documented, not fixed -- 2026-08-26, ulam.io): the
    `created_at` fallback assumes it reflects a real discovery/update event,
    but a row restored from a Cassandra backup (e.g. after the article-table
    incident recovery) gets `created_at` re-stamped at RESTORE time, not at
    whatever real event originally produced the content -- so a purely
    static, undateable page can score "fresh as yesterday" just because its
    artifact row happened to be recreated recently. There is currently no
    tracking anywhere in this codebase of an artifact's original vs.
    restored `created_at` (no `original_created_at`/`restored_at`-style
    field, checked before writing this note), and backup/restore is a rare,
    manual, whole-keyspace operation rather than a routine code path -- so
    this isn't fixed here rather than invent new schema/tracking for what is
    so far a one-off incident. If restore-induced timestamp resets turn out
    to recur, the fix would be to have the restore process itself preserve
    (or explicitly null out) the true original `created_at`/`event_date`
    instead of teaching this function to guess.
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


def ecosystem_listed_score(url: str | None, content: str = "") -> float:
    """Flat ARTIFACT_ECOSYSTEM_LISTED_BOOST when the artifact's URL domain is directory-listed, else 0.0. An artifact with no URL (a brief, a mail message) never earns this bonus.

    Two different registries back this, and they are trusted differently:

    - `KNOWN_DOMAINS` (score_page's hardcoded anchor list) is a small,
      human-curated set maintained SPECIFICALLY because each entry is
      chain-silent on its own pages (sealed.channel, hesab.af, lofty.ai,
      zerosignal.ai, dark-coin.com/.io, sowandreap.in) -- see the
      2026-08-26 fix note below. Membership here is itself the verified
      relevance signal, so it grants the full bonus unconditionally,
      regardless of what today's fetched `content` says (or doesn't say).
    - `ecosystem_listed_domains()` (the curated directory registry, synced
      from sources like awesome-algorand and algorand.co/case-studies) is
      much broader and unreviewed per-entry: a domain lands there because it
      was *once* linked from a directory or case study, not because a human
      verified it's still an Algorand service today. Companies pivot, and
      the crawler-coverage gap that motivated this fix (ulam.io, 2026-08-26)
      showed the aggregate that actually gets scored can drift to a
      generic, off-topic page even when the domain itself once had a
      genuinely relevant page. So for THIS registry only, the bonus also
      requires the artifact's own content to show at least one on-topic
      keyword hit (`keyword_hits`, word-boundary matched RELEVANCE_KEYWORDS)
      -- some minimal evidence that what's being scored today, not just the
      domain's history, is still about Algorand. Zero hits => 0.0, not a
      reduced bonus: a page that says nothing on-topic is exactly the
      failure mode being fixed, and a partial bonus would still let a fully
      drifted page outrank genuinely on-topic ones.

    This asymmetry is the point: it must NOT become a bare "requires the
    word algorand" gate, which would break exactly what `KNOWN_DOMAINS`
    exists to protect (content that legitimately never mentions the chain).
    The content check only ever applies to the weaker, unreviewed registry.

    Checking only `ecosystem_listed_domains()` (2026-08-26 fix) missed every
    chain-silent service that's hardcoded in `KNOWN_DOMAINS` specifically
    BECAUSE it's chain-silent (sealed.channel, hesab.af, lofty.ai,
    zerosignal.ai, dark-coin.com/.io, sowandreap.in) -- exactly the class
    this bonus's own docstring claims to protect, verified empirically
    against real production artifacts: sealed.channel/hesab.af were getting
    zero bonus while a generic multi-chain aggregator already present in
    `ecosystem_listed_domains()` outranked genuinely Algorand-native
    services. `domain_from_url` already collapses to the registrable eTLD+1
    domain, so a plain membership check against both sets is sufficient --
    no subdomain-suffix matching needed here the way score.py's own
    `_domain_signal` needs it against raw hostnames.

    Workers-only bonus (see module docstring): the crawler/classifier modules
    this depends on don't exist in backend, so this fails open to 0.0 there.
    See `ecosystem_scoring_available` just below for a way to tell THAT case
    apart from a genuine, computed 0.0 -- this function's own return type
    stays a plain float on purpose (its real caller, `sweep_artifact_priorities`,
    just needs a number to sum, always, and must keep failing open the same
    way it always has).
    """
    from app.core.config import ARTIFACT_ECOSYSTEM_LISTED_BOOST

    if not url:
        return 0.0
    try:
        from app.modules.crawler.domain_tracker import domain_from_url
        from app.modules.crawler.ecosystem_sync import ecosystem_listed_domains
        from app.modules.search.classifier.score import KNOWN_DOMAINS, keyword_hits

        domain = domain_from_url(url)
        if not domain:
            return 0.0
        if domain in KNOWN_DOMAINS:
            return float(ARTIFACT_ECOSYSTEM_LISTED_BOOST)
        if domain in ecosystem_listed_domains() and keyword_hits(content or "") > 0:
            return float(ARTIFACT_ECOSYSTEM_LISTED_BOOST)
    except Exception:
        return 0.0
    return 0.0


def ecosystem_scoring_available() -> bool:
    """Capability probe (2026-08-27): True when `ecosystem_listed_score`'s real, workers-only dependencies are importable in THIS process, False when it can only ever fail open to 0.0 here.

    Does the exact same import `ecosystem_listed_score` itself does -- and
    nothing else (no domain lookup, no registry read, no Cassandra) -- so it
    stays cheap enough to call once per preview/breakdown build. Always
    `True` in workers (where those crawler/classifier modules live), always
    `False` in backend (where they don't exist at all).

    This exists because `ecosystem_listed_score`'s own return type is a
    plain float that fails open the same way for BOTH "genuinely computed a
    real 0.0" and "couldn't even try" -- fine for its real caller
    (`sweep_artifact_priorities`, which just needs a number to sum and must
    keep failing open unconditionally), but not enough for a caller that
    wants to tell the two cases apart, like `to_compose_selection`'s
    admin-facing breakdown, which needs to say so rather than silently show
    a 0.0 that looks like a real measured absence of a bonus.
    """
    try:
        from app.modules.crawler.domain_tracker import domain_from_url  # noqa: F401
        from app.modules.crawler.ecosystem_sync import ecosystem_listed_domains  # noqa: F401
        from app.modules.search.classifier.score import (  # noqa: F401
            KNOWN_DOMAINS,
            keyword_hits,
        )
    except Exception:
        return False
    return True


def skip_count_score(metadata: dict[str, Any] | None) -> float:
    """0..ARTIFACT_SKIP_COUNT_MAX_SCORE, a LINEAR (not sqrt) reward for how many times this artifact has been superseded-by-concatenation, read directly from metadata["segments"].

    `_concatenate_with_pending` (artifact_store.py) appends one entry to
    `metadata["segments"]` every time a service_id that already has a
    pending artifact gets another ignored update concatenated onto it -- so
    `len(segments)` is a direct, unambiguous "times superseded" counter, not
    a proxy.

    Why this needs to exist alongside word_count_score rather than relying on
    it: word_count_score saturates at ARTIFACT_WORD_COUNT_CAP words (1200 by
    default), which a chronically-ignored service can reach in a handful of
    concatenation cycles -- well before ARTIFACT_CONCAT_MAX_OLD_CHARS (the
    concatenation mechanism's own ceiling, ~3x larger in word terms) ever
    kicks in. Past that point word_count_score is flat: a service ignored 4
    times and one ignored 14 times can score IDENTICALLY on word count alone,
    even though the second is a much stronger case for finally getting
    composed. This component reads the provenance trail directly so priority
    keeps rising for every additional ignored cycle, independent of whatever
    word_count_score happens to be doing. Deliberately linear (not sqrt) --
    word_count_score already supplies the diminishing-returns curve for raw
    text volume; this component's job is to keep differentiating repeat
    neglect specifically, so it should not itself flatten out early.
    """
    from app.core.config import ARTIFACT_SKIP_COUNT_CAP, ARTIFACT_SKIP_COUNT_MAX_SCORE

    segments = (metadata or {}).get("segments") or []
    count = len(segments)
    if count <= 0:
        return 0.0
    cap = max(1, ARTIFACT_SKIP_COUNT_CAP)
    ratio = min(1.0, count / cap)
    return round(ratio * ARTIFACT_SKIP_COUNT_MAX_SCORE, 4)


def _word_count_component(_artifact: Artifact, content: ArtifactContent | None) -> float:
    return word_count_score(content.content if content else "")


def _timeliness_component(artifact: Artifact, _content: ArtifactContent | None) -> float:
    return timeliness_score(artifact.event_date, artifact.created_at)


def _ecosystem_component(artifact: Artifact, content: ArtifactContent | None) -> float:
    return ecosystem_listed_score(artifact.url, content.content if content else "")


def _skip_count_component(_artifact: Artifact, content: ArtifactContent | None) -> float:
    return skip_count_score(content.metadata if content else None)


# Pluggable score components -- the sweep sums these. Add a new signal
# (sentiment, on-chain activity, authority) by appending one more
# `(artifact, content) -> float` function here; nothing else needs to change.
SCORE_COMPONENTS: tuple[Callable[[Artifact, ArtifactContent | None], float], ...] = (
    _word_count_component,
    _timeliness_component,
    _ecosystem_component,
    _skip_count_component,
)


def compute_artifact_priority(artifact: Artifact, content: ArtifactContent | None) -> float:
    """Sum every SCORE_COMPONENTS entry for one artifact."""
    total = sum(component(artifact, content) for component in SCORE_COMPONENTS)
    # math.fsum-grade precision isn't needed at this scale; round for a stable,
    # human-legible stored value (matches the round()s inside each component).
    return round(total, 4)


def sweep_artifact_priorities() -> dict[str, int]:
    """Recompute and persist `priority` for every PENDING artifact -- the body of the daily beat task (see workers' tasks/artifact_tasks.py). Pure function: safe to call directly from a test or a manual trigger."""
    from algorand_shared.artifact_store import (
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

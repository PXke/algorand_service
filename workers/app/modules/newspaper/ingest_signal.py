"""Hash a scrape's content to decide whether it's a real change worth publishing."""

from __future__ import annotations

import hashlib
import re

from app.core import config
from app.modules.newspaper.article_matching import resolve_publish_mode
from app.modules.newspaper.publish_policy import build_publish_intent, evaluate_enqueue
from app.modules.newspaper.service_profile_store import (
    get_stored_scale_signal,
    get_stored_service_weight,
    upsert_service_profile,
    upsert_service_scale,
)
from app.modules.newspaper.snapshot_store import (
    get_latest_snapshot,
    insert_snapshot,
    source_id_for_service,
)
from app.modules.pipeline.core.diffing import build_text_diff

# Volatile tokens that change on a page without the story changing: numbers,
# money amounts, percentages, clock times, "N minutes/hours ago" phrasing, and
# calendar dates (e.g. a "last updated" stamp ticking forward). Stripped before
# hashing so a live price/counter/date tick is not seen as a content change
# (which would otherwise re-enqueue the same page every poll). The full-date
# pattern must run before the generic digit-strip below, or its day/year digits
# are gone by the time it would match, leaving the month name behind as a
# false diff.
_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_VOLATILE_PATTERNS = (
    re.compile(r"\d+\s*(?:seconds?|minutes?|mins?|hours?|days?)\s+ago", re.IGNORECASE),
    re.compile(r"\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?", re.IGNORECASE),
    re.compile(
        rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s*(?:\d{{4}})?\b", re.IGNORECASE
    ),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"[$€£]\s?\d[\d,]*(?:\.\d+)?"),
    re.compile(r"\d[\d,]*(?:\.\d+)?\s*%"),
    re.compile(r"\d[\d,]*(?:\.\d+)?"),
)

# A live activity feed (marketplace sales, leaderboard, recent trades) churns
# its per-row IDENTITY (names, addresses, tickers) every poll even when the
# volatile numeric/date fields above are stripped -- found 2026-08-02
# (NFDomains): a "recent sales" table flattened to text as a 4-line-per-row
# cycle (name+price / seller / buyer / date) meant the page's stable hash
# differed on every single one of 12 consecutive weekly polls, so "unchanged"
# never fired and every poll looked like real news even though the volatile
# numeric/date fields WERE being stripped -- the row's changing NAME is what
# survived. Rather than hand-list every site's own feed vocabulary
# (unmaintainable, never generalizes to the next service with this shape),
# detect it structurally: normalize each line to its "shape" (the same
# volatile-token strip as the hash, plus any dotted/ticker-ish identifier
# collapsed to a placeholder), then find any run of a repeating N-line CYCLE
# (rows are rarely 1 line each once seller/buyer/date are split onto their
# own lines, as scrapers commonly flatten a table) that repeats
# _ROW_BLOCK_MIN_CYCLES+ times, and blank the whole run out. A hand-written
# passage essentially never repeats its own line-shape cycle that many times;
# a flattened table of near-identical rows always does.
_ROW_BLOCK_MIN_CYCLES = 3
_ROW_BLOCK_MAX_PERIOD = 6
_ROW_SHAPE_NAME = re.compile(r"\b[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)+\b")
# A scraped table's seller/buyer cell is often flattened onto its own line as
# ONE bare token -- a .algo name (caught above) or a truncated address like
# "X5KD3V…EXVU" (an ellipsis, not a dot). Collapse a whole line that's just a
# single non-whitespace identifier-shaped run, so both forms shape the same.
_ROW_SHAPE_BARE_TOKEN = re.compile(r"^[a-z0-9][a-z0-9_.…-]{1,79}$")
# Second detection pass (see _strip_repeating_row_blocks): a dense screener
# (hay.app: TICKER / PERCENT, with some rows also carrying a rank number) has
# no CONSTANT period, so the cycle pass above misses its tail. Independently
# catch a long run of individually short-shaped lines instead. 4 chars covers
# "" (a bare number/percent, fully consumed by _VOLATILE_PATTERNS) and "@" /
# "@ a" (an identifier with at most one short leftover token, e.g. a
# currency-ish prefix letter). A hand-written sentence's shape is essentially
# never that short.
_ROW_SHORT_SHAPE_MAX_LEN = 4
# Higher bar than the cycle pass's MIN_CYCLES*period floor -- this heuristic
# is blunter (no shape-matching, just "short"), so require more consecutive
# evidence before blanking a run.
_SHORT_RUN_MIN_LINES = 8


def _line_shape(line: str) -> str | None:
    """Structural signature of one line -- volatile tokens stripped, identifier-shaped tokens collapsed -- or None for a genuinely blank input line."""
    stripped = line.strip().lower()
    if not stripped:
        return None
    for pat in _VOLATILE_PATTERNS:
        stripped = pat.sub("", stripped)
    stripped = _ROW_SHAPE_NAME.sub("@", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    if _ROW_SHAPE_BARE_TOKEN.match(stripped):
        stripped = "@"
    return stripped


def _blank_repeating_cycles(shapes: list[str | None], out: list[str]) -> None:
    """Pass 1: blank any run where an N-line shape cycle (N up to _ROW_BLOCK_MAX_PERIOD) repeats _ROW_BLOCK_MIN_CYCLES+ times -- a multi-field row flattened across several lines (NFDomains: name+price / seller / buyer / date, every cycle the same length). Mutates ``out`` in place."""
    n = len(shapes)
    i = 0
    while i < n:
        if shapes[i] is None:
            i += 1
            continue
        best_end = i
        for period in range(1, _ROW_BLOCK_MAX_PERIOD + 1):
            if i + period * _ROW_BLOCK_MIN_CYCLES > n:
                continue
            block = shapes[i : i + period]
            if any(s is None for s in block):
                continue
            cycles = 1
            pos = i + period
            while pos + period <= n and shapes[pos : pos + period] == block:
                cycles += 1
                pos += period
            if cycles >= _ROW_BLOCK_MIN_CYCLES:
                end = i + period * cycles
                best_end = max(best_end, end)
        if best_end > i:
            for k in range(i, best_end):
                out[k] = ""
            i = best_end
        else:
            i += 1


def _blank_short_shape_runs(shapes: list[str | None], out: list[str]) -> None:
    """Pass 2: blank any run of _SHORT_RUN_MIN_LINES+ individually short-shaped lines, regardless of exact period -- a dense single-value screener (hay.app: TICKER / PERCENT, but some rows also carry a rank number, so the period isn't constant and pass 1 alone misses the tail). "Short-shaped" means the line reduced to nothing (a bare number/percent, fully consumed by _VOLATILE_PATTERNS) or to the bare "@" identifier placeholder -- a hand-written sentence essentially never does that many times in a row. Mutates ``out`` in place."""
    n = len(shapes)
    i = 0
    while i < n:
        shape = shapes[i]
        if shape is None or len(shape) > _ROW_SHORT_SHAPE_MAX_LEN:
            i += 1
            continue
        j = i
        while (
            j + 1 < n
            and shapes[j + 1] is not None
            and len(shapes[j + 1]) <= _ROW_SHORT_SHAPE_MAX_LEN
        ):
            j += 1
        if (j - i + 1) >= _SHORT_RUN_MIN_LINES:
            for k in range(i, j + 1):
                out[k] = ""
        i = j + 1


def _strip_repeating_row_blocks(text: str) -> str:
    """Blank out live-activity-table noise before hashing/diffing, via two independent passes over line SHAPE (never vocabulary) -- see _blank_repeating_cycles and _blank_short_shape_runs."""
    lines = text.split("\n")
    shapes = [_line_shape(line) for line in lines]
    out = list(lines)
    _blank_repeating_cycles(shapes, out)
    _blank_short_shape_runs(shapes, out)
    return "\n".join(out)


def _stable_content_hash(text: str) -> str:
    """Hash of the page with volatile numeric/time tokens AND any repeating live-activity-list rows removed, so pages that only update live data (prices, counters, timestamps, a marketplace/leaderboard feed's row identities) hash the same across polls and are correctly treated as ``unchanged``."""
    normalized = _strip_repeating_row_blocks(text)
    for pat in _VOLATILE_PATTERNS:
        normalized = pat.sub("", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _resolve_stale_scale_signal(
    *, service_id: str, display_name: str, source_url: str, outbound_links: list[str]
) -> float | None:
    """Return this service's scale signal, refreshing it first if stale/never-scored.

    Piggybacked on this call site (the only existing per-service write path)
    rather than a separate scheduled job — this is already doing comparable-
    cost inline work here (novelty/service-profile lookups). None means "no
    score available" (never resolved yet, or a resolver error this run) and
    must NOT be conflated with a resolved-and-tiny 0.0 — compute_priority
    treats None as the neutral unresolved floor, not as "small". A resolver
    failure/timeout here must never break ingest.
    """
    from datetime import UTC, datetime, timedelta

    score, updated_at = get_stored_scale_signal(service_id)
    stale = updated_at is None or (
        datetime.now(tz=UTC) - updated_at > timedelta(days=config.SERVICE_SCALE_REFRESH_DAYS)
    )
    if not stale:
        return score

    try:
        from app.modules.newspaper.service_scale import resolve_service_scale

        fresh_score, source = resolve_service_scale(
            display_name=display_name,
            source_url=source_url,
            outbound_links=outbound_links,
        )
        upsert_service_scale(service_id=service_id, scale_score=fresh_score, scale_source=source)
        return fresh_score
    except Exception:
        return score


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
    is_first_override: bool | None = None,
) -> dict[str, str]:
    """Shared enqueue path for crawl, mail, and other lanes after content is fetched.

    Updates snapshot, service profile, and publish queue.

    ``is_first_override``: for lanes whose ``service_id`` is a synthetic
    per-item key (e.g. Bluesky's ``{account}:{post_rkey}``, one per post),
    ``previous is None`` is ALWAYS true — every post is "first" under its own
    key — which would misclassify every post as SERVICE_DISCOVERY. Such
    callers already know the underlying account is an established, already-
    monitored service (never a genuine discovery) and pass ``False`` here to
    say so.
    """
    from app.modules.ai.content_signals import compute_content_signals
    from app.modules.crawler.domain_tracker import url_recently_rejected
    from app.modules.newspaper.article_store import record_service_event

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
    is_first = (previous is None) if is_first_override is None else is_first_override

    if previous and previous[0] == content_hash:
        _record_event()
        return {"status": "unchanged", "txid": txid}

    # Classifier verdicts for this page, computed once here and carried on the
    # queue payload so the drain and compose step never recompute (or disagree).
    # Relevance feeds novelty/priority ranking — it is NOT an enqueue gate.
    # Whether a domain is worth monitoring is decided upstream (Classifier A at
    # discovery); by the time content reaches here the source is already
    # approved, so the only enqueue veto is an explicit admin reject.
    #
    # outbound_links reuses inner_links (already scraped for the writer's
    # source context) to feed the SAME explorer-link signal discovery already
    # cleared — without it a multi-chain service's priority sinks to the
    # bottom of the queue for content that already proved relevant once
    # (zerosignal.ai/dark-coin.com sitting at priority 0, 2026-07-22).
    outbound_links = tuple(
        link["url"] for link in (inner_links or []) if isinstance(link, dict) and link.get("url")
    )
    signals = compute_content_signals(page_text, source_url, outbound_links=outbound_links)

    if url_recently_rejected(source_url):
        return _skip("recently_rejected")

    # Novelty for selection ranking: how different this headline is from what
    # we've published recently (1.0 = nothing close). Together with relevance
    # and source timeliness this sets queue priority.
    from app.modules.newspaper.article_grader import (
        recent_content_similarity,
        recent_title_similarity,
    )

    title_sim, _title_match = recent_title_similarity(page_title)
    # Also compare by CONTENT (Typesense full-text retrieval over recent article
    # bodies) so a reworded headline about the same story still reads as low-novelty.
    content_sim, _content_match = recent_content_similarity(page_title, page_text)
    # title_sim and content_sim are both token-Jaccard (see
    # article_grader.recent_content_similarity), so they're already on the
    # same scale and combine directly via max().
    closest_sim = max(title_sim, content_sim)
    novelty = max(0.0, 1.0 - closest_sim)

    diff = None
    if not is_first:
        # Strip the same repeating live-activity-list rows the stable hash
        # ignores -- otherwise a page whose only REAL change is prose
        # elsewhere still hands the writer a diff dominated by marketplace/
        # leaderboard row noise (new names, same shape) as "what changed".
        diff = build_text_diff(
            previous=_strip_repeating_row_blocks(previous_body),
            current=_strip_repeating_row_blocks(page_text),
        )

    upsert_service_profile(
        service_id=service_id,
        page_text=page_text,
        source_url=source_url,
    )
    stored_weight = get_stored_service_weight(service_id)
    scale_signal = _resolve_stale_scale_signal(
        service_id=service_id,
        display_name=display_name,
        source_url=source_url,
        outbound_links=list(outbound_links),
    )

    intent = build_publish_intent(
        service_id=service_id,
        page_text=page_text,
        page_title=page_title,
        published_at=published_at,
        is_first_snapshot=is_first,
        diff=diff,
        source_kind=source_kind,
        source_url=source_url,
        mail_from=mail_from,
        stored_service_weight=stored_weight,
        stored_scale_signal=scale_signal,
        chain_triggered=round_num > 0,
        relevance=signals.relevance,
        novelty=novelty,
        classifier_publish=signals.publish_decision,
        classifier_confidence=signals.confidence,
    )
    enqueue_decision = evaluate_enqueue(
        intent.kind, diff=diff, source_kind=source_kind, relevance=signals.relevance
    )
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
        requested_mode=publish_mode,
        requested_article_id=linked_article_id,
    )

    # This becomes artifact_content.metadata["payload"] -- the editorial-room
    # artifact system's compose input, read back by
    # queue_drain_tasks._artifact_to_queued_row when this artifact composes.
    queue_payload = {
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
        "signals": signals.to_payload(),
        "priority_breakdown": intent.priority_breakdown,
    }

    _record_event()

    # The editorial-room artifact system (2026-08-25 cutover): the live
    # compose trigger reads exclusively from `artifacts`/`to_compose` (see
    # queue_drain_tasks.drain_to_compose), so this is THE write for this
    # signal -- publish_queue's dual-write (a one-deploy-cycle rollback
    # safety net) was dropped once the artifact path proved stable in prod.
    # Left unguarded (no try/except): this is the only write left, so a
    # failure here must propagate to the caller the same way an enqueue_publish
    # failure always did pre-cutover -- callers (e.g. mail_poll_tasks) rely on
    # the raise to leave the source item unseen/unacked for retry.
    _insert_artifact_for_signal(
        service_id=service_id,
        source_url=source_url,
        page_title=page_title,
        page_text=page_text,
        source_kind=source_kind,
        display_name=display_name,
        match_kind=match_kind,
        match_value=match_value,
        published_at=published_at,
        queue_payload=queue_payload,
    )

    return {"status": "enqueued", "txid": txid}


# Maps ingest_publish_signal's `source_kind` to the artifacts table's
# `channel` column. `channel` is a plain text column (no CQL enum), so this
# is free to diverge from 077_artifacts.cql's illustrative
# "crawler / youtube / mail / twitter / brief / special_edition" list where a
# more accurate name exists for a lane that migration comment predates:
# "bluesky" and "forum" get their own channel rather than being folded into
# the aspirational "twitter" value (no X/Twitter poll lane exists yet -- see
# search_x, which is a research TOOL, not an ingest lane). Everything else
# (plain web crawl, single-page compose, xgov's programmatic chain-derived
# proposal text, and any source_kind not listed) falls into "crawler" --
# each is "a raw candidate discovered/fetched by an automated lane", the
# same bucket the migration's docstring describes for that channel.
# "editorial_assignment" is NOT handled here -- that lane bypasses
# ingest_publish_signal entirely (see editorial_assignment.py's own
# insert_artifact calls, channel "brief"/"special_edition").
_CHANNEL_BY_SOURCE_KIND = {
    "youtube": "youtube",
    "mail": "mail",
    "bluesky": "bluesky",
    "forum": "forum",
}


def _channel_for_source_kind(source_kind: str | None) -> str:
    """`artifacts.channel` for a given `ingest_publish_signal` `source_kind` -- see `_CHANNEL_BY_SOURCE_KIND`."""
    return _CHANNEL_BY_SOURCE_KIND.get((source_kind or "").strip().lower(), "crawler")


def _insert_artifact_for_signal(
    *,
    service_id: str,
    source_url: str,
    page_title: str,
    page_text: str,
    source_kind: str | None,
    display_name: str,
    match_kind: str,
    match_value: str,
    published_at: str,
    queue_payload: dict,
) -> None:
    """Create (or replace-for-this-service) the editorial-room artifact for one publish signal."""
    from datetime import UTC, datetime

    from app.modules.gatekeeper.fact_align import event_anchor_date
    from app.modules.newspaper.artifact_store import insert_artifact

    event_date = None
    anchor = event_anchor_date(published_at=published_at, page_title=page_title, page_text=page_text)
    if anchor is not None:
        event_date = datetime(anchor.year, anchor.month, anchor.day, tzinfo=UTC)

    insert_artifact(
        service_id=service_id or None,
        url=source_url or None,
        channel=_channel_for_source_kind(source_kind),
        content=page_text,
        title=page_title,
        metadata={
            "display_name": display_name,
            "source_kind": source_kind or "",
            "match_kind": match_kind,
            "match_value": match_value,
            "payload": queue_payload,
        },
        event_date=event_date,
    )

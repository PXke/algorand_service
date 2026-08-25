"""Compose an article draft (scrape-triggered or weekly digest) via the writer."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core import config
from app.core.config import mistral_configured
from app.modules.ai.llm_compose import (
    compose_assignment_article,
    compose_recap_from_transcript,
    compose_weekly_digest_article,
)
from app.modules.ai.llm_compose import (
    compose_scrape_article as _llm_compose_scrape_article,
)
from app.modules.ai.llm_provider import LLMError
from app.modules.ai.llm_purpose_router import PeakHoursBlockedError
from app.modules.newspaper.peak_hours import is_off_peak_now, next_off_peak_at
from app.modules.newspaper.publish_policy import PublishKind, PublishTopic, trim_text_to_chars
from app.modules.newspaper.weekly_digest import WeeklyDigestContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArticleComposeResult:
    """The composer's output: fields, composer used, and heuristic grade."""

    title: str
    summary: str
    body: str
    composer: str
    publish_kind: str = ""
    extra_tags: tuple[str, ...] = ()
    prompt_version: str = ""
    heuristic_grade: dict | None = None
    confirmed_alert: str | None = None
    # Hard-divert signals from llm_compose's post-hoc gates (chain_entity_gate,
    # unsourced_specifics_gate) — MUST be forwarded from LLMArticleFields on
    # every branch below. Silently dropping these makes publish_tasks.py's
    # getattr(composed, ..., default) fall back to the all-clear default, which
    # is exactly what happened 2026-07-17..07-20: both hard-diverts (MyAlgo
    # defunct-entity fix, GoPlausible unsourced-specifics fix) were dead code in
    # production because this dataclass never carried the fields through.
    defunct_domains: tuple[str, ...] = ()
    unsourced_hold_reason: str = ""
    broken_link_hold_reason: str = ""


def _require_mistral() -> None:
    """No template fallback exists (owner decision 2026-07-14: a lesser, robotic article is worse than no article) — Mistral or nothing. Callers must handle the resulting LLMError as "no article this cycle"; see publish_from_queued_row/recompose_review/recompose_published for the established skip-cleanly pattern (all three already catch LLMError and return a {"status": ...} dict before any DB write happens)."""
    if not mistral_configured():
        raise LLMError("MISTRAL_ENABLED and MISTRAL_API_KEY required — no template fallback")


def _require_off_peak() -> None:
    """DeepSeek peak/off-peak billing (owner decision 2026-08-15): confine ALL compose, no exceptions (including breaking news), to off-peak hours -- checked here, the single shared funnel every one of the 9 real compose-triggering task paths (beat-scheduled AND on-demand) already goes through, for the exact reason AUTO_COMPOSE_PAUSED is checked at every entry point instead of each task's own schedule (see that flag's own docstring: a gate checked only at celery-beat entries missed an on-demand trigger path entirely, 2026-08-09).

    Raises PeakHoursBlockedError (an LLMError subclass) so every existing
    `except LLMError` call site catches this with zero changes there --
    but each of those sites MUST check isinstance(exc, PeakHoursBlockedError)
    before logging/reporting, since this is an intentional, routine skip, not
    a real failure.
    """
    if not is_off_peak_now():
        next_at = next_off_peak_at()
        raise PeakHoursBlockedError(
            f"peak hours (DeepSeek billing) — next off-peak start at "
            f"{next_at.isoformat() if next_at else 'unknown'}"
        )


def compose_scrape_article(
    *,
    service_name: str,
    source_url: str,
    page_title: str,
    page_text: str,
    txid: str,
    round_num: int,
    diff: str | None,
    is_first_snapshot: bool,
    publish_kind: PublishKind,
    publish_topic: PublishTopic | None = None,
    # Vestigial: every compose is Mistral-only now (no template fallback to
    # opt out of). Kept because it's threaded through ingest_signal.py's
    # public API and Celery task payloads (publish_tasks.py) — not worth the
    # blast radius of unwinding it from queued-task payloads for a parameter
    # that no longer changes behavior either way.
    mistral_only: bool = False,
    enrichment_block: str = "",
    transcript_text: str = "",
    source_links: list[dict[str, str]] | None = None,
    keywords: str = "",
    brief_id: str = "",
    first_coverage: bool = False,
    prior_coverage_block: str = "",
    is_special_edition: bool = False,
) -> ArticleComposeResult:
    """Compose by publish kind (discovery vs update) via the writer LLM."""
    del mistral_only  # see docstring above
    topic = publish_topic or PublishTopic.GENERIC
    _require_mistral()
    _require_off_peak()

    # COMMUNITY_RECAP gets the full transcript via compose_recap_from_transcript
    # below; every other topic previously dropped transcript_text entirely. Fold it into
    # page_text so the existing writer prompt (which already treats page_text as raw
    # source material) picks it up without a dispatch-logic rewrite.
    if transcript_text and topic != PublishTopic.COMMUNITY_RECAP:
        page_text = (
            f"{page_text}\n\nVideo transcript:\n"
            f"{trim_text_to_chars(transcript_text, config.YOUTUBE_TRANSCRIPT_MAX_CHARS)}"
        )

    if topic == PublishTopic.EDITORIAL_ASSIGNMENT:
        fields = compose_assignment_article(
            brief_title=page_title,
            brief_body=page_text,
            keywords=keywords,
            brief_id=brief_id or source_url,
            is_special_edition=is_special_edition,
        )
        extra_tags = tuple(getattr(fields, "tags", ()))
        if is_special_edition and "special-edition" not in extra_tags:
            extra_tags = (*extra_tags, "special-edition")
        return ArticleComposeResult(
            title=fields.title,
            summary=fields.summary,
            body=fields.body,
            composer="mistral_assignment",
            publish_kind=publish_kind.value,
            extra_tags=extra_tags,
            prompt_version=getattr(fields, "prompt_version", ""),
            heuristic_grade=getattr(fields, "heuristic_grade", None),
            defunct_domains=getattr(fields, "defunct_domains", ()),
            unsourced_hold_reason=getattr(fields, "unsourced_hold_reason", ""),
            broken_link_hold_reason=getattr(fields, "broken_link_hold_reason", ""),
        )

    if topic == PublishTopic.COMMUNITY_RECAP and transcript_text:
        fields = compose_recap_from_transcript(
            service_name=service_name,
            source_url=source_url,
            page_title=page_title,
            transcript_text=transcript_text,
        )
        return ArticleComposeResult(
            title=fields.title,
            summary=fields.summary,
            body=fields.body,
            composer="mistral_transcript",
            publish_kind=publish_kind.value,
            extra_tags=getattr(fields, "tags", ()),
            prompt_version=getattr(fields, "prompt_version", ""),
            heuristic_grade=getattr(fields, "heuristic_grade", None),
        )

    fields = _llm_compose_scrape_article(
        service_name=service_name,
        source_url=source_url,
        page_title=page_title,
        page_text=page_text,
        txid=txid,
        round_num=round_num,
        diff=diff,
        is_first_snapshot=is_first_snapshot,
        enrichment_block=enrichment_block,
        source_links=source_links,
        publish_topic=topic.value,
        first_coverage=first_coverage,
        prior_coverage_block=prior_coverage_block,
    )
    return ArticleComposeResult(
        title=fields.title,
        summary=fields.summary,
        body=fields.body,
        composer="mistral",
        publish_kind=publish_kind.value,
        extra_tags=getattr(fields, "tags", ()),
        prompt_version=getattr(fields, "prompt_version", ""),
        heuristic_grade=getattr(fields, "heuristic_grade", None),
        confirmed_alert=getattr(fields, "confirmed_alert", None),
        defunct_domains=getattr(fields, "defunct_domains", ()),
        unsourced_hold_reason=getattr(fields, "unsourced_hold_reason", ""),
    )


def compose_weekly_digest(
    context: WeeklyDigestContext,
    *,
    mistral_only: bool = False,  # vestigial, see compose_scrape_article
) -> ArticleComposeResult:
    """Weekly digest: CoinGecko snapshot + recent feed articles, via the digest-tier LLM."""
    del mistral_only  # see compose_scrape_article
    _require_mistral()
    _require_off_peak()
    fields = compose_weekly_digest_article(context)
    body = trim_text_to_chars(fields.body, config.WEEKLY_DIGEST_MAX_BODY_CHARS)
    return ArticleComposeResult(
        title=fields.title,
        summary=fields.summary,
        body=body,
        composer="mistral",
        publish_kind=PublishKind.WEEKLY_DIGEST.value,
        prompt_version=getattr(fields, "prompt_version", ""),
    )

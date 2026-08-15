"""compose(): run one full article compose entirely outside Celery.

Extracted for multi-provider benchmarking (2026-08-14, ahead of a DeepSeek
pricing change) -- a script can call this directly with a frozen article
snapshot, a provider name, and a local SessionRegister, and get back a
result with per-run token usage, with zero queue/publish/Celery coupling.

This does NOT duplicate the compose pipeline: it's a thin wrapper around the
same compose_scrape_article_mistral(...) every Celery task already calls,
using the research_client/session_register override points added there
specifically for this purpose. Production behavior (publish_tasks.py's
call sites, which never pass these) is completely unaffected.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from uuid import UUID

from app.modules.ai.llm_registry import get_provider
from app.modules.ai.mistral_compose import MistralArticleFields, compose_scrape_article_mistral
from app.modules.ai.session_register import SessionRegister


@dataclass(frozen=True)
class ArticleInput:
    """Frozen compose_scrape_article_mistral inputs -- the exact kwargs a real compose used, snapshotted once so every benchmark run composes from IDENTICAL source material (see scripts/snapshot_compose_input.py)."""

    service_name: str
    source_url: str
    page_title: str
    page_text: str
    txid: str
    round_num: int
    diff: str | None
    is_first_snapshot: bool
    enrichment_block: str = ""
    source_links: list[dict[str, str]] | None = None
    publish_topic: str = ""
    first_coverage: bool = False
    prior_coverage_block: str = ""


@dataclass(frozen=True)
class ComposeRunResult:
    """One compose() call's output: the article fields plus everything a benchmark comparison needs."""

    fields: MistralArticleFields
    usage: dict[str, int] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    duration_ms: int = 0
    session_id: UUID | None = None


def compose(
    *,
    article_input: ArticleInput,
    provider_name: str,
    session_register: SessionRegister,
    timeout: float | None = None,
) -> ComposeRunResult:
    """Run one full research -> write -> grade/revise compose against `article_input`, using `provider_name` for BOTH the research and writer tiers (a benchmark compares one provider end to end, not a purpose-routed mix), checkpointing through `session_register` instead of prod Cassandra.

    No Celery, no publish queue, no article store write -- safe to call
    directly from a script or a REPL. Raises whatever compose_scrape_article_mistral
    itself raises (LLMError/LLMCreditError/StorySpikedError) rather than
    falling back to a template -- a benchmark run failing loudly on a bad
    provider/model is more useful than a silently degraded result.
    """
    writer = get_provider(provider_name, timeout=timeout)
    research = get_provider(provider_name, timeout=timeout)

    t0 = time.monotonic()
    fields = compose_scrape_article_mistral(
        service_name=article_input.service_name,
        source_url=article_input.source_url,
        page_title=article_input.page_title,
        page_text=article_input.page_text,
        txid=article_input.txid,
        round_num=article_input.round_num,
        diff=article_input.diff,
        is_first_snapshot=article_input.is_first_snapshot,
        enrichment_block=article_input.enrichment_block,
        source_links=article_input.source_links,
        publish_topic=article_input.publish_topic,
        first_coverage=article_input.first_coverage,
        prior_coverage_block=article_input.prior_coverage_block,
        client=writer,
        research_client=research,
        session_register=session_register,
    )
    duration_ms = int((time.monotonic() - t0) * 1000)
    usage = {
        key: research.usage_totals()[key] + writer.usage_totals()[key]
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    return ComposeRunResult(
        fields=fields,
        usage=usage,
        provider=provider_name,
        model=writer.model,
        duration_ms=duration_ms,
        session_id=None,
    )

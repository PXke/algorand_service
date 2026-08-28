"""Purpose-based LLM routing: writer/research/digest/translate/rubric -> Mistral or DeepSeek.

Moved out of the now-deleted mistral_client.py backward-compat shim
(2026-08-25, the mistral_* -> llm_* rename follow-up) -- this is the routing
logic that decides which provider actually serves a given purpose (per that
purpose's LLM_PROVIDER_<PURPOSE> config + canary), not a Mistral-specific
concern. Deliberately kept in its own module rather than folded into
llm_registry.py: that module's own docstring calls this purpose-based routing
"orthogonal to" its direct name -> provider selection (get_provider("kimi") et
al) -- different concerns, kept separate.

PeakHoursBlockedError also lives here (not a real API failure -- see its own
docstring) since it's raised by the same purpose-routed call sites this module
serves.
"""

from __future__ import annotations

from app.core.config import (
    DEEPSEEK_API_BASE,
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL_DIGEST,
    DEEPSEEK_MODEL_RESEARCH,
    DEEPSEEK_MODEL_RUBRIC,
    DEEPSEEK_MODEL_TRANSLATE,
    DEEPSEEK_MODEL_WRITER,
    LLM_PROVIDER_DIGEST,
    LLM_PROVIDER_DIGEST_CANARY_PCT,
    LLM_PROVIDER_RESEARCH,
    LLM_PROVIDER_RESEARCH_CANARY_PCT,
    LLM_PROVIDER_RUBRIC,
    LLM_PROVIDER_RUBRIC_CANARY_PCT,
    LLM_PROVIDER_TRANSLATE,
    LLM_PROVIDER_TRANSLATE_CANARY_PCT,
    LLM_PROVIDER_WRITER,
    LLM_PROVIDER_WRITER_CANARY_PCT,
    MISTRAL_MODEL_DIGEST,
    MISTRAL_MODEL_RESEARCH,
    MISTRAL_MODEL_TRANSLATE,
    MISTRAL_MODEL_WRITER,
)
from app.modules.ai.llm_openai_compatible import (
    DeepSeekProvider,
    MistralProvider,
    OpenAICompatibleProvider,
)
from app.modules.ai.llm_provider import LLMError


class PeakHoursBlockedError(LLMError):
    """Raised by article_composer's peak-hours guard (2026-08-15) -- NOT a real API failure.

    Every one of its call sites must check `isinstance(exc, PeakHoursBlockedError)`
    BEFORE falling through to the generic failure branch: unlike a real
    failure, this is an intentional, expected, routine skip (we chose not to
    call the API), so it must never log at ERROR level or report a status
    that reads as something being broken.
    """


_PROVIDER_CONFIG: dict[str, tuple[str, int, str]] = {
    # purpose -> (configured default provider, canary %, DeepSeek model for this purpose)
    "writer": (LLM_PROVIDER_WRITER, LLM_PROVIDER_WRITER_CANARY_PCT, DEEPSEEK_MODEL_WRITER),
    "research": (
        LLM_PROVIDER_RESEARCH,
        LLM_PROVIDER_RESEARCH_CANARY_PCT,
        DEEPSEEK_MODEL_RESEARCH,
    ),
    "digest": (LLM_PROVIDER_DIGEST, LLM_PROVIDER_DIGEST_CANARY_PCT, DEEPSEEK_MODEL_DIGEST),
    "translate": (
        LLM_PROVIDER_TRANSLATE,
        LLM_PROVIDER_TRANSLATE_CANARY_PCT,
        DEEPSEEK_MODEL_TRANSLATE,
    ),
    "rubric": (LLM_PROVIDER_RUBRIC, LLM_PROVIDER_RUBRIC_CANARY_PCT, DEEPSEEK_MODEL_RUBRIC),
}


def _select_provider(purpose: str) -> str:
    """Which provider actually serves this call: `purpose`'s configured default, or its canary alternate on a random roll (LLM_PROVIDER_<PURPOSE>_CANARY_PCT). Raises LLMError if that resolves to deepseek but DEEPSEEK_API_KEY is unset, rather than silently falling back to mistral -- Mistral is retired (see CLAUDE.md), so a silent fallback there used to mask a missing/rotated DeepSeek key as "composed fine, just on the wrong, retired provider" instead of failing loud the moment it happened."""
    import random

    default_provider, canary_pct, _ = _PROVIDER_CONFIG[purpose]
    provider = default_provider
    if canary_pct > 0 and random.random() * 100 < canary_pct:
        provider = "deepseek" if provider == "mistral" else "mistral"
    if provider == "deepseek" and not DEEPSEEK_API_KEY.strip():
        raise LLMError(
            f"LLM_PROVIDER_{purpose.upper()} resolved to deepseek but DEEPSEEK_API_KEY is not set"
        )
    return provider


def _client_for_purpose(
    purpose: str, *, mistral_model: str, timeout: float | None = None
) -> OpenAICompatibleProvider:
    """A client for `purpose` ("writer"/"research"/"digest"/"translate"), routed to Mistral or DeepSeek per that purpose's LLM_PROVIDER_<PURPOSE> config (+ canary). `mistral_model` is used when Mistral serves the call — same model as today's behavior when DeepSeek isn't configured, so this is a no-op change until DEEPSEEK_API_KEY is actually set."""
    if _select_provider(purpose) == "deepseek":
        _, _, deepseek_model = _PROVIDER_CONFIG[purpose]
        return DeepSeekProvider(
            api_key=DEEPSEEK_API_KEY,
            api_base=DEEPSEEK_API_BASE,
            model=deepseek_model,
            timeout=timeout,
        )
    return MistralProvider(model=mistral_model, timeout=timeout)


def get_llm_writer_client(*, model: str | None = None) -> OpenAICompatibleProvider:
    """Build a client for the writer tier (or an override model), routed to Mistral or DeepSeek per LLM_PROVIDER_WRITER."""
    return _client_for_purpose("writer", mistral_model=model or MISTRAL_MODEL_WRITER)


def get_llm_research_client(*, timeout: float | None = None) -> OpenAICompatibleProvider:
    """Build a client pinned to the research-tier model, routed to Mistral or DeepSeek per LLM_PROVIDER_RESEARCH, optionally with a non-default per-request timeout (special editions use a longer one -- see LLM_TIMEOUT_SPECIAL_EDITION_MULTIPLIER)."""
    return _client_for_purpose("research", mistral_model=MISTRAL_MODEL_RESEARCH, timeout=timeout)


def get_llm_digest_client() -> OpenAICompatibleProvider:
    """Build a client pinned to the digest-tier model, routed to Mistral or DeepSeek per LLM_PROVIDER_DIGEST."""
    return _client_for_purpose("digest", mistral_model=MISTRAL_MODEL_DIGEST)


def get_llm_translate_client() -> OpenAICompatibleProvider:
    """Build a client pinned to the translate-tier model, routed to Mistral or DeepSeek per LLM_PROVIDER_TRANSLATE. A dedicated factory (rather than get_llm_writer_client(model=MISTRAL_MODEL_TRANSLATE), the old call pattern) so translate calls can be routed independently of generic writer calls."""
    return _client_for_purpose("translate", mistral_model=MISTRAL_MODEL_TRANSLATE)


def get_llm_rubric_client(*, timeout: float | None = None) -> OpenAICompatibleProvider:
    """Build a client for the LLM quality rubric, routed to Mistral or DeepSeek per LLM_PROVIDER_RUBRIC — independently of LLM_PROVIDER_RESEARCH, even though it shares research's Mistral-side model tier (a judgment task, not generation, doesn't need the writer's Large tier). Split into its own purpose 2026-08-06 so a compose can route its research tool loop to one provider while grading with another."""
    return _client_for_purpose("rubric", mistral_model=MISTRAL_MODEL_RESEARCH, timeout=timeout)


__all__ = [
    "PeakHoursBlockedError",
    "get_llm_digest_client",
    "get_llm_research_client",
    "get_llm_rubric_client",
    "get_llm_translate_client",
    "get_llm_writer_client",
]

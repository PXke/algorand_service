"""Backward-compat shim for the mistral_* -> llm_* rename (2026-08-15).

The actual OpenAI-compatible chat-completions implementation now lives in
llm_openai_compatible.py as `OpenAICompatibleProvider`, with `MistralProvider`
as its thin Mistral-flavored subclass. This module re-exports everything
under its historical names so none of the 30+ existing importers (production
modules and tests) needed to change in the same pass as the physical move --
`MistralClient` is `MistralProvider` (not the generic base class: it keeps
Mistral's own config-sourced defaults, matching every existing bare
`MistralClient(...)` call site's expectations), and `MistralError`/
`MistralCreditError` are literal aliases of the canonical `LLMError`/
`LLMCreditError` (llm_provider.py) that every other provider (Anthropic,
Gemini) already raises directly.

Callers that construct a client for a specific purpose (writer/research/
digest/translate/rubric), routed to Mistral or DeepSeek per that purpose's
LLM_PROVIDER_<PURPOSE> config (+ canary), still belong here -- this
purpose-based routing is orthogonal to llm_registry.py's direct
name -> provider selection (see that module's own docstring), not
superseded by it.
"""

from __future__ import annotations

import httpx  # noqa: F401 -- re-exported so `mistral_client.httpx.Client = FakeClient`-style test patches (mutating the shared httpx module singleton) still reach llm_openai_compatible.py's own `httpx.Client(...)` calls without needing to update every existing test file.

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
    LLMRateLimitError,
    MistralProvider,
    OpenAICompatibleProvider,
    _ensure_tool_call_ids,
    _fetch_model_metadata,
    _model_metadata_cache,
    _parse_json_object,
    _retry_after_seconds,
)
from app.modules.ai.llm_provider import LLMCreditError, LLMError

# LLMError/LLMCreditError (llm_provider.py) are the canonical exception names
# for the whole multi-provider abstraction -- llm_anthropic_provider.py and
# llm_gemini_provider.py already raise them directly. MistralError/
# MistralCreditError are kept as literal aliases (not separate classes) so
# every existing `except MistralError`/`isinstance(exc, MistralCreditError)`
# call site (30+) keeps working unchanged.
MistralError = LLMError
MistralCreditError = LLMCreditError
MistralRateLimitError = LLMRateLimitError

# MistralClient is MistralProvider, not the generic OpenAICompatibleProvider
# base -- every existing bare `MistralClient(...)` call site (interrogate.py,
# mistral_compose.py, ~15 test files) relies on falling back to Mistral's own
# config (MISTRAL_API_KEY/MISTRAL_API_BASE/MISTRAL_MODEL) when an argument is
# omitted, which is exactly MistralProvider's job.
MistralClient = MistralProvider


class PeakHoursBlockedError(MistralError):
    """Raised by article_composer's peak-hours guard (2026-08-15) -- NOT a real API failure.

    Every one of its call sites must check `isinstance(exc, PeakHoursBlockedError)`
    BEFORE falling through to the generic "mistral_failed" branch: unlike a
    real failure, this is an intentional, expected, routine skip (we chose
    not to call the API), so it must never log at ERROR level or report a
    status that reads as something being broken.
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
    """Which provider actually serves this call: `purpose`'s configured default, or its canary alternate on a random roll (LLM_PROVIDER_<PURPOSE>_CANARY_PCT). Falls back to mistral if that resolves to deepseek but DEEPSEEK_API_KEY is unset — a canary or override can never hard-fail a compose just because the second provider isn't configured yet."""
    import random

    default_provider, canary_pct, _ = _PROVIDER_CONFIG[purpose]
    provider = default_provider
    if canary_pct > 0 and random.random() * 100 < canary_pct:
        provider = "deepseek" if provider == "mistral" else "mistral"
    if provider == "deepseek" and not DEEPSEEK_API_KEY.strip():
        return "mistral"
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


def get_mistral_client(*, model: str | None = None) -> OpenAICompatibleProvider:
    """Build a client for the writer tier (or an override model), routed to Mistral or DeepSeek per LLM_PROVIDER_WRITER."""
    return _client_for_purpose("writer", mistral_model=model or MISTRAL_MODEL_WRITER)


def get_mistral_research_client(*, timeout: float | None = None) -> OpenAICompatibleProvider:
    """Build a client pinned to the research-tier model, routed to Mistral or DeepSeek per LLM_PROVIDER_RESEARCH, optionally with a non-default per-request timeout (special editions use a longer one -- see MISTRAL_TIMEOUT_SPECIAL_EDITION_MULTIPLIER)."""
    return _client_for_purpose("research", mistral_model=MISTRAL_MODEL_RESEARCH, timeout=timeout)


def get_mistral_digest_client() -> OpenAICompatibleProvider:
    """Build a client pinned to the digest-tier model, routed to Mistral or DeepSeek per LLM_PROVIDER_DIGEST."""
    return _client_for_purpose("digest", mistral_model=MISTRAL_MODEL_DIGEST)


def get_mistral_translate_client() -> OpenAICompatibleProvider:
    """Build a client pinned to the translate-tier model, routed to Mistral or DeepSeek per LLM_PROVIDER_TRANSLATE. A dedicated factory (rather than get_mistral_client(model=MISTRAL_MODEL_TRANSLATE), the old call pattern) so translate calls can be routed independently of generic writer calls."""
    return _client_for_purpose("translate", mistral_model=MISTRAL_MODEL_TRANSLATE)


def get_mistral_rubric_client(*, timeout: float | None = None) -> OpenAICompatibleProvider:
    """Build a client for the LLM quality rubric, routed to Mistral or DeepSeek per LLM_PROVIDER_RUBRIC — independently of LLM_PROVIDER_RESEARCH, even though it shares research's Mistral-side model tier (a judgment task, not generation, doesn't need the writer's Large tier). Split into its own purpose 2026-08-06 so a compose can route its research tool loop to one provider while grading with another."""
    return _client_for_purpose("rubric", mistral_model=MISTRAL_MODEL_RESEARCH, timeout=timeout)


__all__ = [
    "MistralClient",
    "MistralCreditError",
    "MistralError",
    "MistralRateLimitError",
    "OpenAICompatibleProvider",
    "PeakHoursBlockedError",
    "_ensure_tool_call_ids",
    "_fetch_model_metadata",
    "_model_metadata_cache",
    "_parse_json_object",
    "_retry_after_seconds",
    "get_mistral_client",
    "get_mistral_digest_client",
    "get_mistral_research_client",
    "get_mistral_rubric_client",
    "get_mistral_translate_client",
]

"""Thin per-provider subclasses over the shared OpenAI-compatible chat-completions implementation.

Mistral, DeepSeek, OpenAI, Kimi (Moonshot), and GLM (Zhipu) all speak the
same `/chat/completions` JSON wire format -- the differences between them are
data (base URL, API key, model name) plus two small overridable quirk hooks
(`_reasoning_payload_extra`, `_effective_max_tokens`), not control flow. So
rather than reimplementing retry/backoff, the credit-exhaustion breaker,
context-window trimming, and the agentic tool-calling round loop once per
provider, `OpenAICompatibleProvider` is that one shared implementation
(today's MistralClient -- not yet physically moved here, see its own
docstring) and each provider below is a ~20-line subclass supplying its own
config-sourced defaults. Each subclass still independently implements
LLMProvider (satisfies "each model gets its own class"), just via
inheritance instead of duplication.

Gemini is the one provider that genuinely doesn't fit here -- its native API
uses a different wire format entirely (contents/parts, functionCall) -- see
llm_gemini_provider.py instead.
"""

from __future__ import annotations

from app.core.config import (
    DEEPSEEK_API_BASE,
    DEEPSEEK_API_KEY,
    DEEPSEEK_MAX_TOKENS,
    DEEPSEEK_MODEL_WRITER,
    GLM_API_BASE,
    GLM_API_KEY,
    GLM_MODEL_WRITER,
    KIMI_API_BASE,
    KIMI_API_KEY,
    KIMI_MAX_TOKENS,
    KIMI_MODEL_WRITER,
    MISTRAL_API_BASE,
    MISTRAL_API_KEY,
    MISTRAL_MAX_TOKENS,
    MISTRAL_MODEL,
    OPENAI_API_BASE,
    OPENAI_API_KEY,
    OPENAI_MODEL_WRITER,
)
from app.modules.ai.mistral_client import MistralClient

# The shared OpenAI-compatible implementation. Not yet physically relocated
# out of mistral_client.py (deferred to the mistral_* -> llm_* rename) --
# this name is what every provider below (and any future one) should
# actually subclass, rather than referring to `MistralClient` directly.
OpenAICompatibleProvider = MistralClient


class MistralProvider(OpenAICompatibleProvider):
    """Mistral's own hosted API -- the platform's original/default provider."""

    def __init__(
        self,
        *,
        model: str | None = None,
        timeout: float | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        """Wire credentials/model/timeout, defaulting to Mistral's own config."""
        super().__init__(
            api_key=api_key if api_key is not None else MISTRAL_API_KEY,
            api_base=api_base if api_base is not None else MISTRAL_API_BASE,
            model=model if model is not None else MISTRAL_MODEL,
            timeout=timeout,
            provider="mistral",
        )

    def _supports_prompt_cache_key(self) -> bool:
        """Mistral documents prompt_cache_key as the opt-in for its 90% cached-input discount -- unlike DeepSeek/OpenAI/Kimi's fully automatic caching, Mistral needs this explicit hint to pin a growing conversation's shared prefix to one cache entry."""
        return True


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek -- the one existing quirk provider: thinking-mode payload extras and a higher max_tokens floor (reasoning_content shares the same budget as content)."""

    def __init__(
        self,
        *,
        model: str | None = None,
        timeout: float | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        """Wire credentials/model/timeout, defaulting to DeepSeek's own config."""
        super().__init__(
            api_key=api_key if api_key is not None else DEEPSEEK_API_KEY,
            api_base=api_base if api_base is not None else DEEPSEEK_API_BASE,
            model=model if model is not None else DEEPSEEK_MODEL_WRITER,
            timeout=timeout,
            provider="deepseek",
        )

    def _reasoning_payload_extra(self) -> dict[str, object]:
        return {"thinking": {"type": "enabled"}, "stream": False}

    def _effective_max_tokens(self, requested: int | None) -> int:
        base = requested if requested is not None else MISTRAL_MAX_TOKENS
        return max(base, DEEPSEEK_MAX_TOKENS)


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI's hosted API."""

    def __init__(
        self,
        *,
        model: str | None = None,
        timeout: float | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        """Wire credentials/model/timeout, defaulting to OpenAI's own config."""
        super().__init__(
            api_key=api_key if api_key is not None else OPENAI_API_KEY,
            api_base=api_base if api_base is not None else OPENAI_API_BASE,
            model=model if model is not None else OPENAI_MODEL_WRITER,
            timeout=timeout,
            provider="openai",
        )

    def _max_tokens_field_name(self) -> str:
        """Confirmed live 2026-08-14: GPT-5.6 rejects "max_tokens" outright (400 unsupported_parameter) and wants "max_completion_tokens" instead."""
        return "max_completion_tokens"

    def _supports_temperature(self) -> bool:
        """Confirmed live 2026-08-14: GPT-5.6 rejects any explicit temperature value other than its own default (1) -- omit the field entirely rather than guess which value it'll accept."""
        return False

    def _tool_reasoning_effort_override(self) -> str | None:
        """Confirmed live 2026-08-14: GPT-5.6 400s on any tool-calling request ("Function tools with reasoning_effort are not supported... set reasoning_effort to 'none'") even when we send no reasoning_effort field at all -- omitting it isn't equivalent to disabling it. Force "none" explicitly on every tool round; non-tool calls (chat_completion) are unaffected."""
        return "none"


class KimiProvider(OpenAICompatibleProvider):
    """Moonshot AI's Kimi."""

    def __init__(
        self,
        *,
        model: str | None = None,
        timeout: float | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        """Wire credentials/model/timeout, defaulting to Kimi's own config."""
        super().__init__(
            api_key=api_key if api_key is not None else KIMI_API_KEY,
            api_base=api_base if api_base is not None else KIMI_API_BASE,
            model=model if model is not None else KIMI_MODEL_WRITER,
            timeout=timeout,
            provider="kimi",
        )

    def _supports_temperature(self) -> bool:
        """Confirmed live 2026-08-14: Kimi K3 rejects any explicit temperature value ("invalid temperature: only 1 is allowed for this model", 400) -- same shape as GPT-5.6's constraint, different provider."""
        return False

    def _effective_max_tokens(self, requested: int | None) -> int:
        """Floor at KIMI_MAX_TOKENS -- confirmed live 2026-08-14 that a small budget (e.g. 10) comes back with EMPTY content because K3's mandatory reasoning consumes it first, same failure shape as the 2026-08-06 DeepSeek incident this exact pattern was built to prevent."""
        base = requested if requested is not None else MISTRAL_MAX_TOKENS
        return max(base, KIMI_MAX_TOKENS)


class GLMProvider(OpenAICompatibleProvider):
    """Zhipu's GLM."""

    def __init__(
        self,
        *,
        model: str | None = None,
        timeout: float | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        """Wire credentials/model/timeout, defaulting to GLM's own config."""
        super().__init__(
            api_key=api_key if api_key is not None else GLM_API_KEY,
            api_base=api_base if api_base is not None else GLM_API_BASE,
            model=model if model is not None else GLM_MODEL_WRITER,
            timeout=timeout,
            provider="glm",
        )

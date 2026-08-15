"""String -> LLMProvider class registry.

The "loading into the agent is based on a string and it uses the class
related" requirement: get_provider("kimi") returns a fresh KimiProvider
instance, get_provider("gemini") a fresh GeminiProvider, etc. This is the
direct-selection path a benchmark caller (compose_runner.py) uses -- it's
orthogonal to, and doesn't replace, mistral_client.py's existing
LLM_PROVIDER_<PURPOSE> + canary-pct purpose-based routing (_client_for_purpose),
which stays exactly as-is for production's writer/research/digest/translate/
rubric tiers.
"""

from __future__ import annotations

from app.modules.ai.llm_anthropic_provider import AnthropicProvider
from app.modules.ai.llm_gemini_provider import GeminiProvider
from app.modules.ai.llm_openai_compatible import (
    DeepSeekProvider,
    GLMProvider,
    KimiProvider,
    MistralProvider,
    OpenAIProvider,
)
from app.modules.ai.llm_provider import LLMProvider

_PROVIDER_CLASSES: dict[str, type[LLMProvider]] = {
    "mistral": MistralProvider,
    "deepseek": DeepSeekProvider,
    "openai": OpenAIProvider,
    "kimi": KimiProvider,
    "glm": GLMProvider,
    "gemini": GeminiProvider,
    "anthropic": AnthropicProvider,
}


def get_provider(
    name: str,
    *,
    model: str | None = None,
    timeout: float | None = None,
) -> LLMProvider:
    """Build a fresh provider instance for `name` (case/whitespace-insensitive). Raises ValueError on an unrecognized name -- deliberately, not a silent fallback, since a benchmark caller asking for "kimi" and silently getting Mistral would invalidate the whole comparison."""
    cls = _PROVIDER_CLASSES.get(name.strip().lower())
    if cls is None:
        known = ", ".join(sorted(_PROVIDER_CLASSES))
        raise ValueError(f"unknown LLM provider {name!r}; known providers: {known}")
    return cls(model=model, timeout=timeout)


def known_providers() -> list[str]:
    """Every registered provider name, sorted -- for a benchmark script to loop over or validate against."""
    return sorted(_PROVIDER_CLASSES)

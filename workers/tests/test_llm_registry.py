"""get_provider(name): the string -> class registry every benchmark caller loads a provider through."""

from __future__ import annotations

import pytest

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
from app.modules.ai.llm_registry import get_provider, known_providers


def test_known_providers_lists_every_registered_name() -> None:
    """known_providers() lists every registered name, sorted."""
    assert known_providers() == [
        "anthropic",
        "deepseek",
        "gemini",
        "glm",
        "kimi",
        "mistral",
        "openai",
    ]


@pytest.mark.parametrize(
    ("name", "expected_class"),
    [
        ("mistral", MistralProvider),
        ("deepseek", DeepSeekProvider),
        ("openai", OpenAIProvider),
        ("kimi", KimiProvider),
        ("glm", GLMProvider),
        ("gemini", GeminiProvider),
        ("anthropic", AnthropicProvider),
    ],
)
def test_get_provider_returns_the_right_class(name: str, expected_class: type) -> None:
    """get_provider(name) returns an instance of the matching concrete provider class."""
    provider = get_provider(name)
    assert isinstance(provider, expected_class)
    assert isinstance(provider, LLMProvider)


def test_get_provider_is_case_and_whitespace_insensitive() -> None:
    """get_provider trims and lowercases the name before lookup."""
    assert isinstance(get_provider("  Kimi  "), KimiProvider)
    assert isinstance(get_provider("GEMINI"), GeminiProvider)


def test_get_provider_raises_on_unknown_name() -> None:
    """Deliberately never falls back silently -- a benchmark caller asking for a typo'd provider name must fail loudly, not quietly run the wrong model."""
    with pytest.raises(ValueError, match="unknown LLM provider"):
        get_provider("chatgpt-turbo-9000")


def test_get_provider_forwards_model_and_timeout_overrides() -> None:
    """get_provider forwards model/timeout overrides through to the constructed provider."""
    provider = get_provider("kimi", model="kimi-k3-preview", timeout=42.0)
    assert provider.model == "kimi-k3-preview"

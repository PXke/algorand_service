"""W1-D: per-purpose provider routing defaults to DeepSeek, and a missing DEEPSEEK_API_KEY now fails loud instead of silently downgrading to the retired Mistral provider (CLAUDE.md: "Mistral is retired")."""

from __future__ import annotations

import pytest

from app.modules.ai import llm_purpose_router as router
from app.modules.ai.llm_provider import LLMError


def test_config_defaults_route_every_purpose_to_deepseek() -> None:
    """Every LLM_PROVIDER_* purpose knob defaults to "deepseek" now that DeepSeek is the live provider (Mistral is retired)."""
    from app.core.config import (
        LLM_PROVIDER_DIGEST,
        LLM_PROVIDER_RESEARCH,
        LLM_PROVIDER_RUBRIC,
        LLM_PROVIDER_TRANSLATE,
        LLM_PROVIDER_WRITER,
    )

    assert LLM_PROVIDER_WRITER == "deepseek"
    assert LLM_PROVIDER_RESEARCH == "deepseek"
    assert LLM_PROVIDER_DIGEST == "deepseek"
    assert LLM_PROVIDER_TRANSLATE == "deepseek"
    assert LLM_PROVIDER_RUBRIC == "deepseek"


def test_select_provider_raises_when_deepseek_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A purpose resolved to deepseek with no DEEPSEEK_API_KEY set raises LLMError -- it must never silently run the call on Mistral instead."""
    monkeypatch.setattr(router, "DEEPSEEK_API_KEY", "")
    monkeypatch.setitem(router._PROVIDER_CONFIG, "writer", ("deepseek", 0, "deepseek-model"))

    with pytest.raises(LLMError, match="DEEPSEEK_API_KEY"):
        router._select_provider("writer")


def test_select_provider_returns_deepseek_when_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """A purpose resolved to deepseek with a real key set returns "deepseek", not a silent Mistral downgrade."""
    monkeypatch.setattr(router, "DEEPSEEK_API_KEY", "sk-real-key")
    monkeypatch.setitem(router._PROVIDER_CONFIG, "writer", ("deepseek", 0, "deepseek-model"))

    assert router._select_provider("writer") == "deepseek"


def test_select_provider_mistral_default_unaffected_by_missing_deepseek_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A purpose explicitly configured for mistral is untouched by the DeepSeek-key check -- only a resolved-to-deepseek purpose can raise."""
    monkeypatch.setattr(router, "DEEPSEEK_API_KEY", "")
    monkeypatch.setitem(router._PROVIDER_CONFIG, "writer", ("mistral", 0, "deepseek-model"))

    assert router._select_provider("writer") == "mistral"


def test_select_provider_canary_to_deepseek_raises_on_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 100% canary roll onto deepseek is subject to the exact same missing-key check as the configured default -- no quieter path via the canary."""
    monkeypatch.setattr(router, "DEEPSEEK_API_KEY", "")
    monkeypatch.setitem(router._PROVIDER_CONFIG, "writer", ("mistral", 100, "deepseek-model"))
    monkeypatch.setattr("random.random", lambda: 0.0)

    with pytest.raises(LLMError, match="DEEPSEEK_API_KEY"):
        router._select_provider("writer")


def test_select_provider_raises_when_glm_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A purpose resolved to glm with no GLM_API_KEY set raises LLMError -- same fail-loud contract as deepseek, never a silent Mistral downgrade."""
    monkeypatch.setattr(router, "GLM_API_KEY", "")
    monkeypatch.setitem(router._PROVIDER_CONFIG, "writer", ("glm", 0, "deepseek-model"))

    with pytest.raises(LLMError, match="GLM_API_KEY"):
        router._select_provider("writer")


def test_select_provider_returns_glm_when_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """A purpose resolved to glm with a real key set returns "glm"."""
    monkeypatch.setattr(router, "GLM_API_KEY", "glm-real-key")
    monkeypatch.setitem(router._PROVIDER_CONFIG, "writer", ("glm", 0, "deepseek-model"))

    assert router._select_provider("writer") == "glm"


def test_select_provider_canary_never_flips_a_glm_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mistral<->deepseek canary flip is a no-op when the configured default is glm -- there is no "canary against glm" concept, so a glm purpose stays glm regardless of the random roll."""
    monkeypatch.setattr(router, "GLM_API_KEY", "glm-real-key")
    monkeypatch.setitem(router._PROVIDER_CONFIG, "writer", ("glm", 100, "deepseek-model"))
    monkeypatch.setattr("random.random", lambda: 0.0)

    assert router._select_provider("writer") == "glm"


def test_client_for_purpose_builds_glm_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """A purpose resolved to glm builds a GLMProvider, not a silent Mistral fallback."""
    from app.modules.ai.llm_openai_compatible import GLMProvider

    monkeypatch.setattr(router, "GLM_API_KEY", "glm-real-key")
    monkeypatch.setitem(router._PROVIDER_CONFIG, "writer", ("glm", 0, "deepseek-model"))

    client = router._client_for_purpose("writer", mistral_model="mistral-model")
    assert isinstance(client, GLMProvider)

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

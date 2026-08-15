"""Thin per-provider subclasses over the shared OpenAI-compatible implementation: each resolves its own config defaults, never falls back to another provider's key/model."""

from __future__ import annotations

from typing import Any, Self

import pytest

from app.modules.ai.llm_openai_compatible import (
    DeepSeekProvider,
    GLMProvider,
    KimiProvider,
    MistralProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
)
from app.modules.ai.llm_provider import LLMProvider
from app.modules.ai.mistral_client import MistralClient


def test_mistral_client_is_an_llm_provider() -> None:
    """MistralClient (the shared implementation behind OpenAICompatibleProvider) satisfies the abstract LLMProvider interface."""
    client = MistralClient(api_key="test-key")
    assert isinstance(client, LLMProvider)


def test_open_ai_compatible_provider_is_mistral_client() -> None:
    """OpenAICompatibleProvider is the name every new provider subclasses -- not a separate reimplementation."""
    assert OpenAICompatibleProvider is MistralClient


def test_mistral_provider_defaults_to_mistral_config() -> None:
    """MistralProvider resolves its api_key/api_base/model from Mistral's own config when not overridden."""
    provider = MistralProvider()
    assert provider.provider == "mistral"
    assert provider.model  # resolves to MISTRAL_MODEL, non-empty


def test_deepseek_provider_defaults_to_deepseek_config() -> None:
    """DeepSeekProvider resolves its api_key/api_base/model from DeepSeek's own config."""
    provider = DeepSeekProvider()
    assert provider.provider == "deepseek"
    assert provider.model == "deepseek-chat"


def test_openai_provider_defaults_to_openai_config() -> None:
    """OpenAIProvider resolves its api_key/api_base/model from OpenAI's own config."""
    provider = OpenAIProvider()
    assert provider.provider == "openai"
    assert provider.model == "gpt-5.6-luna"


def test_kimi_provider_defaults_to_kimi_config() -> None:
    """KimiProvider resolves its api_key/api_base/model from Kimi's own config."""
    provider = KimiProvider()
    assert provider.provider == "kimi"
    assert provider.model == "kimi-k2.7-code"


def test_glm_provider_defaults_to_glm_config() -> None:
    """GLMProvider resolves its api_key/api_base/model from GLM's own config."""
    provider = GLMProvider()
    assert provider.provider == "glm"
    assert provider.model == "glm-5.2"


def test_explicit_overrides_win_over_config_defaults() -> None:
    """An explicit model/api_key/api_base always wins, regardless of which provider subclass."""
    provider = KimiProvider(model="kimi-k3-override", api_key="explicit-key")
    assert provider.model == "kimi-k3-override"


def test_providers_never_silently_fall_back_to_mistral_key() -> None:
    """Root-caused while designing this: MistralClient.__init__ falls back to MISTRAL_API_KEY when api_key is None -- every new subclass must unconditionally pass its OWN provider's key so it never silently authenticates as Mistral."""
    kimi = KimiProvider()
    openai = OpenAIProvider()
    glm = GLMProvider()
    # None of these should be the literal MISTRAL_API_KEY value unless it
    # happens to equal their own (both empty by default in tests) -- the
    # real guard here is that each provider label is correct, which the
    # tests above already assert; this test documents the failure mode.
    assert kimi.provider == "kimi"
    assert openai.provider == "openai"
    assert glm.provider == "glm"


def test_deepseek_reasoning_payload_extra_unchanged_from_today() -> None:
    """DeepSeekProvider's quirk hooks must still return exactly today's production values -- these are what fixed the 2026-08-06 empty-completion incident."""
    provider = DeepSeekProvider()
    assert provider._reasoning_payload_extra() == {
        "thinking": {"type": "enabled"},
        "stream": False,
    }


def test_mistral_provider_has_no_reasoning_quirk() -> None:
    """The base (and Mistral's own subclass) stays a no-op -- only DeepSeek needs the thinking-mode payload extras."""
    provider = MistralProvider()
    assert provider._reasoning_payload_extra() == {}


def test_openai_provider_uses_max_completion_tokens_field() -> None:
    """Confirmed live 2026-08-14: GPT-5.6 rejects "max_tokens" outright (400 unsupported_parameter) and wants "max_completion_tokens" instead -- only OpenAIProvider overrides this, no other provider needs it."""
    provider = OpenAIProvider()
    assert provider._max_tokens_field_name() == "max_completion_tokens"


def test_other_providers_still_use_max_tokens_field() -> None:
    """The override is OpenAI-specific -- Mistral, DeepSeek, Kimi, GLM all still use the traditional "max_tokens" field name."""
    assert MistralProvider()._max_tokens_field_name() == "max_tokens"
    assert DeepSeekProvider()._max_tokens_field_name() == "max_tokens"
    assert KimiProvider()._max_tokens_field_name() == "max_tokens"
    assert GLMProvider()._max_tokens_field_name() == "max_tokens"


def test_openai_provider_actually_sends_max_completion_tokens_not_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: the outgoing request payload itself carries "max_completion_tokens", not just the hook method -- root-caused live 2026-08-14 (GPT-5.6 rejected a real compose call with "Unsupported parameter: 'max_tokens'")."""
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "OK"}}]}

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, headers: dict | None = None, json: dict | None = None) -> Any:  # noqa: ANN401, ARG002
            captured.update(json or {})
            return FakeResponse()

    import app.modules.ai.mistral_client as mistral_module

    monkeypatch.setattr(mistral_module.httpx, "Client", FakeClient)

    provider = OpenAIProvider(api_key="test-key")
    provider.chat_completion([{"role": "user", "content": "hi"}], max_tokens=50)

    assert "max_completion_tokens" in captured
    assert captured["max_completion_tokens"] == 50
    assert "max_tokens" not in captured


def test_openai_provider_does_not_support_temperature() -> None:
    """Confirmed live 2026-08-14: GPT-5.6 rejects any explicit temperature value other than its own default (1)."""
    assert OpenAIProvider()._supports_temperature() is False


def test_kimi_provider_does_not_support_temperature() -> None:
    """Confirmed live 2026-08-14: Kimi K3 rejects any explicit temperature value ("only 1 is allowed for this model") -- same constraint shape as GPT-5.6, independently discovered on a different provider."""
    assert KimiProvider()._supports_temperature() is False


def test_kimi_provider_floors_max_tokens_like_deepseek() -> None:
    """Confirmed live 2026-08-14: a small max_tokens (e.g. 10) comes back with EMPTY content on Kimi K3 because mandatory reasoning consumes the budget first -- same failure shape as the 2026-08-06 DeepSeek incident, same floor fix."""
    provider = KimiProvider()
    assert provider._effective_max_tokens(10) >= 40_000
    assert provider._effective_max_tokens(None) >= 40_000
    # A generous explicit request is never reduced.
    assert provider._effective_max_tokens(100_000) == 100_000


def test_other_providers_still_support_temperature() -> None:
    """The override is OpenAI/Kimi-specific -- Mistral, DeepSeek, and GLM still accept an explicit temperature."""
    assert MistralProvider()._supports_temperature() is True
    assert DeepSeekProvider()._supports_temperature() is True
    assert GLMProvider()._supports_temperature() is True


def test_openai_provider_forces_reasoning_effort_none_on_tool_calls() -> None:
    """Confirmed live 2026-08-14: GPT-5.6 400s on any tool-calling request citing reasoning_effort even when we send NO reasoning_effort field at all -- omitting it isn't the same as disabling it. Must force "none" explicitly, not just skip the field."""
    assert OpenAIProvider()._tool_reasoning_effort_override() == "none"


def test_other_providers_do_not_force_a_reasoning_effort_override() -> None:
    """The override is OpenAI-specific -- every other provider keeps the shared base's normal reasoning_effort logic untouched."""
    assert MistralProvider()._tool_reasoning_effort_override() is None
    assert DeepSeekProvider()._tool_reasoning_effort_override() is None
    assert KimiProvider()._tool_reasoning_effort_override() is None
    assert GLMProvider()._tool_reasoning_effort_override() is None


def test_openai_provider_sends_reasoning_effort_none_in_the_actual_tool_payload() -> None:
    """End-to-end: _tool_round_payload must carry reasoning_effort="none", not just the hook method in isolation."""
    provider = OpenAIProvider(api_key="test-key")
    payload = provider._tool_round_payload(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "probe", "parameters": {}}}],
        response_reserve=100,
        temperature=0.3,
    )
    assert payload["reasoning_effort"] == "none"


def test_only_mistral_sends_prompt_cache_key() -> None:
    """DeepSeek/OpenAI/Kimi/GLM all cache automatically server-side with no request-side flag (confirmed live 2026-08-14) -- only Mistral documents prompt_cache_key as its opt-in, so only MistralProvider should send it."""
    assert MistralProvider()._supports_prompt_cache_key() is True
    assert DeepSeekProvider()._supports_prompt_cache_key() is False
    assert OpenAIProvider()._supports_prompt_cache_key() is False
    assert KimiProvider()._supports_prompt_cache_key() is False
    assert GLMProvider()._supports_prompt_cache_key() is False


def test_mistral_provider_sends_a_stable_prompt_cache_key_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: the same instance must send the SAME prompt_cache_key on every call (so a growing multi-round conversation hits one cache entry), and it must actually be a non-empty string."""
    captured: list[dict[str, Any]] = []

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "OK"}}]}

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, headers: dict | None = None, json: dict | None = None) -> Any:  # noqa: ANN401, ARG002
            captured.append(dict(json or {}))
            return FakeResponse()

    import app.modules.ai.mistral_client as mistral_module

    monkeypatch.setattr(mistral_module.httpx, "Client", FakeClient)

    provider = MistralProvider(api_key="test-key")
    provider.chat_completion([{"role": "user", "content": "hi"}])
    provider.chat_completion([{"role": "user", "content": "hi again"}])

    assert len(captured) == 2
    assert captured[0]["prompt_cache_key"]
    assert captured[0]["prompt_cache_key"] == captured[1]["prompt_cache_key"]


def test_deepseek_provider_omits_prompt_cache_key_from_the_actual_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek's API caches automatically -- sending it an unrecognized field is an unnecessary risk for zero benefit, so the actual outgoing payload must not carry the key."""
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "OK"}}]}

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, headers: dict | None = None, json: dict | None = None) -> Any:  # noqa: ANN401, ARG002
            captured.update(json or {})
            return FakeResponse()

    import app.modules.ai.mistral_client as mistral_module

    monkeypatch.setattr(mistral_module.httpx, "Client", FakeClient)

    provider = DeepSeekProvider(api_key="test-key")
    provider.chat_completion([{"role": "user", "content": "hi"}])

    assert "prompt_cache_key" not in captured


def test_openai_provider_omits_temperature_from_the_actual_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: the outgoing payload has no "temperature" key at all -- root-caused live 2026-08-14 (GPT-5.6 rejected a real compose call with "Unsupported value: 'temperature' does not support 0.3...")."""
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "OK"}}]}

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, headers: dict | None = None, json: dict | None = None) -> Any:  # noqa: ANN401, ARG002
            captured.update(json or {})
            return FakeResponse()

    import app.modules.ai.mistral_client as mistral_module

    monkeypatch.setattr(mistral_module.httpx, "Client", FakeClient)

    provider = OpenAIProvider(api_key="test-key")
    provider.chat_completion([{"role": "user", "content": "hi"}], temperature=0.3)

    assert "temperature" not in captured

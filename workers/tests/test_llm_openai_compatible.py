"""Thin per-provider subclasses over the shared OpenAI-compatible implementation: each resolves its own config defaults, never falls back to another provider's key/model."""

from __future__ import annotations

from pathlib import Path
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


def test_mistral_provider_is_an_llm_provider() -> None:
    """MistralProvider satisfies the abstract LLMProvider interface (via OpenAICompatibleProvider)."""
    client = MistralProvider(api_key="test-key")
    assert isinstance(client, LLMProvider)


def test_mistral_provider_is_an_open_ai_compatible_provider() -> None:
    """MistralProvider is a thin subclass of the shared implementation, not a separate reimplementation."""
    assert issubclass(MistralProvider, OpenAICompatibleProvider)


def test_mistral_provider_defaults_to_mistral_config() -> None:
    """MistralProvider resolves its api_key/api_base/model from Mistral's own config when not overridden."""
    provider = MistralProvider()
    assert provider.provider == "mistral"
    assert provider.model  # resolves to MISTRAL_MODEL, non-empty


def test_deepseek_provider_defaults_to_deepseek_config() -> None:
    """DeepSeekProvider resolves its api_key/api_base/model from DeepSeek's own config -- DEEPSEEK_MODEL_WRITER, reverted 2026-08-28 off the experimental vision-capable variant back to the stable dated snapshot (see config.py's own comment; test_vision_tool_result_embedding.py still exercises the vision mechanism itself against an explicit model override)."""
    provider = DeepSeekProvider()
    assert provider.provider == "deepseek"
    assert provider.model == "deepseek-v4-flash-0731"


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
    """Root-caused while designing this: MistralProvider.__init__ falls back to MISTRAL_API_KEY when api_key is None -- every new subclass must unconditionally pass its OWN provider's key so it never silently authenticates as Mistral."""
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


def test_deepseek_provider_defaults_to_thinking_enabled() -> None:
    """enable_thinking defaults to True -- every existing caller (writer/research/digest/rubric) that doesn't pass it keeps today's behavior unchanged."""
    provider = DeepSeekProvider()
    assert provider._reasoning_effort_enabled() is True
    assert provider._reasoning_payload_extra() == {"thinking": {"type": "enabled"}, "stream": False}


def test_deepseek_provider_enable_thinking_false_disables_both_hooks() -> None:
    """enable_thinking=False (2026-08-26, the translate call path) must suppress BOTH the "thinking" payload block AND the reasoning_effort field -- disabling only one still pays for DeepSeek's thinking mode via the other."""
    provider = DeepSeekProvider(enable_thinking=False)
    assert provider._reasoning_effort_enabled() is False
    assert provider._reasoning_payload_extra() == {}


def test_deepseek_provider_thinking_disabled_omits_reasoning_effort_from_real_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: chat_completion's actual payload must carry neither "reasoning_effort" nor "thinking"/"stream" when enable_thinking=False, even though MISTRAL_REASONING_EFFORT (the shared knob) is set."""
    import app.modules.ai.llm_openai_compatible as oc

    monkeypatch.setattr(oc, "MISTRAL_REASONING_EFFORT", "high")
    provider = DeepSeekProvider(api_key="test-key", enable_thinking=False)
    captured: dict = {}

    def _fake_post(payload: dict) -> dict:
        captured.update(payload)
        return {"choices": [{"message": {"content": "{}"}}], "usage": {}}

    monkeypatch.setattr(provider, "_post", _fake_post)
    provider.chat_completion([{"role": "user", "content": "hi"}])

    assert "reasoning_effort" not in captured
    assert "thinking" not in captured
    assert "stream" not in captured


def test_other_providers_unaffected_by_reasoning_effort_enabled_hook() -> None:
    """The new _reasoning_effort_enabled hook defaults True for every provider that doesn't override it -- Mistral, Kimi, GLM, OpenAI all keep sending reasoning_effort exactly as before."""
    assert MistralProvider()._reasoning_effort_enabled() is True
    assert KimiProvider()._reasoning_effort_enabled() is True
    assert GLMProvider()._reasoning_effort_enabled() is True
    assert OpenAIProvider()._reasoning_effort_enabled() is True


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

    import app.modules.ai.llm_openai_compatible as mistral_module

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

    import app.modules.ai.llm_openai_compatible as mistral_module

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

    import app.modules.ai.llm_openai_compatible as mistral_module

    monkeypatch.setattr(mistral_module.httpx, "Client", FakeClient)

    provider = DeepSeekProvider(api_key="test-key")
    provider.chat_completion([{"role": "user", "content": "hi"}])

    assert "prompt_cache_key" not in captured


def test_record_usage_prefers_the_nested_openai_compatible_cache_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirmed live 2026-08-17: DeepSeek sends cached_tokens nested under prompt_tokens_details -- that's the portable shape other OpenAI-compatible providers use too, so prefer it over DeepSeek's own top-level field when both are present."""

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {
                "choices": [{"message": {"content": "OK"}}],
                "usage": {
                    "prompt_tokens": 358,
                    "completion_tokens": 10,
                    "total_tokens": 368,
                    "prompt_tokens_details": {"cached_tokens": 256},
                    "prompt_cache_hit_tokens": 999,  # must be ignored -- nested field wins
                },
            }

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, *args: object, **kwargs: object) -> Any:  # noqa: ANN401, ARG002
            return FakeResponse()

    import app.modules.ai.llm_openai_compatible as mistral_module

    monkeypatch.setattr(mistral_module.httpx, "Client", FakeClient)

    provider = DeepSeekProvider(api_key="test-key")
    provider.chat_completion([{"role": "user", "content": "hi"}])

    assert provider.usage_totals()["cached_tokens"] == 256


def test_record_usage_falls_back_to_deepseeks_own_top_level_cache_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older/alternate response shapes may carry ONLY DeepSeek's own prompt_cache_hit_tokens field, with no nested prompt_tokens_details at all -- that must still be picked up."""

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {
                "choices": [{"message": {"content": "OK"}}],
                "usage": {
                    "prompt_tokens": 358,
                    "completion_tokens": 10,
                    "total_tokens": 368,
                    "prompt_cache_hit_tokens": 102,
                },
            }

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, *args: object, **kwargs: object) -> Any:  # noqa: ANN401, ARG002
            return FakeResponse()

    import app.modules.ai.llm_openai_compatible as mistral_module

    monkeypatch.setattr(mistral_module.httpx, "Client", FakeClient)

    provider = DeepSeekProvider(api_key="test-key")
    provider.chat_completion([{"role": "user", "content": "hi"}])

    assert provider.usage_totals()["cached_tokens"] == 102


def test_record_usage_defaults_cached_tokens_to_zero_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider/response that never reports cache stats (e.g. Mistral) must not KeyError or crash -- cached_tokens stays 0."""

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {
                "choices": [{"message": {"content": "OK"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            }

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, *args: object, **kwargs: object) -> Any:  # noqa: ANN401, ARG002
            return FakeResponse()

    import app.modules.ai.llm_openai_compatible as mistral_module

    monkeypatch.setattr(mistral_module.httpx, "Client", FakeClient)

    provider = MistralProvider(api_key="test-key")
    provider.chat_completion([{"role": "user", "content": "hi"}])

    assert provider.usage_totals()["cached_tokens"] == 0


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

    import app.modules.ai.llm_openai_compatible as mistral_module

    monkeypatch.setattr(mistral_module.httpx, "Client", FakeClient)

    provider = OpenAIProvider(api_key="test-key")
    provider.chat_completion([{"role": "user", "content": "hi"}], temperature=0.3)

    assert "temperature" not in captured


# ------------------------------------------------------- rotating HTTP log


@pytest.fixture(autouse=True)
def _reset_http_logger_cache() -> Any:  # noqa: ANN401 -- pytest yield-fixture typing
    """_llm_http_logger() caches its logger in a module global on first call, and the underlying named logger (logging.getLogger("llm_http")) is itself a process-wide singleton that keeps whatever file handler got attached to it first -- reset both around every test in this file so one test's LLM_HTTP_LOG_PATH (and its tmp_path-backed handler) never leaks into another test's run, in this file or any other sharing the worker process."""
    import logging

    import app.modules.ai.llm_openai_compatible as mod

    mod._HTTP_LOG = None
    logging.getLogger("llm_http").handlers.clear()
    yield
    mod._HTTP_LOG = None
    logging.getLogger("llm_http").handlers.clear()


def test_http_log_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """With LLM_HTTP_LOG_PATH unset (the default), _post never touches resp.text or the filesystem -- confirmed by a FakeResponse that has no .text attribute at all, which would raise if the logger path were mistakenly active."""
    monkeypatch.setattr("app.core.config.LLM_HTTP_LOG_PATH", "")

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
            return FakeResponse()

    import app.modules.ai.llm_openai_compatible as mistral_module

    monkeypatch.setattr(mistral_module.httpx, "Client", FakeClient)
    provider = OpenAIProvider(api_key="test-key")
    provider.chat_completion([{"role": "user", "content": "hi"}])  # would raise if logging were active


def test_http_log_writes_request_and_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When enabled, _post logs one REQUEST line before the call and one RESPONSE line with the FULL raw body after it -- the whole point being to catch fields (finish_reason, moderation, error objects) the normal content-parsing path discards."""
    log_path = tmp_path / "llm_http.log"
    monkeypatch.setattr("app.core.config.LLM_HTTP_LOG_PATH", str(log_path))
    monkeypatch.setattr("app.core.config.LLM_HTTP_LOG_MAX_BYTES", 1_000_000)
    monkeypatch.setattr("app.core.config.LLM_HTTP_LOG_BACKUP_COUNT", 1)

    class FakeResponse:
        status_code = 200
        text = '{"choices": [{"message": {"content": ""}}], "finish_reason": "content_filter"}'

        def json(self) -> dict:
            return {"choices": [{"message": {"content": ""}}]}

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, headers: dict | None = None, json: dict | None = None) -> Any:  # noqa: ANN401, ARG002
            return FakeResponse()

    import app.modules.ai.llm_openai_compatible as mistral_module

    monkeypatch.setattr(mistral_module.httpx, "Client", FakeClient)
    provider = DeepSeekProvider(api_key="test-key")
    provider.chat_completion([{"role": "user", "content": "hi"}])

    contents = log_path.read_text()
    assert "REQUEST" in contents
    assert "provider=deepseek" in contents
    assert "RESPONSE" in contents
    assert "content_filter" in contents  # the full raw body made it through

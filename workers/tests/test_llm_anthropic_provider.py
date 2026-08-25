"""AnthropicProvider: translation to/from Claude's native Messages API (system field, tool_use/tool_result content blocks), verified against a mocked response shape (no live Anthropic access in this environment)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Self

import httpx
import pytest

from app.modules.ai.llm_anthropic_provider import (
    AnthropicProvider,
    _openai_messages_to_anthropic,
    _openai_tools_to_anthropic,
)
from app.modules.ai.llm_provider import LLMCreditError, LLMError, LLMProvider


def _fake_client(post_fn: Callable[..., object]) -> type:
    """Build a fake httpx.Client class whose .post(...) delegates to post_fn -- same pattern the other provider tests use."""

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(
            self, url: str, headers: dict | None = None, json: dict | None = None
        ) -> object:
            return post_fn(url, headers, json)

    return FakeClient


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, fake_client_cls: type) -> None:
    """Swap llm_anthropic_provider's httpx.Client for the fake one for the duration of one test."""
    import app.modules.ai.llm_anthropic_provider as anthropic_module

    monkeypatch.setattr(anthropic_module.httpx, "Client", fake_client_cls)


def _response(status_code: int, body: dict) -> object:
    """A minimal fake httpx.Response: .status_code, .text, .json()."""

    class FakeResponse:
        pass

    r = FakeResponse()
    r.status_code = status_code
    r.text = str(body)
    r.json = lambda: body
    return r


def test_anthropic_provider_is_an_llm_provider() -> None:
    """AnthropicProvider satisfies the abstract LLMProvider interface despite its different wire format."""
    assert isinstance(AnthropicProvider(api_key="k"), LLMProvider)


def test_messages_to_anthropic_splits_system_prompt_out() -> None:
    """Anthropic takes the system prompt as a top-level field, not a "system"-role message."""
    system_text, messages = _openai_messages_to_anthropic(
        [
            {"role": "system", "content": "You are a helpful writer."},
            {"role": "user", "content": "Write the article."},
        ]
    )
    assert system_text == "You are a helpful writer."
    assert messages == [{"role": "user", "content": [{"type": "text", "text": "Write the article."}]}]


def test_messages_to_anthropic_translates_tool_calls_to_tool_use_blocks() -> None:
    """OpenAI tool_calls -> Anthropic tool_use content blocks, preserving a real id."""
    _system, messages = _openai_messages_to_anthropic(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_abc123",
                        "function": {"name": "search_web", "arguments": '{"query": "algorand"}'},
                    }
                ],
            }
        ]
    )
    assert messages[0]["content"] == [
        {"type": "tool_use", "id": "call_abc123", "name": "search_web", "input": {"query": "algorand"}}
    ]


def test_messages_to_anthropic_generates_a_synthetic_id_when_missing() -> None:
    """A tool_call with no id (e.g. history from a DeepSeek research pass) still gets a real id -- Anthropic's API rejects a tool_use block without one."""
    _system, messages = _openai_messages_to_anthropic(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "search_web", "arguments": "{}"}}],
            }
        ]
    )
    tool_use_id = messages[0]["content"][0]["id"]
    assert tool_use_id


def test_messages_to_anthropic_translates_tool_result_and_pairs_by_id() -> None:
    """OpenAI tool-role messages -> Anthropic tool_result blocks, paired to the original tool_use via tool_use_id."""
    _system, messages = _openai_messages_to_anthropic(
        [{"role": "tool", "name": "search_web", "tool_call_id": "call_abc123", "content": '{"ok": true}'}]
    )
    assert messages[0] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_abc123", "content": '{"ok": true}'}],
    }


def test_tools_to_anthropic_uses_input_schema_field_name() -> None:
    """Anthropic calls the JSON schema field input_schema, not parameters."""
    anthropic_tools = _openai_tools_to_anthropic(
        [
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "Search the web",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            }
        ]
    )
    assert anthropic_tools == [
        {
            "name": "search_web",
            "description": "Search the web",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        }
    ]


def test_chat_completion_extracts_text_and_records_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plain completion extracts the text block and records usage into the shared {prompt,completion,total}_tokens shape."""
    body = {
        "content": [{"type": "text", "text": "Hello there"}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    _patch_httpx(monkeypatch, _fake_client(lambda *_a: _response(200, body)))

    provider = AnthropicProvider(api_key="test-key")
    result = provider.chat_completion([{"role": "user", "content": "hi"}])

    assert result == "Hello there"
    assert provider.usage_totals() == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cached_tokens": 0,
    }


def test_chat_json_object_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat_json_object parses the text block as JSON (Anthropic has no dedicated JSON-mode flag, so it's nudged via an appended instruction)."""
    body = {
        "content": [{"type": "text", "text": '{"title": "t", "body": "b"}'}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    _patch_httpx(monkeypatch, _fake_client(lambda *_a: _response(200, body)))

    provider = AnthropicProvider(api_key="test-key")
    result = provider.chat_json_object([{"role": "user", "content": "hi"}])

    assert result == {"title": "t", "body": "b"}


def test_chat_json_object_raises_llm_error_on_non_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-JSON text raises LLMError rather than returning garbage."""
    body = {"content": [{"type": "text", "text": "not json"}], "usage": {}}
    _patch_httpx(monkeypatch, _fake_client(lambda *_a: _response(200, body)))

    provider = AnthropicProvider(api_key="test-key")
    with pytest.raises(LLMError, match="non-JSON"):
        provider.chat_json_object([{"role": "user", "content": "hi"}])


def test_chat_with_tools_executes_a_tool_use_then_returns_final_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 1: model emits a tool_use block. Round 2: model returns final text, no more tool_use."""
    responses = [
        {
            "content": [
                {"type": "tool_use", "id": "call_1", "name": "search_web", "input": {"query": "x"}}
            ],
            "usage": {},
        },
        {"content": [{"type": "text", "text": "FINAL"}], "usage": {}},
    ]
    calls = {"n": 0}

    def _post(*_a: object) -> object:
        i = calls["n"]
        calls["n"] += 1
        return _response(200, responses[i])

    _patch_httpx(monkeypatch, _fake_client(_post))

    provider = AnthropicProvider(api_key="test-key")
    handler_calls = []
    result = provider.chat_with_tools(
        [{"role": "user", "content": "research this"}],
        tools=[{"type": "function", "function": {"name": "search_web", "parameters": {}}}],
        handlers={"search_web": lambda **kw: handler_calls.append(kw) or {"ok": True}},
    )

    assert result == "FINAL"
    assert handler_calls == [{"query": "x"}]
    assert calls["n"] == 2


def test_chat_with_tools_nudges_once_for_a_required_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """No tool_use and require_tool not yet satisfied -> one nudge message, then accepts the next round's plain text."""
    responses = [
        {"content": [{"type": "text", "text": "thinking..."}], "usage": {}},
        {"content": [{"type": "text", "text": "FINAL"}], "usage": {}},
    ]
    calls = {"n": 0}

    def _post(*_a: object) -> object:
        i = calls["n"]
        calls["n"] += 1
        return _response(200, responses[i])

    _patch_httpx(monkeypatch, _fake_client(_post))

    provider = AnthropicProvider(api_key="test-key")
    result = provider.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[],
        handlers={},
        require_tool="review_draft",
    )
    assert result == "FINAL"
    assert calls["n"] == 2


def test_post_raises_llm_credit_error_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 (dead/invalid key) raises LLMCreditError, not a generic LLMError."""
    _patch_httpx(monkeypatch, _fake_client(lambda *_a: _response(401, {"error": "unauthorized"})))
    provider = AnthropicProvider(api_key="bad-key")
    with pytest.raises(LLMCreditError):
        provider.chat_completion([{"role": "user", "content": "hi"}])


def test_post_retries_then_raises_on_persistent_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A persistent network error retries the configured number of times, then raises LLMError."""

    class _ExplodingClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, *_a: object, **_kw: object) -> object:
            raise httpx.ConnectError("boom")

    _patch_httpx(monkeypatch, _ExplodingClient)
    provider = AnthropicProvider(api_key="test-key")
    with pytest.raises(LLMError, match="Anthropic request failed"):
        provider.chat_completion([{"role": "user", "content": "hi"}])


def test_anthropic_registered_in_llm_registry() -> None:
    """get_provider("anthropic") resolves to AnthropicProvider -- the actual load-bearing wiring for the benchmark script."""
    from app.modules.ai.llm_registry import get_provider, known_providers

    assert "anthropic" in known_providers()
    assert isinstance(get_provider("anthropic"), AnthropicProvider)


def test_anthropic_configured_key_env_var_matches_registry() -> None:
    """The benchmark script's provider-key-config map points at the right env var name."""
    import scripts.benchmark_compose_providers as bm

    assert bm._PROVIDER_KEY_CONFIG["anthropic"] == "ANTHROPIC_API_KEY"

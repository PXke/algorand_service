"""GeminiProvider: translation to/from Gemini's native contents/parts/functionCall wire format, verified against a mocked response shape (no live Gemini access in this environment)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Self

import httpx
import pytest

from app.modules.ai.llm_gemini_provider import (
    GeminiProvider,
    _openai_messages_to_gemini_contents,
    _openai_tools_to_gemini,
)
from app.modules.ai.llm_provider import LLMError, LLMProvider


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _seconds: None)


def _fake_client(post_fn: Callable[..., object]) -> type:
    """Build a fake httpx.Client class whose .post(...) delegates to post_fn -- same pattern test_mistral_client.py uses."""

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
    """Swap llm_gemini_provider's httpx.Client for the fake one for the duration of one test."""
    import app.modules.ai.llm_gemini_provider as gemini_module

    monkeypatch.setattr(gemini_module.httpx, "Client", fake_client_cls)


def _response(status_code: int, body: dict) -> object:
    """A minimal fake httpx.Response: .status_code, .text, .json()."""

    class FakeResponse:
        pass

    r = FakeResponse()
    r.status_code = status_code
    r.text = str(body)
    r.json = lambda: body
    return r


def test_gemini_provider_is_an_llm_provider() -> None:
    """GeminiProvider satisfies the abstract LLMProvider interface despite its different wire format."""
    assert isinstance(GeminiProvider(api_key="k"), LLMProvider)


def test_messages_to_contents_splits_system_prompt_out() -> None:
    """Gemini takes the system prompt as a top-level field, not a "system"-role content entry."""
    system_text, contents = _openai_messages_to_gemini_contents(
        [
            {"role": "system", "content": "You are a helpful writer."},
            {"role": "user", "content": "Write the article."},
        ]
    )
    assert system_text == "You are a helpful writer."
    assert contents == [{"role": "user", "parts": [{"text": "Write the article."}]}]


def test_messages_to_contents_maps_assistant_to_model_role() -> None:
    """Gemini uses role: "model" for assistant turns, not "assistant"."""
    _system, contents = _openai_messages_to_gemini_contents(
        [{"role": "assistant", "content": "Here is my answer."}]
    )
    assert contents[0]["role"] == "model"


def test_messages_to_contents_translates_tool_calls_to_function_call_parts() -> None:
    """OpenAI tool_calls -> Gemini functionCall parts."""
    _system, contents = _openai_messages_to_gemini_contents(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "search_web", "arguments": '{"query": "algorand"}'}}
                ],
            }
        ]
    )
    assert contents[0]["parts"] == [
        {"functionCall": {"name": "search_web", "args": {"query": "algorand"}}}
    ]


def test_messages_to_contents_translates_tool_result_to_function_response() -> None:
    """OpenAI tool-role messages -> Gemini functionResponse parts."""
    _system, contents = _openai_messages_to_gemini_contents(
        [{"role": "tool", "name": "search_web", "content": '{"ok": true}'}]
    )
    assert contents[0] == {
        "role": "function",
        "parts": [{"functionResponse": {"name": "search_web", "response": {"result": {"ok": True}}}}],
    }


def test_tools_to_gemini_wraps_declarations_in_one_block() -> None:
    """OpenAI's [{"type": "function", "function": {...}}] -> one Gemini functionDeclarations block."""
    gemini_tools = _openai_tools_to_gemini(
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
    assert gemini_tools == [
        {
            "functionDeclarations": [
                {
                    "name": "search_web",
                    "description": "Search the web",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                }
            ]
        }
    ]


def test_chat_completion_extracts_text_and_records_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plain completion extracts the candidate's text and records usageMetadata into the shared {prompt,completion,total}_tokens shape."""
    body = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "Hello there"}]}}],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 5,
            "totalTokenCount": 15,
        },
    }
    _patch_httpx(monkeypatch, _fake_client(lambda *_a: _response(200, body)))

    provider = GeminiProvider(api_key="test-key")
    result = provider.chat_completion([{"role": "user", "content": "hi"}])

    assert result == "Hello there"
    assert provider.usage_totals() == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cached_tokens": 0,
    }


def test_chat_json_object_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat_json_object parses the candidate's text as JSON."""
    body = {
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": '{"title": "t", "body": "b"}'}]}}
        ],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
    }
    _patch_httpx(monkeypatch, _fake_client(lambda *_a: _response(200, body)))

    provider = GeminiProvider(api_key="test-key")
    result = provider.chat_json_object([{"role": "user", "content": "hi"}])

    assert result == {"title": "t", "body": "b"}


def test_chat_json_object_raises_llm_error_on_non_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-JSON candidate text raises LLMError rather than returning garbage."""
    body = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "not json"}]}}],
        "usageMetadata": {},
    }
    _patch_httpx(monkeypatch, _fake_client(lambda *_a: _response(200, body)))

    provider = GeminiProvider(api_key="test-key")
    with pytest.raises(LLMError, match="non-JSON"):
        provider.chat_json_object([{"role": "user", "content": "hi"}])


def test_chat_with_tools_executes_a_function_call_then_returns_final_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 1: model calls search_web. Round 2: model returns final text, no more calls."""
    responses = [
        {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"functionCall": {"name": "search_web", "args": {"query": "x"}}}],
                    }
                }
            ],
            "usageMetadata": {},
        },
        {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "FINAL"}]}}],
            "usageMetadata": {},
        },
    ]
    calls = {"n": 0}

    def _post(*_a: object) -> object:
        i = calls["n"]
        calls["n"] += 1
        return _response(200, responses[i])

    _patch_httpx(monkeypatch, _fake_client(_post))

    provider = GeminiProvider(api_key="test-key")
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
    """No function calls and require_tool not yet satisfied -> one nudge message, then accepts the next round's plain text."""
    """No function calls and require_tool not yet satisfied -> one nudge message, then accepts the next round's plain text."""
    responses = [
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "thinking..."}]}}], "usageMetadata": {}},
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "FINAL"}]}}], "usageMetadata": {}},
    ]
    calls = {"n": 0}

    def _post(*_a: object) -> object:
        i = calls["n"]
        calls["n"] += 1
        return _response(200, responses[i])

    _patch_httpx(monkeypatch, _fake_client(_post))

    provider = GeminiProvider(api_key="test-key")
    result = provider.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[],
        handlers={},
        require_tool="review_draft",
    )
    assert result == "FINAL"
    assert calls["n"] == 2


def test_post_raises_llm_credit_error_on_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 403 (Gemini's equivalent of a dead/forbidden key) raises LLMCreditError, not a generic LLMError."""
    _patch_httpx(monkeypatch, _fake_client(lambda *_a: _response(403, {"error": "forbidden"})))
    provider = GeminiProvider(api_key="bad-key")
    from app.modules.ai.llm_provider import LLMCreditError

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
    provider = GeminiProvider(api_key="test-key")
    with pytest.raises(LLMError, match="Gemini request failed"):
        provider.chat_completion([{"role": "user", "content": "hi"}])

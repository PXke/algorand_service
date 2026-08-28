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
from app.modules.ai.story_spike import StorySpikedError


def _fake_client(post_fn: Callable[..., object]) -> type:
    """Build a fake httpx.Client class whose .post(...) delegates to post_fn -- same pattern the other provider tests use."""

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, headers: dict | None = None, json: dict | None = None) -> object:
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
    assert messages == [
        {"role": "user", "content": [{"type": "text", "text": "Write the article."}]}
    ]


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
        {
            "type": "tool_use",
            "id": "call_abc123",
            "name": "search_web",
            "input": {"query": "algorand"},
        }
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
        [
            {
                "role": "tool",
                "name": "search_web",
                "tool_call_id": "call_abc123",
                "content": '{"ok": true}',
            }
        ]
    )
    assert messages[0] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "call_abc123", "content": '{"ok": true}'}
        ],
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


def test_chat_with_tools_reraises_story_spiked_error_and_records_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The writer aborting the article (abort_article) must escape chat_with_tools uncaught, same contract as OpenAICompatibleProvider (see test_story_spike.py) -- before this fix it was caught by the generic `except Exception` and fed back to the model as an ordinary {"error": ...} tool result, silently defeating the abort."""
    responses = [
        {
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "abort_article",
                    "input": {
                        "category": "dead_project",
                        "reason": "no on-chain activity since 2021",
                    },
                }
            ],
            "usage": {},
        },
    ]
    calls = {"n": 0}

    def _post(*_a: object) -> object:
        i = calls["n"]
        calls["n"] += 1
        return _response(200, responses[i])

    _patch_httpx(monkeypatch, _fake_client(_post))

    def spike_handler(**_kw: object) -> dict:
        raise StorySpikedError("no on-chain activity since 2021", "dead_project")

    provider = AnthropicProvider(api_key="test-key")
    trace: list = []
    with pytest.raises(StorySpikedError):
        provider.chat_with_tools(
            [{"role": "user", "content": "research this"}],
            tools=[{"type": "function", "function": {"name": "abort_article", "parameters": {}}}],
            handlers={"abort_article": spike_handler},
            trace=trace,
        )
    assert calls["n"] == 1  # never asked the model to continue past the spike
    assert trace, "spike call must be recorded in the trace before re-raising"
    assert trace[-1]["tool"] == "abort_article"
    assert trace[-1]["result"]["spiked"] is True
    assert trace[-1]["result"]["category"] == "dead_project"


def test_chat_with_tools_dedup_nudges_an_identical_repeat_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared llm_tool_loop driver's seen-calls dedup cache (previously enforced for OpenAI-compatible providers only, see test_writer_tool_loop.py's cross-pass-dedup tests) now protects Anthropic sessions too: an exact repeat of an already-executed call is nudged, not re-run."""
    call = {"type": "tool_use", "id": "call_1", "name": "search_web", "input": {"query": "x"}}
    responses = [
        {"content": [call], "usage": {}},
        {"content": [{**call, "id": "call_2"}], "usage": {}},
        {"content": [{"type": "text", "text": "FINAL"}], "usage": {}},
    ]
    calls = {"n": 0}

    def _post(*_a: object) -> object:
        i = calls["n"]
        calls["n"] += 1
        return _response(200, responses[i])

    _patch_httpx(monkeypatch, _fake_client(_post))

    provider = AnthropicProvider(api_key="test-key")
    executed = {"n": 0}

    def handler(**_kw: object) -> dict:
        executed["n"] += 1
        return {"ok": True}

    result = provider.chat_with_tools(
        [{"role": "user", "content": "research this"}],
        tools=[{"type": "function", "function": {"name": "search_web", "parameters": {}}}],
        handlers={"search_web": handler},
    )

    assert result == "FINAL"
    assert executed["n"] == 1  # the exact repeat in round 2 was nudged, never re-executed
    assert calls["n"] == 3


def test_chat_with_tools_enforces_the_shared_per_tool_call_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """search_x's per-session call cap (llm_tool_loop.CALL_CAPPED_TOOLS, previously enforced for OpenAI-compatible providers only) now also applies to an Anthropic session via the shared driver: a call past the cap is refused outright without ever reaching the handler."""
    responses = [
        {
            "content": [
                {"type": "tool_use", "id": "call_1", "name": "search_x", "input": {"query": "new"}}
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
    executed = {"n": 0}

    def handler(**_kw: object) -> dict:
        executed["n"] += 1
        return {"count": 0}

    # Seed the trace as if 3 earlier search_x calls (the cap) already ran
    # this session -- cross-pass seeding is itself shared driver behavior.
    trace = [
        {"tool": "search_x", "arguments": {"query": f"q{i}"}, "result": {"count": 0}}
        for i in range(3)
    ]
    result = provider.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[{"type": "function", "function": {"name": "search_x", "parameters": {}}}],
        handlers={"search_x": handler},
        trace=trace,
    )

    assert result == "FINAL"
    assert executed["n"] == 0  # capped refusal -- handler never ran
    assert calls["n"] == 2


def test_chat_with_tools_exhaustion_sets_debug_flags_and_skips_finalize_when_told(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running out of rounds (the round-budget ceiling) sets debug["rounds"]/["exhausted"] -- previously OpenAI-only bookkeeping, now shared -- and, with finalize_on_exhaustion=False, returns the last seen text without paying for the extra wrap-up completion, matching OpenAICompatibleProvider's own exhaustion contract."""
    call = {
        "type": "tool_use",
        "id": "call_1",
        "name": "fetch_url",
        "input": {"url": "https://example.com/1"},
    }

    def _post(*_a: object) -> object:
        return _response(200, {"content": [call], "usage": {}})

    _patch_httpx(monkeypatch, _fake_client(_post))

    provider = AnthropicProvider(api_key="test-key")
    debug: dict = {}
    result = provider.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[{"type": "function", "function": {"name": "fetch_url", "parameters": {}}}],
        handlers={"fetch_url": lambda **_k: {"ok": True}},
        max_rounds=2,
        finalize_on_exhaustion=False,
        debug=debug,
    )

    assert result == ""  # every round called a tool, never produced text content
    assert debug["rounds"] == 2
    assert debug["exhausted"] is True


def test_chat_with_tools_debug_transcript_is_a_live_native_messages_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """debug["messages"] is now the same kind of full, live-mutating transcript OpenAICompatibleProvider has always produced -- before this refactor Anthropic recorded no transcript detail at all beyond a one-line-per-round text summary. debug["model"] is set too."""
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

    provider = AnthropicProvider(api_key="test-key", model="claude-test-model")
    debug: dict = {}
    result = provider.chat_with_tools(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "research this"}],
        tools=[{"type": "function", "function": {"name": "search_web", "parameters": {}}}],
        handlers={"search_web": lambda **_kw: {"ok": True}},
        debug=debug,
    )

    assert result == "FINAL"
    assert debug["model"] == "claude-test-model"
    # The real native messages list -- role/content-block shape, not a
    # synthetic one-line summary -- so a tool_result block is really in there.
    assert any(
        isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m["content"])
        for m in debug["messages"]
        if m.get("role") == "user"
    )


def test_post_raises_llm_credit_error_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 (dead/invalid key) raises LLMCreditError, not a generic LLMError."""
    _patch_httpx(monkeypatch, _fake_client(lambda *_a: _response(401, {"error": "unauthorized"})))
    provider = AnthropicProvider(api_key="bad-key")
    with pytest.raises(LLMCreditError):
        provider.chat_completion([{"role": "user", "content": "hi"}])


def test_post_retries_then_raises_on_persistent_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

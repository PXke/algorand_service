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
from app.modules.ai.story_spike import StorySpikedError


def _fake_client(post_fn: Callable[..., object]) -> type:
    """Build a fake httpx.Client class whose .post(...) delegates to post_fn -- same pattern test_mistral_provider.py uses."""

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
        "parts": [
            {"functionResponse": {"name": "search_web", "response": {"result": {"ok": True}}}}
        ],
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
        {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "thinking..."}]}}],
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
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "abort_article",
                                    "args": {
                                        "category": "dead_project",
                                        "reason": "no on-chain activity since 2021",
                                    },
                                }
                            }
                        ],
                    }
                }
            ],
            "usageMetadata": {},
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

    provider = GeminiProvider(api_key="test-key")
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


def test_post_raises_llm_credit_error_on_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 403 (Gemini's equivalent of a dead/forbidden key) raises LLMCreditError, not a generic LLMError."""
    _patch_httpx(monkeypatch, _fake_client(lambda *_a: _response(403, {"error": "forbidden"})))
    provider = GeminiProvider(api_key="bad-key")
    from app.modules.ai.llm_provider import LLMCreditError

    with pytest.raises(LLMCreditError):
        provider.chat_completion([{"role": "user", "content": "hi"}])


def test_chat_with_tools_dedup_nudges_an_identical_repeat_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared llm_tool_loop driver's seen-calls dedup cache (previously enforced for OpenAI-compatible providers only, see test_writer_tool_loop.py's cross-pass-dedup tests) now protects Gemini sessions too: an exact repeat of an already-executed call is nudged, not re-run."""
    call = {"functionCall": {"name": "search_web", "args": {"query": "x"}}}
    responses = [
        {"candidates": [{"content": {"role": "model", "parts": [call]}}], "usageMetadata": {}},
        {"candidates": [{"content": {"role": "model", "parts": [call]}}], "usageMetadata": {}},
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
    """search_x's per-session call cap (llm_tool_loop.CALL_CAPPED_TOOLS, previously enforced for OpenAI-compatible providers only) now also applies to a Gemini session via the shared driver: a call past the cap is refused outright without ever reaching the handler."""
    responses = [
        {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"functionCall": {"name": "search_x", "args": {"query": "new"}}}],
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
    call = {"functionCall": {"name": "fetch_url", "args": {"url": "https://example.com/1"}}}

    def _post(*_a: object) -> object:
        return _response(
            200,
            {"candidates": [{"content": {"role": "model", "parts": [call]}}], "usageMetadata": {}},
        )

    _patch_httpx(monkeypatch, _fake_client(_post))

    provider = GeminiProvider(api_key="test-key")
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


def test_chat_with_tools_debug_transcript_is_a_live_native_contents_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """debug["messages"] is now the same kind of full, live-mutating transcript OpenAICompatibleProvider has always produced -- before this refactor Gemini only ever recorded a synthetic one-line-per-round summary, dropping the actual functionCall/functionResponse detail. debug["model"] is set too."""
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

    provider = GeminiProvider(api_key="test-key", model="gemini-test-model")
    debug: dict = {}
    result = provider.chat_with_tools(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "research this"}],
        tools=[{"type": "function", "function": {"name": "search_web", "parameters": {}}}],
        handlers={"search_web": lambda **_kw: {"ok": True}},
        debug=debug,
    )

    assert result == "FINAL"
    assert debug["model"] == "gemini-test-model"
    # The real native contents list -- role/parts shape, not a synthetic
    # one-line summary -- so a functionResponse turn is really in there.
    assert any(m.get("role") == "function" for m in debug["messages"])
    assert any(
        "functionCall" in p
        for m in debug["messages"]
        if m.get("role") == "model"
        for p in m.get("parts", [])
    )


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
    provider = GeminiProvider(api_key="test-key")
    with pytest.raises(LLMError, match="Gemini request failed"):
        provider.chat_completion([{"role": "user", "content": "hi"}])

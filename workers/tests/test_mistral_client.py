"""Mistral chat/completion parsing, retry, and the credit-exhaustion circuit breaker."""

from __future__ import annotations

import json
from typing import Any, Self

import httpx
import pytest

from app.modules.ai.mistral_client import MistralClient, MistralCreditError, MistralError


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retryable-status tests below exercise MistralClient's real backoff (MISTRAL_MAX_RETRIES) — without this they genuinely sleep out the full schedule (~30s) for zero extra coverage. Retry count/behavior is still exercised; only the wall-clock wait is removed."""
    monkeypatch.setattr("time.sleep", lambda _seconds: None)


def test_chat_json_object_parses_response() -> None:
    """chat_json_object parses a well-formed choices[0].message.content JSON reply."""
    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "title": "Hello",
                            "summary": "Short",
                            "body": "# Hello\n\nBody",
                        }
                    )
                }
            }
        ]
    }

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return payload

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, headers: dict | None = None, json: dict | None = None) -> Any:  # noqa: ARG002, ANN401 -- name must match the real callee's keyword arg
            assert "chat/completions" in url
            assert headers["Authorization"] == "Bearer test-key"
            return FakeResponse()

    client = MistralClient(api_key="test-key")
    import app.modules.ai.mistral_client as mistral_module

    original = httpx.Client
    mistral_module.httpx.Client = FakeClient
    try:
        result = client.chat_json_object([{"role": "user", "content": "hi"}])
    finally:
        mistral_module.httpx.Client = original

    assert result["title"] == "Hello"


def test_chat_json_object_empty_reply_retries_without_assistant_echo() -> None:
    """An empty first reply must NOT be echoed back as an assistant message — Mistral 400s on assistant messages with neither content nor tool_calls."""
    responses = [
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": json.dumps({"title": "Hello"})}}]},
    ]
    seen_messages: list[list[dict]] = []

    class FakeResponse:
        status_code = 200

        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, _url: str, headers: dict | None = None, json: dict | None = None) -> Any:  # noqa: ARG002, ANN401 -- name must match the real callee's keyword arg
            seen_messages.append(json["messages"])
            return FakeResponse(responses[len(seen_messages) - 1])

    client = MistralClient(api_key="test-key")
    import app.modules.ai.mistral_client as mistral_module

    original = httpx.Client
    mistral_module.httpx.Client = FakeClient
    try:
        result = client.chat_json_object([{"role": "user", "content": "hi"}])
    finally:
        mistral_module.httpx.Client = original

    assert result["title"] == "Hello"
    assert len(seen_messages) == 2
    assert all(m["role"] != "assistant" for m in seen_messages[1])


def test_chat_completion_raises_on_http_error() -> None:
    """chat_completion raises MistralError on an HTTP error response."""

    class FakeResponse:
        status_code = 401
        text = "unauthorized"

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, _url: str, headers: dict | None = None, json: dict | None = None) -> Any:  # noqa: ARG002, ANN401 -- name must match the real callee's keyword arg
            return FakeResponse()

    client = MistralClient(api_key="test-key")
    import app.modules.ai.mistral_client as mistral_module

    original = httpx.Client
    mistral_module.httpx.Client = FakeClient
    try:
        with pytest.raises(MistralError, match="401"):
            client.chat_completion([{"role": "user", "content": "hi"}])
    finally:
        mistral_module.httpx.Client = original


def test_post_short_circuits_when_credit_already_marked_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the circuit breaker is set, _post must fail fast with NO HTTP call at all — this is the actual fix for the 17-hour hourly re-hammering of a dead key (2026-07-23/24): every later call in the outage window skips straight to MistralCreditError instead of repeating the request."""
    monkeypatch.setattr("app.modules.ai.mistral_client.is_credit_exhausted", lambda: True)

    class _ExplodingClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("must not make an HTTP client when already exhausted")

    import app.modules.ai.mistral_client as mistral_module

    original = httpx.Client
    mistral_module.httpx.Client = _ExplodingClient
    try:
        client = MistralClient(api_key="test-key")
        with pytest.raises(MistralCreditError):
            client.chat_completion([{"role": "user", "content": "hi"}])
    finally:
        mistral_module.httpx.Client = original


def test_post_marks_exhausted_on_401() -> None:
    """A real 401 must flip the circuit breaker so subsequent calls (this process or any other worker sharing Redis) fail fast instead of also paying for their own doomed round trip."""
    marked: list[bool] = []

    class FakeResponse:
        status_code = 401
        text = "unauthorized"

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, _url: str, headers: dict | None = None, json: dict | None = None) -> Any:  # noqa: ARG002, ANN401 -- name must match the real callee's keyword arg
            return FakeResponse()

    import app.modules.ai.mistral_client as mistral_module

    original_httpx = httpx.Client
    original_mark = mistral_module.mark_credit_exhausted
    mistral_module.httpx.Client = FakeClient
    mistral_module.mark_credit_exhausted = lambda: marked.append(True)
    try:
        client = MistralClient(api_key="test-key")
        with pytest.raises(MistralCreditError):
            client.chat_completion([{"role": "user", "content": "hi"}])
    finally:
        mistral_module.httpx.Client = original_httpx
        mistral_module.mark_credit_exhausted = original_mark

    assert marked == [True]


def test_exhausted_tool_loop_skips_final_completion_when_told(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Research/gap-fill callers run chat_with_tools for its tool side-effects (the trace) and discard the return value. Confirmed 2026-07-14: a gap-fill pass ran out of rounds and the exhaustion fallback paid for a full 'write the final JSON article' completion nobody read. With finalize_on_exhaustion=False the loop returns without that extra call."""
    client = MistralClient(api_key="k", model="m")
    calls = {"n": 0}

    def _fake_post(_payload: dict) -> dict:
        calls["n"] += 1
        # Always demand another tool round so the loop exhausts max_rounds.
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": f"c{calls['n']}",
                                "function": {
                                    "name": "probe",
                                    "arguments": json.dumps({"i": calls["n"]}),
                                },
                            }
                        ],
                    }
                }
            ]
        }

    monkeypatch.setattr(client, "_post", _fake_post)
    finalized = {"n": 0}
    monkeypatch.setattr(
        client,
        "chat_completion",
        lambda *_a, **_k: finalized.__setitem__("n", finalized["n"] + 1) or "{}",
    )

    trace: list[dict] = []
    out = client.chat_with_tools(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        tools=[{"type": "function", "function": {"name": "probe", "parameters": {}}}],
        handlers={"probe": lambda **_kw: {"ok": True}},
        max_rounds=2,
        trace=trace,
        finalize_on_exhaustion=False,
    )
    assert finalized["n"] == 0  # no discarded article completion
    assert len(trace) == 2  # the tool rounds themselves still ran
    assert isinstance(out, str)

from __future__ import annotations

import json

import httpx
import pytest

from app.modules.ai.mistral_client import MistralClient, MistralError


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Retryable-status tests below exercise MistralClient's real backoff
    (MISTRAL_MAX_RETRIES) — without this they genuinely sleep out the full
    schedule (~30s) for zero extra coverage. Retry count/behavior is still
    exercised; only the wall-clock wait is removed."""
    monkeypatch.setattr("time.sleep", lambda _seconds: None)


def test_chat_json_object_parses_response() -> None:
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
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, headers=None, json=None):
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
    """An empty first reply must NOT be echoed back as an assistant message —
    Mistral 400s on assistant messages with neither content nor tool_calls."""
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
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, headers=None, json=None):
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
    class FakeResponse:
        status_code = 401
        text = "unauthorized"

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, headers=None, json=None):
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

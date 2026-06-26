from __future__ import annotations

import json

import httpx
import pytest

from app.modules.ai.mistral_client import MistralClient, MistralError


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

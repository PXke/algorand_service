"""Live model metadata (context length, reasoning_effort support) from
Mistral's own GET /v1/models, root-caused 2026-07-15: a hardcoded comment
("mistral-small ~128k") went stale when Mistral silently upgraded the
"-latest" aliases to 262144 without changing the model name, and every
Large-tier request was paying for two API calls (send reasoning_effort, get
rejected, retry without it) because nothing checked the model's actual
advertised capabilities.

conftest.py's autouse _no_live_mistral_model_metadata fixture blanks
_fetch_model_metadata to {} for every other test in the suite — these tests
import the REAL function before that patch applies, so they can still
exercise its actual behavior directly.
"""

from __future__ import annotations

import httpx
import pytest

import app.modules.ai.mistral_client as mc
from app.modules.ai.mistral_client import (
    MistralClient,
)
from app.modules.ai.mistral_client import (
    _fetch_model_metadata as _real_fetch_model_metadata,
)


class _FakeModelsResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeModelsClient:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def get(self, url, headers=None):
        assert "/models" in url
        return _FakeModelsResponse(self._payload)


@pytest.fixture(autouse=True)
def _clear_cache():
    mc._model_metadata_cache.clear()
    yield
    mc._model_metadata_cache.clear()


def test_fetch_model_metadata_extracts_context_length_and_reasoning(monkeypatch) -> None:
    payload = {
        "data": [
            {
                "id": "mistral-large-latest",
                "max_context_length": 262144,
                "capabilities": {"reasoning": False},
            },
            {
                "id": "mistral-small-latest",
                "max_context_length": 262144,
                "capabilities": {"reasoning": True},
            },
        ]
    }
    monkeypatch.setattr(mc.httpx, "Client", lambda **kw: _FakeModelsClient(payload))

    large = _real_fetch_model_metadata(
        api_base="https://api.mistral.ai/v1", api_key="k", model="mistral-large-latest"
    )
    small = _real_fetch_model_metadata(
        api_base="https://api.mistral.ai/v1", api_key="k", model="mistral-small-latest"
    )

    assert large == {"max_context_length": 262144, "reasoning": False}
    assert small == {"max_context_length": 262144, "reasoning": True}


def test_fetch_model_metadata_caches_per_model(monkeypatch) -> None:
    calls = []

    class _CountingClient(_FakeModelsClient):
        def get(self, url, headers=None):
            calls.append(url)
            return super().get(url, headers=headers)

    payload = {"data": [{"id": "mistral-large-latest", "max_context_length": 262144,
                          "capabilities": {"reasoning": False}}]}
    monkeypatch.setattr(mc.httpx, "Client", lambda **kw: _CountingClient(payload))

    for _ in range(3):
        _real_fetch_model_metadata(
            api_base="https://api.mistral.ai/v1", api_key="k", model="mistral-large-latest"
        )

    assert len(calls) == 1  # cached after the first fetch


def test_fetch_model_metadata_returns_empty_on_network_failure(monkeypatch) -> None:
    def _boom(**kw):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(mc.httpx, "Client", _boom)

    result = _real_fetch_model_metadata(
        api_base="https://api.mistral.ai/v1", api_key="k", model="mistral-large-latest"
    )

    assert result == {}


def test_fetch_model_metadata_failure_is_not_cached(monkeypatch) -> None:
    """A transient failure must not poison the cache forever — the next
    MistralClient constructed (e.g. after the network recovers) should get a
    real answer, not a permanently-cached {}."""
    attempt = {"n": 0}

    def _flaky(**kw):
        attempt["n"] += 1
        if attempt["n"] == 1:
            raise httpx.ConnectError("no network")
        payload = {"data": [{"id": "mistral-large-latest", "max_context_length": 262144,
                              "capabilities": {"reasoning": False}}]}
        return _FakeModelsClient(payload)

    monkeypatch.setattr(mc.httpx, "Client", _flaky)

    first = _real_fetch_model_metadata(
        api_base="https://api.mistral.ai/v1", api_key="k", model="mistral-large-latest"
    )
    second = _real_fetch_model_metadata(
        api_base="https://api.mistral.ai/v1", api_key="k", model="mistral-large-latest"
    )

    assert first == {}
    assert second == {"max_context_length": 262144, "reasoning": False}


def test_client_seeds_reasoning_unsupported_from_live_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        mc,
        "_fetch_model_metadata",
        lambda **kw: {"max_context_length": 262144, "reasoning": False},
    )

    client = MistralClient(api_key="test-key", model="mistral-large-latest")

    assert client._reasoning_effort_unsupported is True


def test_client_keeps_reasoning_supported_when_metadata_says_so(monkeypatch) -> None:
    monkeypatch.setattr(
        mc,
        "_fetch_model_metadata",
        lambda **kw: {"max_context_length": 262144, "reasoning": True},
    )

    client = MistralClient(api_key="test-key", model="mistral-small-latest")

    assert client._reasoning_effort_unsupported is False


def test_client_defaults_to_reasoning_supported_when_metadata_fetch_fails(monkeypatch) -> None:
    # conftest's autouse fixture already does this ({}), but assert the
    # actual fallback semantics explicitly rather than relying on it.
    monkeypatch.setattr(mc, "_fetch_model_metadata", lambda **kw: {})

    client = MistralClient(api_key="test-key", model="mistral-large-latest")

    assert client._reasoning_effort_unsupported is False  # unknown -> assume supported

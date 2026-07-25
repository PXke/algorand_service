"""Ingest-signal auth and the push-signal write path."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.modules.ingest.api.routes import _check_ingest_auth
from app.modules.ingest.queue import QUEUE_KEY, push_signal


def _req(headers: dict[str, str]) -> SimpleNamespace:
    lower = {k.lower(): v for k, v in headers.items()}
    return SimpleNamespace(headers=SimpleNamespace(get=lambda k, d=None: lower.get(k.lower(), d)))


def test_ingest_auth_disabled_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns 503 when no INGEST_API_KEY is configured on the API."""
    monkeypatch.setattr(settings, "ingest_api_key", "")
    denied = _check_ingest_auth(_req({"X-Ingest-Key": "anything"}))
    assert denied is not None
    assert denied.status_code == 503


def test_ingest_auth_rejects_wrong_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rejects a key that differs by a single character."""
    monkeypatch.setattr(settings, "ingest_api_key", "s3cret-key")
    denied = _check_ingest_auth(_req({"X-Ingest-Key": "s3cret-kez"}))  # same length, 1 char off
    assert denied is not None
    assert denied.status_code == 401


def test_ingest_auth_accepts_correct_key_via_header_and_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepts the correct key via both the X-Ingest-Key header and a Bearer token."""
    monkeypatch.setattr(settings, "ingest_api_key", "s3cret-key")
    assert _check_ingest_auth(_req({"X-Ingest-Key": "s3cret-key"})) is None
    assert _check_ingest_auth(_req({"Authorization": "Bearer s3cret-key"})) is None


def test_ingest_auth_rejects_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rejects a request with neither the header nor bearer key set."""
    monkeypatch.setattr(settings, "ingest_api_key", "s3cret-key")
    denied = _check_ingest_auth(_req({}))
    assert denied is not None
    assert denied.status_code == 401


def test_push_signal_writes_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pushes the signal payload onto the ingest queue key and returns the new depth."""
    stored: list[str] = []

    class FakeRedis:
        def lpush(self, key: str, value: str) -> int:
            stored.append(value)
            assert key == QUEUE_KEY
            return 1

    monkeypatch.setattr(
        "app.modules.ingest.queue.redis.from_url",
        lambda *_a, **_k: FakeRedis(),
    )
    depth = push_signal({"service_id": "test", "page_text": "hello"})
    assert depth == 1
    payload = json.loads(stored[0])
    assert payload["service_id"] == "test"


def test_ingest_requires_api_key() -> None:
    """Skips unless the test environment has INGEST_API_KEY set (guard placeholder)."""
    if not settings.ingest_api_key:
        pytest.skip("INGEST_API_KEY not set in test env")

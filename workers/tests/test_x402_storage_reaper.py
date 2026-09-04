"""x402 storage reaper beat: token-gated, POSTs the API-host internal route."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.celery_app as celery_app
from app.core import config
from app.core import redis_lock as redis_lock_module
from app.modules.x402_storage_reaper.tasks import reaper_tasks


def test_beat_entry_absent_when_token_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty reaper token leaves the beat unregistered."""
    monkeypatch.setattr(config, "X402_STORAGE_REAPER_TOKEN", "")
    assert "x402-storage-reap-expired" not in celery_app._build_beat_schedule()


def test_beat_entry_present_with_expires_when_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured token registers the hourly beat with matching expires."""
    monkeypatch.setattr(config, "X402_STORAGE_REAPER_TOKEN", "secret")
    monkeypatch.setattr(config, "X402_STORAGE_REAPER_INTERVAL_SECONDS", 3600)
    entry = celery_app._build_beat_schedule()["x402-storage-reap-expired"]
    assert entry["task"] == "app.tasks.x402_storage_reaper.reap_expired_backups"
    assert entry["schedule"] == 3600.0
    assert entry["options"]["expires"] == 3600.0


def test_task_skips_when_token_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A disabled reaper task returns skipped without POSTing."""
    monkeypatch.setattr(reaper_tasks, "X402_STORAGE_REAPER_TOKEN", "")

    def _must_not_post(*_a: object, **_kw: object) -> None:
        raise AssertionError("must not POST when the token is empty")

    monkeypatch.setattr(reaper_tasks, "get_http_client", _must_not_post)
    acquired: list[tuple[str, int]] = []

    class _Redis:
        def set(self, key: str, _v: str, *, nx: bool, ex: int) -> bool:
            _ = nx
            acquired.append((key, ex))
            return True

        def eval(self, *_a: object) -> int:
            return 1

    monkeypatch.setattr(redis_lock_module, "_client", lambda: _Redis())
    assert reaper_tasks.reap_expired_backups() == {
        "status": "skipped",
        "reason": "x402_storage_reaper_token_empty",
    }
    assert acquired
    assert acquired[0][0] == "lock:x402_storage:reap"


def test_task_posts_token_and_returns_api_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """The beat POSTs the configured URL with the reaper token and returns the API JSON."""
    monkeypatch.setattr(reaper_tasks, "X402_STORAGE_REAPER_TOKEN", "secret")
    monkeypatch.setattr(
        reaper_tasks,
        "X402_STORAGE_REAPER_URL",
        "http://127.0.0.1:8080/api/v1/internal/x402/storage/reap",
    )
    seen: dict[str, object] = {}

    class _Client:
        def post(self, url: str, headers: dict[str, str]) -> object:
            seen["url"] = url
            seen["headers"] = headers
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"status": "ok", "reaped": 2},
                text="",
            )

    monkeypatch.setattr(reaper_tasks, "get_http_client", lambda **_kw: _Client())

    class _Redis:
        def set(self, *_a: object, **_kw: object) -> bool:
            return True

        def eval(self, *_a: object) -> int:
            return 1

    monkeypatch.setattr(redis_lock_module, "_client", lambda: _Redis())
    assert reaper_tasks.reap_expired_backups() == {"status": "ok", "reaped": 2}
    assert seen["url"] == "http://127.0.0.1:8080/api/v1/internal/x402/storage/reap"
    assert seen["headers"]["X-Storage-Reaper-Token"] == "secret"

"""Global compose mutex — only one writer research loop at a time."""

from datetime import UTC, datetime, timedelta
from typing import Never
from unittest.mock import MagicMock

import pytest

from app.modules.newspaper.compose_lock import (
    ComposeBusyError,
    _holder_is_dead,
    _try_reclaim,
    compose_lock,
)


def test_compose_lock_raises_when_held(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raises ComposeBusyError when the global compose lock cannot be acquired."""
    monkeypatch.setattr(
        "app.modules.newspaper.compose_lock.acquire",
        lambda _key, _ttl: None,
    )
    with pytest.raises(ComposeBusyError), compose_lock():
        pass  # pragma: no cover


def test_compose_lock_releases_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Releases the acquired lock with its token when the compose_lock context exits normally."""
    released: list[str] = []
    monkeypatch.setattr(
        "app.modules.newspaper.compose_lock.acquire",
        lambda _key, _ttl: "tok123",
    )
    monkeypatch.setattr(
        "app.modules.newspaper.compose_lock.release",
        lambda key, token: released.append(f"{key}:{token}"),
    )
    with compose_lock():
        pass
    assert released == ["compose:article:tok123"]


def test_compose_via_writer_tools_waits_on_global_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Composes normally when the lock is free, then raises ComposeBusyError when it's already held."""
    from app.modules.ai import mistral_compose as mc

    held = {"busy": False}

    def _fake_acquire(_key: str, _ttl: int) -> str | None:
        if held["busy"]:
            return None
        held["busy"] = True
        return "tok"

    def _fake_release(_key: str, _token: str) -> None:
        held["busy"] = False

    monkeypatch.setattr("app.modules.newspaper.compose_lock.acquire", _fake_acquire)
    monkeypatch.setattr("app.modules.newspaper.compose_lock.release", _fake_release)
    monkeypatch.setattr("app.core.config.WRITER_TOOLS_ENABLED", False, raising=False)

    class _Client:
        def chat_json_object(self, *_a: object, **_kw: object) -> dict:
            return {"title": "T", "summary": "S", "body": "B"}

    fields = mc._compose_via_writer_tools(
        system="sys",
        user="usr",
        source_url="https://example.com/",
        mistral=_Client(),
    )
    assert fields.title == "T"

    monkeypatch.setattr(
        "app.modules.newspaper.compose_lock.acquire",
        lambda _key, _ttl: None,
    )
    with pytest.raises(ComposeBusyError):
        mc._compose_via_writer_tools(
            system="sys",
            user="usr",
            source_url="https://example.com/",
            mistral=_Client(),
        )


# ── Dead-lock detection (2026-07-13: a worker restart mid-compose, e.g. a ──
# deploy, used to orphan this lock for its full ~31min TTL with nothing
# checking whether the holder was still alive) ──────────────────────────


def _fresh_meta(**overrides: object) -> dict:
    meta = {
        "task_id": "task-123",
        "started_at": (datetime.now(tz=UTC) - timedelta(seconds=300)).isoformat(),
    }
    meta.update(overrides)
    return meta


def test_holder_is_dead_false_without_task_id() -> None:
    """Never treats a lock as dead when its metadata carries no task_id to check."""
    assert not _holder_is_dead({"started_at": datetime.now(tz=UTC).isoformat()})


def test_holder_is_dead_false_within_min_age(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never reclaims a very fresh lock even when inspect() would otherwise say the holder is gone."""
    # Even if inspect() would say it's gone, a very fresh lock is never
    # reclaimed — the task hasn't had a chance to run yet.
    monkeypatch.setattr(
        "app.celery_app.celery_app.control.inspect",
        lambda _timeout=5: MagicMock(active=lambda: {}),
    )
    meta = _fresh_meta(started_at=datetime.now(tz=UTC).isoformat())
    assert not _holder_is_dead(meta)


def test_holder_is_dead_false_when_inspect_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # Uncertainty must never be treated as "dead" — that's how you'd end up
    # with two writer loops running at once, which this lock exists to
    # prevent.
    """Never treats a lock as dead when the control-plane inspect() call itself fails."""

    def _boom(_timeout: int = 5) -> Never:
        raise TimeoutError("broker unreachable")

    monkeypatch.setattr("app.celery_app.celery_app.control.inspect", _boom)
    assert not _holder_is_dead(_fresh_meta())


def test_holder_is_dead_false_when_task_still_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never treats a lock as dead while its owning task is still reported active."""
    monkeypatch.setattr(
        "app.celery_app.celery_app.control.inspect",
        lambda _timeout=5: MagicMock(active=lambda: {"worker@host": [{"id": "task-123"}]}),
    )
    assert not _holder_is_dead(_fresh_meta())


def test_holder_is_dead_true_when_inspect_confirms_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treats a lock as dead once inspect() confirms no worker is running its task."""
    monkeypatch.setattr(
        "app.celery_app.celery_app.control.inspect",
        lambda timeout=5: MagicMock(active=lambda: {"worker@host": []}),  # noqa: ARG005 -- name must match the real callee's keyword arg
    )
    assert _holder_is_dead(_fresh_meta())


def test_try_reclaim_clears_lock_only_when_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clears both the lock and its metadata key only when the holder is confirmed dead."""
    deleted: list[str] = []
    monkeypatch.setattr(
        "app.modules.newspaper.compose_lock.get_compose_lock_status",
        lambda: _fresh_meta(),
    )
    monkeypatch.setattr("app.modules.newspaper.compose_lock._holder_is_dead", lambda _meta: True)
    fake_client = MagicMock()
    fake_client.delete.side_effect = lambda k: deleted.append(k)
    monkeypatch.setattr("app.modules.newspaper.compose_lock._redis_client", lambda: fake_client)
    assert _try_reclaim() is True
    assert "lock:compose:article" in deleted
    assert "lock:compose:article:meta" in deleted


def test_try_reclaim_leaves_lock_alone_when_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leaves the lock and its metadata untouched when the holder is confirmed alive."""
    monkeypatch.setattr(
        "app.modules.newspaper.compose_lock.get_compose_lock_status",
        lambda: _fresh_meta(),
    )
    monkeypatch.setattr("app.modules.newspaper.compose_lock._holder_is_dead", lambda _meta: False)
    fake_client = MagicMock()
    monkeypatch.setattr("app.modules.newspaper.compose_lock._redis_client", lambda: fake_client)
    assert _try_reclaim() is False
    fake_client.delete.assert_not_called()


def test_compose_lock_reclaims_dead_lock_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # First acquire() call fails (someone else holds it); reclaim succeeds;
    # second acquire() call succeeds — the caller never sees ComposeBusyError.
    """Reclaims a dead lock transparently and retries acquire, so the caller never sees ComposeBusyError."""
    calls = {"n": 0}

    def _fake_acquire(_key: str, _ttl: int) -> str | None:
        calls["n"] += 1
        return None if calls["n"] == 1 else "tok-reclaimed"

    monkeypatch.setattr("app.modules.newspaper.compose_lock.acquire", _fake_acquire)
    monkeypatch.setattr("app.modules.newspaper.compose_lock.release", lambda _k, _t: None)
    monkeypatch.setattr("app.modules.newspaper.compose_lock._try_reclaim", lambda: True)
    monkeypatch.setattr("app.modules.newspaper.compose_lock._write_meta", lambda *_a: None)

    with compose_lock(label="https://example.com/"):
        pass
    assert calls["n"] == 2


def test_compose_lock_still_raises_when_reclaim_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Still raises ComposeBusyError, carrying the holder's label, when reclaim fails."""
    monkeypatch.setattr("app.modules.newspaper.compose_lock.acquire", lambda _key, _ttl: None)
    monkeypatch.setattr("app.modules.newspaper.compose_lock._try_reclaim", lambda: False)
    monkeypatch.setattr(
        "app.modules.newspaper.compose_lock.get_compose_lock_status",
        lambda: _fresh_meta(label="https://slow-site.example/"),
    )
    with pytest.raises(ComposeBusyError) as exc_info, compose_lock():
        pass  # pragma: no cover
    assert exc_info.value.status["label"] == "https://slow-site.example/"


def test_get_compose_lock_status_none_when_not_held(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns None when the lock key does not exist in Redis."""
    fake_client = MagicMock()
    fake_client.ttl.return_value = -2  # key doesn't exist
    monkeypatch.setattr("app.modules.newspaper.compose_lock._redis_client", lambda: fake_client)
    from app.modules.newspaper.compose_lock import get_compose_lock_status

    assert get_compose_lock_status() is None

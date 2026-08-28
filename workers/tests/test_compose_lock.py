"""Global compose mutex — only one writer research loop at a time."""

import os
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
    from app.modules.ai import llm_compose as mc

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
        llm=_Client(),
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
            llm=_Client(),
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


def test_holder_is_dead_false_without_task_id_or_pid_even_when_old() -> None:
    """No task_id AND no pid recorded at all -- nothing to check, never reclaimed, regardless of age."""
    assert not _holder_is_dead(
        {"started_at": (datetime.now(tz=UTC) - timedelta(seconds=999)).isoformat()}
    )


def test_holder_is_dead_true_for_bare_invocation_with_a_dead_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No task_id (a bare/manual script, not a real Celery task) but a recorded pid that's confirmed gone -- reclaimable via the same-host PID-liveness fallback."""
    monkeypatch.setattr(
        "app.modules.newspaper.compose_lock._pid_is_dead", lambda _pid, _start: True
    )
    meta = {
        "started_at": (datetime.now(tz=UTC) - timedelta(seconds=300)).isoformat(),
        "pid": 999999,
        "pid_start_ticks": 12345,
    }
    assert _holder_is_dead(meta)


def test_holder_is_dead_false_for_bare_invocation_with_a_live_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recorded pid that's still confirmed alive is never reclaimed."""
    monkeypatch.setattr(
        "app.modules.newspaper.compose_lock._pid_is_dead", lambda _pid, _start: False
    )
    meta = {
        "started_at": (datetime.now(tz=UTC) - timedelta(seconds=300)).isoformat(),
        "pid": 999999,
        "pid_start_ticks": 12345,
    }
    assert not _holder_is_dead(meta)


def test_pid_is_dead_true_when_process_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    """No process at all currently at that pid -- confirmed dead."""
    from app.modules.newspaper.compose_lock import _pid_is_dead

    monkeypatch.setattr(
        "app.modules.newspaper.compose_lock._proc_start_ticks", lambda _pid: None
    )
    assert _pid_is_dead(999999, 12345)


def test_pid_is_dead_true_when_pid_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process IS running at that pid, but its start time doesn't match what was recorded -- the pid was reused by something else, so the original holder is confirmed dead."""
    from app.modules.newspaper.compose_lock import _pid_is_dead

    monkeypatch.setattr(
        "app.modules.newspaper.compose_lock._proc_start_ticks", lambda _pid: 99999
    )
    assert _pid_is_dead(999999, 12345)


def test_pid_is_dead_false_when_start_time_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same process (matching start time) is still running -- never reclaimed."""
    from app.modules.newspaper.compose_lock import _pid_is_dead

    monkeypatch.setattr(
        "app.modules.newspaper.compose_lock._proc_start_ticks", lambda _pid: 12345
    )
    assert not _pid_is_dead(999999, 12345)


def test_pid_is_dead_false_when_no_start_time_was_ever_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process is running at that pid, but there's nothing to compare its start time against -- can't rule out it's the same process, so never reclaimed (uncertainty defaults to alive)."""
    from app.modules.newspaper.compose_lock import _pid_is_dead

    monkeypatch.setattr(
        "app.modules.newspaper.compose_lock._proc_start_ticks", lambda _pid: 12345
    )
    assert not _pid_is_dead(999999, None)


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


def test_proc_start_ticks_reads_a_real_stable_value_for_this_process() -> None:
    """Against the actual running test process (no mocking) -- a real /proc/<pid>/stat parse, called twice, must return the same value both times (a process's own start time never changes)."""
    import os

    from app.modules.newspaper.compose_lock import _proc_start_ticks

    first = _proc_start_ticks(os.getpid())
    second = _proc_start_ticks(os.getpid())
    assert first is not None
    assert first == second


def test_proc_start_ticks_none_for_a_pid_that_cannot_exist() -> None:
    """An absurdly large pid -- /proc/<pid>/stat can't exist -- returns None rather than raising."""
    from app.modules.newspaper.compose_lock import _proc_start_ticks

    assert _proc_start_ticks(2**30) is None


def test_write_meta_records_pid_and_start_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    """_write_meta captures this process's own pid/start-time unconditionally, not just for bare (empty-task_id) callers."""
    from app.modules.newspaper.compose_lock import _write_meta

    captured: dict = {}
    fake_client = MagicMock()
    fake_client.set.side_effect = lambda _key, value, ex=None: captured.update(  # noqa: ARG005
        __import__("json").loads(value)
    )
    monkeypatch.setattr("app.modules.newspaper.compose_lock._redis_client", lambda: fake_client)

    _write_meta("https://example.com/", "task-123")

    assert captured["task_id"] == "task-123"
    assert captured["pid"] == os.getpid()
    assert captured["pid_start_ticks"] is not None


def test_get_compose_lock_status_none_when_not_held(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns None when the lock key does not exist in Redis."""
    fake_client = MagicMock()
    fake_client.ttl.return_value = -2  # key doesn't exist
    monkeypatch.setattr("app.modules.newspaper.compose_lock._redis_client", lambda: fake_client)
    from app.modules.newspaper.compose_lock import get_compose_lock_status

    assert get_compose_lock_status() is None

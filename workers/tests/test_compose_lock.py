"""Global compose mutex — only one writer research loop at a time."""

from unittest.mock import MagicMock

import pytest

from app.modules.newspaper.compose_lock import ComposeBusyError, compose_lock


def test_compose_lock_raises_when_held(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modules.newspaper.compose_lock.acquire",
        lambda key, ttl: None,
    )
    with pytest.raises(ComposeBusyError):
        with compose_lock():
            pass  # pragma: no cover


def test_compose_lock_releases_on_success(monkeypatch) -> None:
    released: list[str] = []
    monkeypatch.setattr(
        "app.modules.newspaper.compose_lock.acquire",
        lambda key, ttl: "tok123",
    )
    monkeypatch.setattr(
        "app.modules.newspaper.compose_lock.release",
        lambda key, token: released.append(f"{key}:{token}"),
    )
    with compose_lock():
        pass
    assert released == ["compose:article:tok123"]


def test_compose_via_writer_tools_waits_on_global_lock(monkeypatch) -> None:
    from app.modules.ai import mistral_compose as mc

    held = {"busy": False}

    def _fake_acquire(key, ttl):
        if held["busy"]:
            return None
        held["busy"] = True
        return "tok"

    def _fake_release(key, token):
        held["busy"] = False

    monkeypatch.setattr("app.modules.newspaper.compose_lock.acquire", _fake_acquire)
    monkeypatch.setattr("app.modules.newspaper.compose_lock.release", _fake_release)
    monkeypatch.setattr("app.core.config.WRITER_TOOLS_ENABLED", False, raising=False)

    class _Client:
        def chat_json_object(self, *_a, **_kw):
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
        lambda key, ttl: None,
    )
    with pytest.raises(ComposeBusyError):
        mc._compose_via_writer_tools(
            system="sys",
            user="usr",
            source_url="https://example.com/",
            mistral=_Client(),
        )

"""Wallet-auth nonce pop is a single Redis GETDEL, not get-then-delete."""

from __future__ import annotations

import pytest

from app.modules.auth.services.session_store import SessionStore


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.getdel_calls: list[str] = []

    def setex(self, key: str, time: int, value: str) -> bool:
        _ = time
        self.store[key] = value
        return True

    def getdel(self, key: str) -> str | None:
        self.getdel_calls.append(key)
        return self.store.pop(key, None)

    def get(self, _key: str) -> str | None:
        raise AssertionError("pop_nonce_challenge must not GET then DELETE")

    def delete(self, _key: str) -> int:
        raise AssertionError("pop_nonce_challenge must not GET then DELETE")


def test_pop_nonce_challenge_uses_atomic_getdel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two parallel verifies cannot both read the same nonce."""
    fake = _FakeRedis()
    monkeypatch.setattr(
        "app.modules.auth.services.session_store.redis.from_url", lambda *_a, **_k: fake
    )
    store = SessionStore()
    store.set_nonce_challenge("ADDR", "n", '{"nonce":"n"}')
    assert store.pop_nonce_challenge("ADDR", "n") == '{"nonce":"n"}'
    assert store.pop_nonce_challenge("ADDR", "n") is None
    assert fake.getdel_calls == ["auth:nonce:ADDR:n", "auth:nonce:ADDR:n"]
    assert fake.store == {}


def test_pop_nonce_challenge_wrong_nonce_does_not_touch_real_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A garbage/guessed nonce for a known wallet must miss entirely, leaving the real pending challenge intact (2026-09-07 security review, finding 2)."""
    fake = _FakeRedis()
    monkeypatch.setattr(
        "app.modules.auth.services.session_store.redis.from_url", lambda *_a, **_k: fake
    )
    store = SessionStore()
    store.set_nonce_challenge("ADDR", "real-nonce", '{"nonce":"real-nonce"}')
    assert store.pop_nonce_challenge("ADDR", "guessed-garbage") is None
    assert store.pop_nonce_challenge("ADDR", "real-nonce") == '{"nonce":"real-nonce"}'

"""Regression: research_tools.py / investigative_tools.py raw os.getenv() calls.

They used to read several API-key/credential env vars via a raw os.getenv()
call with the default literal duplicated at each call site (CLAUDE.md
section 3 -- config has one owner, never a raw os.getenv in a task module;
BLUESKY_IDENTIFIER/BLUESKY_APP_PASSWORD were an even sharper case: config.py
already owned a constant for them and research_tools.py kept its own
separate os.getenv() copy of the exact same env var right next to it).

These now read `config.<NAME>` as the single owned default while still
honoring a live env override at call time (env_str("<NAME>", config.<NAME>)),
matching the established convention in app/celery_app.py and
test_beat_schedule.py's own "not a stale duplicate" regression tests.
"""

from __future__ import annotations

import pytest

from app.core import config
from app.modules.ai import investigative_tools, research_tools


def test_bsky_access_token_uses_config_owned_default_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no BLUESKY_IDENTIFIER/PASSWORD env vars set, config.py's own constants are the default -- patching them (not the env) must change whether the token mint is even attempted."""
    monkeypatch.delenv("BLUESKY_IDENTIFIER", raising=False)
    monkeypatch.delenv("BLUESKY_APP_PASSWORD", raising=False)
    monkeypatch.setattr(config, "BLUESKY_IDENTIFIER", "")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "")
    assert research_tools._bsky_access_token() == ("", "")

    calls: list[dict] = []

    def _fake_post(_url: str, *, json: dict, **_kwargs: object) -> object:
        calls.append(json)
        raise RuntimeError("network disabled in test")

    monkeypatch.setattr("httpx.post", _fake_post)
    monkeypatch.setattr(config, "BLUESKY_IDENTIFIER", "someone.bsky.social")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "app-password")
    research_tools._bsky_token_cache.clear()
    token, err = research_tools._bsky_access_token()
    assert token == ""
    assert "network disabled" in err
    assert calls
    assert calls[0] == {"identifier": "someone.bsky.social", "password": "app-password"}


def test_bsky_access_token_env_override_still_wins_over_config_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit BLUESKY_IDENTIFIER/PASSWORD env var still overrides config.py's default."""
    monkeypatch.setattr(config, "BLUESKY_IDENTIFIER", "config-value")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "config-pw")
    monkeypatch.setenv("BLUESKY_IDENTIFIER", "env-value.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "env-pw")

    calls: list[dict] = []

    def _fake_post(_url: str, *, json: dict, **_kwargs: object) -> object:
        calls.append(json)
        raise RuntimeError("network disabled in test")

    monkeypatch.setattr("httpx.post", _fake_post)
    research_tools._bsky_token_cache.clear()
    research_tools._bsky_access_token()
    assert calls
    assert calls[0] == {"identifier": "env-value.bsky.social", "password": "env-pw"}


def test_github_get_uses_config_owned_token_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """GITHUB_TOKEN's default now lives in config.py -- with the env var unset, patching config.GITHUB_TOKEN must change the Authorization header _github_get sends."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "ghp_from_config")

    captured: dict = {}

    def _fake_guarded_get(_url: str, **kwargs: object) -> object:
        captured.update(kwargs)

        class _Resp:
            status_code = 200

        return _Resp()

    monkeypatch.setattr(research_tools, "_guarded_get", _fake_guarded_get)
    research_tools._github_get("https://api.github.com/repos/foo/bar")
    assert captured["headers"]["Authorization"] == "Bearer ghp_from_config"


def test_screen_sanctions_and_pep_uses_config_owned_key_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPENSANCTIONS_API_KEY's default now lives in config.py -- with the env var unset, patching config.OPENSANCTIONS_API_KEY must change the Authorization header screen_sanctions_and_pep sends."""
    monkeypatch.delenv("OPENSANCTIONS_API_KEY", raising=False)
    monkeypatch.setattr(config, "OPENSANCTIONS_API_KEY", "os-key-from-config")

    captured: dict = {}

    def _fake_get(_url: str, *, headers: dict | None = None, **_kwargs: object) -> dict:
        captured["headers"] = headers
        return {"results": []}

    monkeypatch.setattr(investigative_tools, "_get", _fake_get)
    investigative_tools.screen_sanctions_and_pep("Jane Doe")
    assert captured["headers"]["Authorization"] == "ApiKey os-key-from-config"

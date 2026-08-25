"""The weekly X-search sweep: bounded, one call per tracked service, stored via injected callables (mirrors test_llm_diff_tasks.py's dependency-injection shape)."""

from __future__ import annotations

import pytest

from app.modules.chain_tail.registry_cache import ServiceEntry
from app.modules.newspaper import x_search_sweep


def _entry(service_id: str, display_name: str) -> ServiceEntry:
    return ServiceEntry(
        service_id=service_id,
        display_name=display_name,
        match_kind="address",
        match_value="X",
        scrape_url="https://example.com",
        enabled=True,
    )


def test_skips_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """X_SEARCH_ENABLED off skips the whole sweep before ever loading the service registry."""
    import app.core.config as config

    monkeypatch.setattr(config, "X_SEARCH_ENABLED", False)
    monkeypatch.setattr(config, "X_BEARER_TOKEN", "")

    def fail_load() -> tuple[ServiceEntry, ...]:
        raise AssertionError("should not have loaded the service registry")

    result = x_search_sweep.run_x_search_weekly_sweep(load_services=fail_load)
    assert result["status"] == "skipped"
    assert result["swept"] == 0


def test_skips_without_bearer_token_even_if_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """X_SEARCH_ENABLED alone isn't enough -- the sweep also needs a bearer token to call X with."""
    import app.core.config as config

    monkeypatch.setattr(config, "X_SEARCH_ENABLED", True)
    monkeypatch.setattr(config, "X_BEARER_TOKEN", "")

    result = x_search_sweep.run_x_search_weekly_sweep(
        load_services=lambda: (_entry("a", "A"),)
    )
    assert result["status"] == "skipped"


def test_sweeps_every_tracked_service_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """One search call and one store call per enabled service, keyed on its display_name."""
    import app.core.config as config

    monkeypatch.setattr(config, "X_SEARCH_ENABLED", True)
    monkeypatch.setattr(config, "X_BEARER_TOKEN", "test-token")
    monkeypatch.setattr(config, "X_SEARCH_WEEKLY_SWEEP_MAX_SERVICES", 200)

    entries = (
        _entry("folks-finance", "Folks Finance"),
        _entry("tinyman", "Tinyman"),
    )
    searched: list[str] = []
    stored: list[dict] = []
    cache_cleared = {"called": False}

    def fake_search(query: str) -> dict:
        searched.append(query)
        return {"posts": [{"text": f"post about {query}", "likes": 1, "reposts": 0, "replies": 0}]}

    def fake_store(**kwargs: object) -> None:
        stored.append(kwargs)

    result = x_search_sweep.run_x_search_weekly_sweep(
        load_services=lambda: entries,
        clear_cache=lambda: cache_cleared.__setitem__("called", True),
        search=fake_search,
        store=fake_store,
    )

    assert cache_cleared["called"] is True
    assert searched == ["Folks Finance", "Tinyman"]
    assert result == {"status": "ok", "swept": 2, "errors": 0, "tracked": 2, "truncated": False}
    assert stored[0]["service_id"] == "folks-finance"
    assert stored[0]["query"] == "Folks Finance"
    assert len(stored[0]["posts"]) == 1
    assert stored[0]["error"] == ""


def test_one_service_erroring_does_not_abort_the_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """One bad service's search call must not stop the rest of the sweep from running."""
    import app.core.config as config

    monkeypatch.setattr(config, "X_SEARCH_ENABLED", True)
    monkeypatch.setattr(config, "X_BEARER_TOKEN", "test-token")
    monkeypatch.setattr(config, "X_SEARCH_WEEKLY_SWEEP_MAX_SERVICES", 200)

    entries = (_entry("bad", "Bad Service"), _entry("good", "Good Service"))
    stored: list[dict] = []

    def fake_search(query: str) -> dict:
        if query == "Bad Service":
            raise RuntimeError("boom")
        return {"posts": []}

    result = x_search_sweep.run_x_search_weekly_sweep(
        load_services=lambda: entries,
        clear_cache=lambda: None,
        search=fake_search,
        store=lambda **kw: stored.append(kw),
    )

    assert result["swept"] == 1
    assert result["errors"] == 1
    assert result["tracked"] == 2
    assert len(stored) == 2
    assert stored[0]["service_id"] == "bad"
    assert "boom" in stored[0]["error"]
    assert stored[1]["error"] == ""


def test_sweep_is_bounded_by_the_defensive_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """More tracked services than X_SEARCH_WEEKLY_SWEEP_MAX_SERVICES only sweeps the cap's worth, and reports truncated=True."""
    import app.core.config as config

    monkeypatch.setattr(config, "X_SEARCH_ENABLED", True)
    monkeypatch.setattr(config, "X_BEARER_TOKEN", "test-token")
    monkeypatch.setattr(config, "X_SEARCH_WEEKLY_SWEEP_MAX_SERVICES", 1)

    entries = (_entry("a", "A Service"), _entry("b", "B Service"))
    searched: list[str] = []

    result = x_search_sweep.run_x_search_weekly_sweep(
        load_services=lambda: entries,
        clear_cache=lambda: None,
        search=lambda q: (searched.append(q), {"posts": []})[1],
        store=lambda **_kw: None,
    )

    assert searched == ["A Service"]
    assert result["tracked"] == 1
    assert result["truncated"] is True

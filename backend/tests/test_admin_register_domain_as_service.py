"""_register_domain_as_service must no longer trigger the removed fetch_source task.

fetch_source (workers/app/modules/scraper/tasks/scrape_tasks.py) was deleted
2026-08-28: it was fire-and-forget (dispatched via send_task, its return value
never read by any caller) and strictly redundant with run_llm_diff_check's own
~10min beat, which already picks up any never-scraped/never-throttled service
promptly. This is a regression test on the exact backend trigger path (W3-C).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import patch_cassandra

import app.modules.admin.api.routes as routes


class _FakeResult:
    """A Cassandra ResultSet stand-in whose .one() always reports "no row"."""

    def one(self) -> None:
        return None


class _FakeSession:
    """Records every statement executed against it; no real Cassandra."""

    def __init__(self) -> None:
        self.executed: list[tuple[object, tuple]] = []

    def execute(self, stmt: object, params: tuple = ()) -> _FakeResult:
        self.executed.append((stmt, params))
        return _FakeResult()


class _FakeCelery:
    """Stand-in for celery.Celery(broker=...): records every send_task call."""

    def __init__(self, broker: str) -> None:
        pass

    def send_task(self, name: str, *, queue: str, args: list[Any] | None = None) -> None:
        _SENT.append({"name": name, "queue": queue, "args": args})


_SENT: list[dict[str, Any]] = []


@pytest.fixture(autouse=True)
def _reset_sent() -> None:
    _SENT.clear()


def _wire(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_cassandra(monkeypatch)
    monkeypatch.setattr("app.modules.registry.sources.service_for_domain", lambda _domain: "")
    monkeypatch.setattr("app.modules.registry.sources.add_web_source", lambda *_a, **_kw: None)
    monkeypatch.setattr("celery.Celery", _FakeCelery)


def test_register_domain_as_service_no_longer_sends_fetch_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approving a full-site domain only kicks the frontier crawl, never the removed fetch_source task."""
    _wire(monkeypatch)

    routes._register_domain_as_service(
        _FakeSession(),
        "svc.example",
        "https://svc.example",
        enqueued=True,
        now=datetime.now(tz=UTC),
    )

    names = [call["name"] for call in _SENT]
    assert "app.tasks.scrape.fetch_source" not in names
    assert names == ["app.tasks.crawler.drain_url_queue"]


def test_register_domain_as_service_sends_nothing_when_not_enqueued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the domain wasn't freshly enqueued, no crawl (and no fetch_source) fires."""
    _wire(monkeypatch)

    routes._register_domain_as_service(
        _FakeSession(),
        "svc.example",
        "https://svc.example",
        enqueued=False,
        now=datetime.now(tz=UTC),
    )

    assert _SENT == []

"""admin_artifacts_to_compose_selected: the REAL, persisted `to_compose` lineup for a day, as opposed to admin_artifacts_to_compose_preview's live forecast.

Same cross-service dispatch shape as the preview route (backend has no direct
import of the workers codebase): send_task + a short synchronous .get() on
app.tasks.newspaper.list_to_compose_for_day. These tests fake `celery.Celery`
the same way test_admin_artifacts_to_compose_preview.py does, so no real
broker/worker is involved.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.core.http import Request
from app.modules.admin.api import routes as admin_routes


def _req(
    *,
    method: str = "GET",
    query_params: dict[str, str] | None = None,
    path_params: dict[str, str] | None = None,
    body: bytes = b"{}",
) -> Request:
    return Request(
        method=method,
        headers={},
        query_params=query_params or {},  # type: ignore[arg-type]
        path_params=path_params or {},
        body=body,
    )


class _FakeAsyncResult:
    def __init__(self, value: Any) -> None:  # noqa: ANN401
        self._value = value

    def get(self, timeout: float) -> Any:  # noqa: ANN401, ARG002
        return self._value


class _FakeCelery:
    """Captures the dispatched task name/args and returns a canned result."""

    last_name: str | None = None
    last_args: list | None = None
    result: Any = None

    def __init__(self, *, broker: str, backend: str) -> None:
        pass

    def send_task(self, name: str, *, args: list, queue: str) -> _FakeAsyncResult:  # noqa: ARG002
        _FakeCelery.last_name = name
        _FakeCelery.last_args = args
        return _FakeAsyncResult(_FakeCelery.result)


@pytest.fixture(autouse=True)
def _admin_allowed() -> Any:  # noqa: ANN401
    with patch.object(admin_routes, "require_admin_wallet", return_value=None):
        yield


def test_selected_defaults_day_to_tomorrow(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ?day= given -> dispatches for (real today + 1 day), not today itself."""
    from datetime import UTC, datetime, timedelta

    _FakeCelery.result = []
    monkeypatch.setattr("celery.Celery", _FakeCelery)

    resp = admin_routes.admin_artifacts_to_compose_selected(_req())

    expected_day = (datetime.now(tz=UTC).date() + timedelta(days=1)).isoformat()
    assert _FakeCelery.last_name == "app.tasks.newspaper.list_to_compose_for_day"
    assert _FakeCelery.last_args == [expected_day]
    assert resp == {"compose_day": expected_day, "items": []}


def test_selected_uses_the_given_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit ?day= is passed through verbatim as the Celery task arg."""
    _FakeCelery.result = [
        {
            "slot": 0,
            "artifact_id": "abc-123",
            "lane": "human",
            "service_id": "svc-a",
            "picked_at": "2026-08-26T00:05:00",
        }
    ]
    monkeypatch.setattr("celery.Celery", _FakeCelery)

    resp = admin_routes.admin_artifacts_to_compose_selected(
        _req(query_params={"day": "2026-08-26"})
    )

    assert _FakeCelery.last_args == ["2026-08-26"]
    assert resp == {"compose_day": "2026-08-26", "items": _FakeCelery.result}


def test_selected_returns_empty_items_when_nothing_locked_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty `to_compose` table (the daily beat hasn't fired yet) surfaces as items: [], not an error."""
    _FakeCelery.result = []
    monkeypatch.setattr("celery.Celery", _FakeCelery)

    resp = admin_routes.admin_artifacts_to_compose_selected(
        _req(query_params={"day": "2026-08-26"})
    )

    assert resp == {"compose_day": "2026-08-26", "items": []}


def test_selected_400s_on_malformed_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ?day= that isn't a valid YYYY-MM-DD date 400s cleanly instead of dispatching garbage."""
    monkeypatch.setattr("celery.Celery", _FakeCelery)

    resp = admin_routes.admin_artifacts_to_compose_selected(
        _req(query_params={"day": "not-a-date"})
    )

    assert resp.status_code == 400


def test_selected_502s_when_broker_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broker connection failure surfaces as a clean 502, not an unhandled exception."""

    class _BrokenCelery:
        def __init__(self, *, broker: str, backend: str) -> None:  # noqa: ARG002
            raise RuntimeError("no broker")

    monkeypatch.setattr("celery.Celery", _BrokenCelery)

    resp = admin_routes.admin_artifacts_to_compose_selected(_req())

    assert resp.status_code == 502


def test_selected_504s_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker that takes too long to answer surfaces as a clean 504, not a hang."""
    from celery.exceptions import TimeoutError as CeleryTimeoutError

    class _SlowAsyncResult:
        def get(self, timeout: float) -> Any:  # noqa: ANN401, ARG002
            raise CeleryTimeoutError

    class _SlowCelery:
        def __init__(self, *, broker: str, backend: str) -> None:
            pass

        def send_task(self, name: str, *, args: list, queue: str) -> _SlowAsyncResult:  # noqa: ARG002
            return _SlowAsyncResult()

    monkeypatch.setattr("celery.Celery", _SlowCelery)

    resp = admin_routes.admin_artifacts_to_compose_selected(_req())

    assert resp.status_code == 504

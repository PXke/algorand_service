"""admin_reset_to_compose_for_day: the "Redo today's picks" admin action -- clears `day`'s locked-in `to_compose` selection and immediately re-selects.

Same cross-service dispatch shape as the preview/selected routes (backend has
no direct import of the workers codebase): send_task + a short synchronous
.get() on app.tasks.newspaper.reset_and_reselect_to_compose_for_day. These
tests fake `celery.Celery` the same way test_admin_artifacts_to_compose_selected.py
does, so no real broker/worker is involved.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.core.http import Request
from app.modules.admin.api import routes as admin_routes


def _req(
    *,
    method: str = "POST",
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


def _canned_result(day: str) -> dict:
    return {
        "status": "ok",
        "compose_day": day,
        "reset": {
            "status": "ok",
            "compose_day": day,
            "cleared_slots": 2,
            "reverted_to_pending": ["a-1", "a-2"],
            "skipped": [],
            "fully_reverted": True,
        },
        "selection": {
            "status": "ok",
            "compose_day": day,
            "human_picked": False,
            "platform_slots_filled": 2,
            "platform_slots_available": 2,
            "platform_pool_counts": {"new_service": 1, "update": 1},
            "selections": [],
        },
    }


def test_reset_defaults_day_to_tomorrow(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ?day= given -> dispatches for (real today + 1 day), matching the preview/selected routes' own default."""
    from datetime import UTC, datetime, timedelta

    expected_day = (datetime.now(tz=UTC).date() + timedelta(days=1)).isoformat()
    _FakeCelery.result = _canned_result(expected_day)
    monkeypatch.setattr("celery.Celery", _FakeCelery)

    resp = admin_routes.admin_reset_to_compose_for_day(_req())

    assert _FakeCelery.last_name == "app.tasks.newspaper.reset_and_reselect_to_compose_for_day"
    assert _FakeCelery.last_args == [expected_day]
    assert resp == _FakeCelery.result


def test_reset_uses_the_given_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit ?day= is passed through verbatim as the Celery task arg."""
    _FakeCelery.result = _canned_result("2026-08-26")
    monkeypatch.setattr("celery.Celery", _FakeCelery)

    resp = admin_routes.admin_reset_to_compose_for_day(_req(query_params={"day": "2026-08-26"}))

    assert _FakeCelery.last_args == ["2026-08-26"]
    assert resp == _FakeCelery.result


def test_reset_surfaces_skipped_already_progressed_picks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pick that already composed/discarded before the reset is reported in reset.skipped, not silently dropped."""
    result = _canned_result("2026-08-26")
    result["reset"]["reverted_to_pending"] = ["a-1"]
    result["reset"]["skipped"] = [{"artifact_id": "a-2", "status": "composed"}]
    result["reset"]["fully_reverted"] = False
    _FakeCelery.result = result
    monkeypatch.setattr("celery.Celery", _FakeCelery)

    resp = admin_routes.admin_reset_to_compose_for_day(_req(query_params={"day": "2026-08-26"}))

    assert resp["reset"]["skipped"] == [{"artifact_id": "a-2", "status": "composed"}]
    assert resp["reset"]["fully_reverted"] is False


def test_reset_400s_on_malformed_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ?day= that isn't a valid YYYY-MM-DD date 400s cleanly instead of dispatching garbage."""
    monkeypatch.setattr("celery.Celery", _FakeCelery)

    resp = admin_routes.admin_reset_to_compose_for_day(_req(query_params={"day": "not-a-date"}))

    assert resp.status_code == 400


def test_reset_502s_when_broker_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broker connection failure surfaces as a clean 502, not an unhandled exception."""

    class _BrokenCelery:
        def __init__(self, *, broker: str, backend: str) -> None:  # noqa: ARG002
            raise RuntimeError("no broker")

    monkeypatch.setattr("celery.Celery", _BrokenCelery)

    resp = admin_routes.admin_reset_to_compose_for_day(_req())

    assert resp.status_code == 502


def test_reset_504s_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
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

    resp = admin_routes.admin_reset_to_compose_for_day(_req())

    assert resp.status_code == 504

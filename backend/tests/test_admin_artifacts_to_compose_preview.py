"""Admin routes for the new editorial-room artifact shadow-selection dashboard (admin_artifacts_to_compose_preview / admin_pin_artifact_for_tomorrow).

Backend has no direct import of the workers codebase, so both routes dispatch
into worker Celery tasks (app.tasks.newspaper.preview_to_compose_for_day /
.pin_artifact_for_tomorrow) and wait briefly on the result -- same shape as
admin_interrogate_compose_session. These tests fake `celery.Celery` the same
way test_admin_update_article_translations.py does for
_clear_and_reenqueue_translations, so no real broker/worker is involved.

This surface is explicitly SHADOW MODE / read-mostly: it must never touch
publish_queue, queue_drain_tasks, or any live compose trigger -- these tests
only exercise the two new routes themselves.
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


# --------------------------------------------------------------------------- #
# admin_artifacts_to_compose_preview
# --------------------------------------------------------------------------- #


def test_preview_defaults_day_to_tomorrow(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ?day= given -> dispatches for (real today + 1 day), not today itself."""
    from datetime import UTC, datetime, timedelta

    _FakeCelery.result = {"status": "ok", "compose_day": "unused", "items": []}
    monkeypatch.setattr("celery.Celery", _FakeCelery)

    resp = admin_routes.admin_artifacts_to_compose_preview(_req())

    expected_day = (datetime.now(tz=UTC).date() + timedelta(days=1)).isoformat()
    assert _FakeCelery.last_name == "app.tasks.newspaper.preview_to_compose_for_day"
    assert _FakeCelery.last_args == [expected_day]
    assert resp == _FakeCelery.result


def test_preview_uses_the_given_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit ?day= is passed through verbatim as the Celery task arg."""
    _FakeCelery.result = {"status": "ok", "compose_day": "2026-09-01", "items": []}
    monkeypatch.setattr("celery.Celery", _FakeCelery)

    resp = admin_routes.admin_artifacts_to_compose_preview(
        _req(query_params={"day": "2026-09-01"})
    )

    assert _FakeCelery.last_args == ["2026-09-01"]
    assert resp == _FakeCelery.result


def test_preview_400s_on_malformed_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ?day= that isn't a valid YYYY-MM-DD date 400s cleanly instead of dispatching garbage."""
    monkeypatch.setattr("celery.Celery", _FakeCelery)

    resp = admin_routes.admin_artifacts_to_compose_preview(
        _req(query_params={"day": "not-a-date"})
    )

    assert resp.status_code == 400


def test_preview_502s_when_broker_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broker connection failure surfaces as a clean 502, not an unhandled exception."""

    class _BrokenCelery:
        def __init__(self, *, broker: str, backend: str) -> None:  # noqa: ARG002
            raise RuntimeError("no broker")

    monkeypatch.setattr("celery.Celery", _BrokenCelery)

    resp = admin_routes.admin_artifacts_to_compose_preview(_req())

    assert resp.status_code == 502


def test_preview_504s_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
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

    resp = admin_routes.admin_artifacts_to_compose_preview(_req())

    assert resp.status_code == 504


# --------------------------------------------------------------------------- #
# admin_pin_artifact_for_tomorrow
# --------------------------------------------------------------------------- #


def test_pin_dispatches_with_the_path_artifact_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The artifact_id path param is forwarded verbatim as the Celery task arg."""
    _FakeCelery.result = {"ok": True, "artifact_id": "abc-123"}
    monkeypatch.setattr("celery.Celery", _FakeCelery)

    resp = admin_routes.admin_pin_artifact_for_tomorrow(
        _req(method="POST", path_params={"artifact_id": "abc-123"})
    )

    assert _FakeCelery.last_name == "app.tasks.newspaper.pin_artifact_for_tomorrow"
    assert _FakeCelery.last_args == ["abc-123"]
    assert resp == _FakeCelery.result


def test_pin_400s_when_artifact_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No artifact_id path param 400s cleanly rather than dispatching an empty id."""
    monkeypatch.setattr("celery.Celery", _FakeCelery)

    resp = admin_routes.admin_pin_artifact_for_tomorrow(_req(method="POST"))

    assert resp.status_code == 400


def test_pin_404s_when_the_worker_reports_unknown_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    """pin_for_tomorrow's {"ok": False} for an unknown id surfaces as a 404, not a bare 200."""
    _FakeCelery.result = {"ok": False, "artifact_id": "missing"}
    monkeypatch.setattr("celery.Celery", _FakeCelery)

    resp = admin_routes.admin_pin_artifact_for_tomorrow(
        _req(method="POST", path_params={"artifact_id": "missing"})
    )

    assert resp.status_code == 404

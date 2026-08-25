"""admin_get_artifact_content: full title/content/url/metadata for one editorial-room artifact -- the raw text that would actually get fed to the writer/composer, fetched on demand when an admin expands a Queue-tab row.

Same cross-service dispatch shape as the other artifact routes (backend has
no direct import of the workers codebase): send_task + a short synchronous
.get() on app.tasks.newspaper.get_artifact_detail. These tests fake
`celery.Celery` the same way test_admin_artifacts_to_compose_selected.py
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


def test_get_content_dispatches_by_artifact_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The artifact_id path param is passed through verbatim as the Celery task arg."""
    _FakeCelery.result = {
        "artifact_id": "abc-123",
        "title": "Big protocol update",
        "content": "Full raw body text goes here.",
        "metadata": {"display_name": "Some Service"},
        "service_id": "svc-a",
        "url": "https://example.com/post",
        "channel": "crawler",
        "status": "pending",
    }
    monkeypatch.setattr("celery.Celery", _FakeCelery)

    resp = admin_routes.admin_get_artifact_content(_req(path_params={"artifact_id": "abc-123"}))

    assert _FakeCelery.last_name == "app.tasks.newspaper.get_artifact_detail"
    assert _FakeCelery.last_args == ["abc-123"]
    assert resp == _FakeCelery.result


def test_get_content_400s_when_artifact_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No artifact_id path param at all 400s cleanly instead of dispatching garbage."""
    monkeypatch.setattr("celery.Celery", _FakeCelery)

    resp = admin_routes.admin_get_artifact_content(_req(path_params={}))

    assert resp.status_code == 400


def test_get_content_404s_for_unknown_or_malformed_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown OR malformed artifact_id both surface as a clean 404 -- get_artifact_detail fails closed to None for either case."""
    _FakeCelery.result = None
    monkeypatch.setattr("celery.Celery", _FakeCelery)

    resp = admin_routes.admin_get_artifact_content(_req(path_params={"artifact_id": "not-a-uuid"}))

    assert resp.status_code == 404


def test_get_content_502s_when_broker_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broker connection failure surfaces as a clean 502, not an unhandled exception."""

    class _BrokenCelery:
        def __init__(self, *, broker: str, backend: str) -> None:  # noqa: ARG002
            raise RuntimeError("no broker")

    monkeypatch.setattr("celery.Celery", _BrokenCelery)

    resp = admin_routes.admin_get_artifact_content(_req(path_params={"artifact_id": "abc-123"}))

    assert resp.status_code == 502


def test_get_content_504s_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
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

    resp = admin_routes.admin_get_artifact_content(_req(path_params={"artifact_id": "abc-123"}))

    assert resp.status_code == 504

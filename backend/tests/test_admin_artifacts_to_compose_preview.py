"""Admin routes for the editorial-room artifact shadow-selection dashboard (admin_artifacts_to_compose_preview / admin_pin_artifact_for_tomorrow).

2026-08-26: both routes call algorand_shared.to_compose_selection functions
directly (preview_to_compose_for_day / pin_for_tomorrow) instead of
dispatching into a worker over Celery -- that shape was purely an artifact
of the underlying selection/priority logic living in workers/, and became a
real production liability once a heavy background job filling the shared
Celery queue could make these routes time out waiting for a worker slot.
These tests monkeypatch the shared functions directly. The old broker-
unavailable/timeout (502/504) test cases are moot for a direct function call
and are dropped.

This surface is explicitly SHADOW MODE / read-mostly: it must never touch
publish_queue, queue_drain_tasks, or any live compose trigger -- these tests
only exercise the two routes themselves.
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


@pytest.fixture(autouse=True)
def _admin_allowed() -> Any:  # noqa: ANN401
    with patch.object(admin_routes, "require_admin_wallet", return_value=None):
        yield


# --------------------------------------------------------------------------- #
# admin_artifacts_to_compose_preview
# --------------------------------------------------------------------------- #


def test_preview_defaults_day_to_tomorrow(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ?day= given -> calls preview_to_compose_for_day for (real today + 1 day), not today itself."""
    from datetime import UTC, datetime, timedelta

    canned = {"status": "ok", "compose_day": "unused", "items": []}
    called = {}

    def _fake_preview(day: str) -> dict:
        called["day"] = day
        return canned

    monkeypatch.setattr(
        "algorand_shared.to_compose_selection.preview_to_compose_for_day", _fake_preview
    )

    resp = admin_routes.admin_artifacts_to_compose_preview(_req())

    expected_day = (datetime.now(tz=UTC).date() + timedelta(days=1)).isoformat()
    assert called["day"] == expected_day
    assert resp == canned


def test_preview_uses_the_given_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit ?day= is passed through verbatim."""
    canned = {"status": "ok", "compose_day": "2026-09-01", "items": []}
    called = {}

    def _fake_preview(day: str) -> dict:
        called["day"] = day
        return canned

    monkeypatch.setattr(
        "algorand_shared.to_compose_selection.preview_to_compose_for_day", _fake_preview
    )

    resp = admin_routes.admin_artifacts_to_compose_preview(
        _req(query_params={"day": "2026-09-01"})
    )

    assert called["day"] == "2026-09-01"
    assert resp == canned


def test_preview_400s_on_malformed_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ?day= that isn't a valid YYYY-MM-DD date 400s cleanly instead of calling through with garbage."""

    def _boom(_day: str) -> dict:
        raise AssertionError("must not be called for a malformed day")

    monkeypatch.setattr("algorand_shared.to_compose_selection.preview_to_compose_for_day", _boom)

    resp = admin_routes.admin_artifacts_to_compose_preview(
        _req(query_params={"day": "not-a-date"})
    )

    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# admin_pin_artifact_for_tomorrow
# --------------------------------------------------------------------------- #


def test_pin_dispatches_with_the_path_artifact_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The artifact_id path param is forwarded verbatim to pin_for_tomorrow."""
    called = {}

    def _fake_pin(artifact_id: str) -> bool:
        called["artifact_id"] = artifact_id
        return True

    monkeypatch.setattr("algorand_shared.to_compose_selection.pin_for_tomorrow", _fake_pin)

    resp = admin_routes.admin_pin_artifact_for_tomorrow(
        _req(method="POST", path_params={"artifact_id": "abc-123"})
    )

    assert called["artifact_id"] == "abc-123"
    assert resp == {"ok": True, "artifact_id": "abc-123"}


def test_pin_400s_when_artifact_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No artifact_id path param 400s cleanly rather than calling through with an empty id."""

    def _boom(_artifact_id: str) -> bool:
        raise AssertionError("must not be called with no artifact_id")

    monkeypatch.setattr("algorand_shared.to_compose_selection.pin_for_tomorrow", _boom)

    resp = admin_routes.admin_pin_artifact_for_tomorrow(_req(method="POST"))

    assert resp.status_code == 400


def test_pin_404s_when_pin_for_tomorrow_reports_unknown_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pin_for_tomorrow returning False for an unknown id surfaces as a 404, not a bare 200."""
    monkeypatch.setattr(
        "algorand_shared.to_compose_selection.pin_for_tomorrow", lambda _artifact_id: False
    )

    resp = admin_routes.admin_pin_artifact_for_tomorrow(
        _req(method="POST", path_params={"artifact_id": "missing"})
    )

    assert resp.status_code == 404

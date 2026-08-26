"""admin_artifacts_to_compose_selected: the REAL, persisted `to_compose` lineup for a day, as opposed to admin_artifacts_to_compose_preview's live forecast.

2026-08-26: calls algorand_shared.to_compose_selection.list_to_compose_for_day
directly (no more Celery round-trip into a worker process -- that shape was
purely an artifact of the underlying query living in workers/, and became a
real production liability once a heavy background job filling the shared
queue could make this read-only route time out). These tests monkeypatch
that function directly, the same seam-swap style the old Celery-mocking
tests used, just one hop shorter. The old broker-unavailable/timeout (502/
504) test cases are moot for a direct function call and are dropped.
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


def test_selected_defaults_day_to_tomorrow(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ?day= given -> calls list_to_compose_for_day for (real today + 1 day), not today itself."""
    from datetime import UTC, datetime, timedelta

    called = {}

    def _fake_list(day: str) -> list:
        called["day"] = day
        return []

    monkeypatch.setattr("algorand_shared.to_compose_selection.list_to_compose_for_day", _fake_list)

    resp = admin_routes.admin_artifacts_to_compose_selected(_req())

    expected_day = (datetime.now(tz=UTC).date() + timedelta(days=1)).isoformat()
    assert called["day"] == expected_day
    assert resp == {"compose_day": expected_day, "items": []}


def test_selected_uses_the_given_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit ?day= is passed through verbatim."""
    items = [
        {
            "slot": 0,
            "artifact_id": "abc-123",
            "lane": "human",
            "service_id": "svc-a",
            "picked_at": "2026-08-26T00:05:00",
        }
    ]
    called = {}

    def _fake_list(day: str) -> list:
        called["day"] = day
        return items

    monkeypatch.setattr("algorand_shared.to_compose_selection.list_to_compose_for_day", _fake_list)

    resp = admin_routes.admin_artifacts_to_compose_selected(
        _req(query_params={"day": "2026-08-26"})
    )

    assert called["day"] == "2026-08-26"
    assert resp == {"compose_day": "2026-08-26", "items": items}


def test_selected_returns_empty_items_when_nothing_locked_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty `to_compose` table (the daily beat hasn't fired yet) surfaces as items: [], not an error."""
    monkeypatch.setattr(
        "algorand_shared.to_compose_selection.list_to_compose_for_day", lambda _day: []
    )

    resp = admin_routes.admin_artifacts_to_compose_selected(
        _req(query_params={"day": "2026-08-26"})
    )

    assert resp == {"compose_day": "2026-08-26", "items": []}


def test_selected_400s_on_malformed_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ?day= that isn't a valid YYYY-MM-DD date 400s cleanly instead of calling through with garbage."""

    def _boom(_day: str) -> list:
        raise AssertionError("must not be called for a malformed day")

    monkeypatch.setattr("algorand_shared.to_compose_selection.list_to_compose_for_day", _boom)

    resp = admin_routes.admin_artifacts_to_compose_selected(
        _req(query_params={"day": "not-a-date"})
    )

    assert resp.status_code == 400

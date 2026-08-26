"""admin_reset_to_compose_for_day: the "Redo today's picks" admin action -- clears `day`'s locked-in `to_compose` selection and immediately re-selects.

2026-08-26: calls algorand_shared.to_compose_selection.reset_and_reselect_for_day
directly instead of dispatching into a worker over Celery. These tests
monkeypatch that function directly. The old broker-unavailable/timeout
(502/504) test cases are moot for a direct function call and are dropped.
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
    """No ?day= given -> calls reset_and_reselect_for_day for (real today + 1 day), matching the preview/selected routes' own default."""
    from datetime import UTC, datetime, timedelta

    expected_day = (datetime.now(tz=UTC).date() + timedelta(days=1)).isoformat()
    canned = _canned_result(expected_day)
    called = {}

    def _fake_reset(day: str) -> dict:
        called["day"] = day
        return canned

    monkeypatch.setattr(
        "algorand_shared.to_compose_selection.reset_and_reselect_for_day", _fake_reset
    )

    resp = admin_routes.admin_reset_to_compose_for_day(_req())

    assert called["day"] == expected_day
    assert resp == canned


def test_reset_uses_the_given_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit ?day= is passed through verbatim."""
    canned = _canned_result("2026-08-26")
    called = {}

    def _fake_reset(day: str) -> dict:
        called["day"] = day
        return canned

    monkeypatch.setattr(
        "algorand_shared.to_compose_selection.reset_and_reselect_for_day", _fake_reset
    )

    resp = admin_routes.admin_reset_to_compose_for_day(_req(query_params={"day": "2026-08-26"}))

    assert called["day"] == "2026-08-26"
    assert resp == canned


def test_reset_surfaces_skipped_already_progressed_picks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pick that already composed/discarded before the reset is reported in reset.skipped, not silently dropped."""
    result = _canned_result("2026-08-26")
    result["reset"]["reverted_to_pending"] = ["a-1"]
    result["reset"]["skipped"] = [{"artifact_id": "a-2", "status": "composed"}]
    result["reset"]["fully_reverted"] = False

    monkeypatch.setattr(
        "algorand_shared.to_compose_selection.reset_and_reselect_for_day", lambda _day: result
    )

    resp = admin_routes.admin_reset_to_compose_for_day(_req(query_params={"day": "2026-08-26"}))

    assert resp["reset"]["skipped"] == [{"artifact_id": "a-2", "status": "composed"}]
    assert resp["reset"]["fully_reverted"] is False


def test_reset_400s_on_malformed_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ?day= that isn't a valid YYYY-MM-DD date 400s cleanly instead of calling through with garbage."""

    def _boom(_day: str) -> dict:
        raise AssertionError("must not be called for a malformed day")

    monkeypatch.setattr("algorand_shared.to_compose_selection.reset_and_reselect_for_day", _boom)

    resp = admin_routes.admin_reset_to_compose_for_day(_req(query_params={"day": "not-a-date"}))

    assert resp.status_code == 400

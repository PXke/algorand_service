"""admin_get_artifact_content: full title/content/url/metadata for one editorial-room artifact -- the raw text that would actually get fed to the writer/composer, fetched on demand when an admin expands a Queue-tab row.

2026-08-26: calls algorand_shared.artifact_store.get_artifact_detail directly
(no more Celery round-trip into a worker process). These tests monkeypatch
that function directly. The old broker-unavailable/timeout (502/504) test
cases are moot for a direct function call and are dropped.
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


def test_get_content_dispatches_by_artifact_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The artifact_id path param is passed through verbatim to get_artifact_detail."""
    result = {
        "artifact_id": "abc-123",
        "title": "Big protocol update",
        "content": "Full raw body text goes here.",
        "metadata": {"display_name": "Some Service"},
        "service_id": "svc-a",
        "url": "https://example.com/post",
        "channel": "crawler",
        "status": "pending",
    }
    called = {}

    def _fake_detail(artifact_id: str) -> dict:
        called["artifact_id"] = artifact_id
        return result

    monkeypatch.setattr("algorand_shared.artifact_store.get_artifact_detail", _fake_detail)

    resp = admin_routes.admin_get_artifact_content(_req(path_params={"artifact_id": "abc-123"}))

    assert called["artifact_id"] == "abc-123"
    assert resp == result


def test_get_content_400s_when_artifact_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No artifact_id path param at all 400s cleanly instead of calling through with garbage."""

    def _boom(_artifact_id: str) -> dict | None:
        raise AssertionError("must not be called with no artifact_id")

    monkeypatch.setattr("algorand_shared.artifact_store.get_artifact_detail", _boom)

    resp = admin_routes.admin_get_artifact_content(_req(path_params={}))

    assert resp.status_code == 400


def test_get_content_404s_for_unknown_or_malformed_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown OR malformed artifact_id both surface as a clean 404 -- get_artifact_detail fails closed to None for either case."""
    monkeypatch.setattr(
        "algorand_shared.artifact_store.get_artifact_detail", lambda _artifact_id: None
    )

    resp = admin_routes.admin_get_artifact_content(_req(path_params={"artifact_id": "not-a-uuid"}))

    assert resp.status_code == 404

"""Admin share-link/comment routes: malformed ids 400 cleanly instead of 500ing on an unguarded UUID(...) parse."""

from __future__ import annotations

from unittest.mock import patch

from app.core.http import Request
from app.modules.admin.api import routes as admin_routes
from app.modules.admin.api.routes import _valid_uuid


def _req(*, path_params: dict[str, str], body: bytes = b"{}") -> Request:
    return Request(
        method="POST",
        headers={},
        query_params={},  # type: ignore[arg-type]
        path_params=path_params,
        body=body,
    )


def test_valid_uuid_accepts_real_uuid() -> None:
    """A well-formed UUID string passes."""
    assert _valid_uuid("00000000-0000-0000-0000-000000000001")


def test_valid_uuid_rejects_garbage() -> None:
    """A non-UUID string, and an empty string, both fail."""
    assert not _valid_uuid("not-a-uuid")
    assert not _valid_uuid("")


def test_admin_create_share_link_400s_on_malformed_article_id() -> None:
    """A malformed article_id 400s cleanly instead of an unhandled ValueError -> 500."""
    with patch.object(admin_routes, "require_admin_wallet", return_value=None):
        resp = admin_routes.admin_create_share_link(_req(path_params={"article_id": "nope"}))
    assert resp.status_code == 400


def test_admin_delete_article_comment_400s_on_malformed_comment_id() -> None:
    """A malformed comment_id 400s cleanly, same guard applied to the second id in this route."""
    with patch.object(admin_routes, "require_admin_wallet", return_value=None):
        resp = admin_routes.admin_delete_article_comment(
            _req(
                path_params={
                    "article_id": "00000000-0000-0000-0000-000000000001",
                    "comment_id": "nope",
                }
            )
        )
    assert resp.status_code == 400

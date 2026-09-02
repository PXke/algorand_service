"""Admin API routes for owner-supplied article sources (docs/newspaper-article-sources-design.md, Phase 1): require_admin_wallet enforcement, article-existence validation, oversize-field rejection, and the three handlers' happy paths."""

from __future__ import annotations

from unittest.mock import patch

from app.core.http import Request
from app.modules.admin.admin_source_store import AdminSourceItem
from app.modules.admin.api import routes as admin_routes

_ARTICLE_ID = "00000000-0000-0000-0000-000000000001"
_SOURCE_ID = "00000000-0000-0000-0000-000000000002"


def _req(*, path_params: dict[str, str], body: bytes = b"{}", method: str = "POST") -> Request:
    return Request(
        method=method,
        headers={},
        query_params={},  # type: ignore[arg-type]
        path_params=path_params,
        body=body,
    )


# --------------------------------------------------------------------------- #
# require_admin_wallet enforcement -- refused before anything else
# --------------------------------------------------------------------------- #


def test_create_admin_source_401s_without_a_valid_admin_wallet() -> None:
    """A request without a valid admin session is refused before article lookup or body parsing."""
    with (
        patch.object(admin_routes, "store") as mock_store,
        patch("app.modules.admin.admin_source_store.create_source") as mock_create,
    ):
        resp = admin_routes.admin_create_admin_source(
            _req(path_params={"article_id": _ARTICLE_ID}, body=b'{"label": "x", "content": "y"}')
        )
    assert resp.status_code in (401, 403, 503)
    mock_store.get_article.assert_not_called()
    mock_create.assert_not_called()


def test_list_admin_sources_401s_without_a_valid_admin_wallet() -> None:
    """A request without a valid admin session is refused before any store read."""
    with patch("app.modules.admin.admin_source_store.list_sources") as mock_list:
        resp = admin_routes.admin_list_admin_sources(_req(path_params={"article_id": _ARTICLE_ID}))
    assert resp.status_code in (401, 403, 503)
    mock_list.assert_not_called()


def test_delete_admin_source_401s_without_a_valid_admin_wallet() -> None:
    """A request without a valid admin session is refused before any store write."""
    with patch("app.modules.admin.admin_source_store.soft_delete_source") as mock_delete:
        resp = admin_routes.admin_delete_admin_source(
            _req(
                path_params={"article_id": _ARTICLE_ID, "source_id": _SOURCE_ID},
                method="DELETE",
            )
        )
    assert resp.status_code in (401, 403, 503)
    mock_delete.assert_not_called()


# --------------------------------------------------------------------------- #
# POST /articles/:article_id/sources -- validation + happy path
# --------------------------------------------------------------------------- #


def test_create_admin_source_400s_on_malformed_article_id() -> None:
    """A non-UUID article_id 400s cleanly instead of an unhandled ValueError -> 500."""
    with patch.object(admin_routes, "require_admin_wallet", return_value=None):
        resp = admin_routes.admin_create_admin_source(
            _req(path_params={"article_id": "nope"}, body=b'{"label": "x", "content": "y"}')
        )
    assert resp.status_code == 400


def test_create_admin_source_404s_when_the_article_does_not_exist() -> None:
    """POST validates the article exists (store.get_article) before writing anything."""
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch.object(admin_routes.store, "get_article", return_value=None),
    ):
        resp = admin_routes.admin_create_admin_source(
            _req(
                path_params={"article_id": _ARTICLE_ID},
                body=b'{"label": "x", "content": "y"}',
            )
        )
    assert resp.status_code == 404


def test_create_admin_source_400s_on_oversize_content_never_truncates() -> None:
    """An oversize field 400s at decode time (msgspec Meta.max_length) -- never silently truncated."""
    import json

    from app.core.config import settings

    oversize = "x" * (settings.admin_source_max_chars + 1)
    body = json.dumps({"label": "l", "content": oversize}).encode()
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch.object(admin_routes.store, "get_article", return_value=object()),
        patch("app.modules.admin.admin_source_store.create_source") as mock_create,
    ):
        resp = admin_routes.admin_create_admin_source(
            _req(path_params={"article_id": _ARTICLE_ID}, body=body)
        )
    assert resp.status_code == 400
    mock_create.assert_not_called()


def test_create_admin_source_400s_on_oversize_label() -> None:
    """A label over 200 chars 400s at decode time."""
    body = ('{"label": "%s", "content": "y"}' % ("l" * 201)).encode()
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch.object(admin_routes.store, "get_article", return_value=object()),
    ):
        resp = admin_routes.admin_create_admin_source(
            _req(path_params={"article_id": _ARTICLE_ID}, body=body)
        )
    assert resp.status_code == 400


def test_create_admin_source_400s_on_oversize_attribution_url() -> None:
    """An attribution_url over 512 chars 400s at decode time."""
    body = ('{"label": "l", "content": "y", "attribution_url": "%s"}' % ("u" * 513)).encode()
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch.object(admin_routes.store, "get_article", return_value=object()),
    ):
        resp = admin_routes.admin_create_admin_source(
            _req(path_params={"article_id": _ARTICLE_ID}, body=body)
        )
    assert resp.status_code == 400


def test_create_admin_source_happy_path_stamps_the_verified_wallet() -> None:
    """added_by is stamped from the VERIFIED session wallet, never the raw X-Admin-Wallet header."""
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch.object(admin_routes, "verified_admin_wallet", return_value="0xVERIFIED"),
        patch.object(admin_routes.store, "get_article", return_value=object()),
        patch(
            "app.modules.admin.admin_source_store.create_source", return_value="new-source-id"
        ) as mock_create,
    ):
        body = (
            b'{"label": "Exclusive interview", "content": "the creator said...", '
            b'"attribution_url": "https://example.com/x"}'
        )
        result = admin_routes.admin_create_admin_source(
            _req(path_params={"article_id": _ARTICLE_ID}, body=body)
        )

    assert result == {"source_id": "new-source-id"}
    mock_create.assert_called_once_with(
        _ARTICLE_ID,
        added_by="0xVERIFIED",
        label="Exclusive interview",
        content="the creator said...",
        attribution_url="https://example.com/x",
    )


# --------------------------------------------------------------------------- #
# GET /articles/:article_id/sources
# --------------------------------------------------------------------------- #


def test_list_admin_sources_400s_on_malformed_article_id() -> None:
    """A non-UUID article_id 400s cleanly."""
    with patch.object(admin_routes, "require_admin_wallet", return_value=None):
        resp = admin_routes.admin_list_admin_sources(_req(path_params={"article_id": "nope"}))
    assert resp.status_code == 400


def test_list_admin_sources_happy_path_returns_items() -> None:
    """GET returns the store's items, content elided to a preview -- never the full content field."""
    item = AdminSourceItem(
        source_id=_SOURCE_ID,
        article_id=_ARTICLE_ID,
        added_by="0xADMIN",
        label="Exclusive interview",
        kind="text",
        attribution_url="",
        content_preview="the creator said...",
        content_length=20,
        added_at_epoch=1_700_000_000,
    )
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch("app.modules.admin.admin_source_store.list_sources", return_value=[item]),
    ):
        result = admin_routes.admin_list_admin_sources(
            _req(path_params={"article_id": _ARTICLE_ID})
        )

    assert isinstance(result, dict)
    assert len(result["items"]) == 1
    assert result["items"][0]["source_id"] == _SOURCE_ID
    assert result["items"][0]["content_preview"] == "the creator said..."
    # never the full content -- content_preview only
    assert "content" not in result["items"][0]


# --------------------------------------------------------------------------- #
# DELETE /articles/:article_id/sources/:source_id
# --------------------------------------------------------------------------- #


def test_delete_admin_source_400s_on_malformed_ids() -> None:
    """A non-UUID article_id or source_id 400s cleanly."""
    with patch.object(admin_routes, "require_admin_wallet", return_value=None):
        resp = admin_routes.admin_delete_admin_source(
            _req(path_params={"article_id": "nope", "source_id": _SOURCE_ID}, method="DELETE")
        )
    assert resp.status_code == 400

    with patch.object(admin_routes, "require_admin_wallet", return_value=None):
        resp = admin_routes.admin_delete_admin_source(
            _req(path_params={"article_id": _ARTICLE_ID, "source_id": "nope"}, method="DELETE")
        )
    assert resp.status_code == 400


def test_delete_admin_source_happy_path_soft_removes() -> None:
    """DELETE calls soft_delete_source (never a real delete) and reports removed=True."""
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch(
            "app.modules.admin.admin_source_store.soft_delete_source", return_value=True
        ) as mock_delete,
    ):
        result = admin_routes.admin_delete_admin_source(
            _req(
                path_params={"article_id": _ARTICLE_ID, "source_id": _SOURCE_ID},
                method="DELETE",
            )
        )

    assert result == {"removed": True, "article_id": _ARTICLE_ID, "source_id": _SOURCE_ID}
    mock_delete.assert_called_once_with(_ARTICLE_ID, _SOURCE_ID)

"""admin.api.routes TRUNCATE-all endpoints (reset-articles, clear-domains, clear-classifier-reviews).

- Outside prod, proceed unconditionally (dev/test convenience).
- In prod, require a `{"confirm": "<table-set hash>"}` body field so a
  stray/blind POST can't wipe production data.
- Their failure responses no longer leak str(exc) -- generic message, logged
  exception instead.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from app.core.config import settings
from app.core.http import Request
from app.modules.admin.api import routes as admin_routes


def _req(*, body: bytes = b"{}", path_params: dict[str, str] | None = None) -> Request:
    return Request(
        method="POST",
        headers={},
        query_params={},  # type: ignore[arg-type]
        path_params=path_params or {},
        body=body,
    )


def test_truncate_confirmation_hash_is_deterministic_per_table_set() -> None:
    """The same table set always hashes the same way; a different set hashes differently."""
    tables = ("a", "b", "c")
    same_hash = admin_routes._truncate_confirmation_hash(tables)
    assert same_hash == admin_routes._truncate_confirmation_hash(tables)
    other_hash = admin_routes._truncate_confirmation_hash(("a", "b", "c", "d"))
    assert same_hash != other_hash


def test_require_truncate_confirmation_allows_outside_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dev/test convenience: no confirmation needed when APP_ENV != prod."""
    monkeypatch.setattr(settings, "app_env", "dev")
    assert admin_routes._require_truncate_confirmation(_req(), ("t1",)) is None


def test_require_truncate_confirmation_blocks_in_prod_without_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In prod, an empty body is rejected with 403."""
    monkeypatch.setattr(settings, "app_env", "prod")
    resp = admin_routes._require_truncate_confirmation(_req(body=b"{}"), ("t1",))
    assert resp is not None
    assert resp.status_code == 403


def test_require_truncate_confirmation_allows_in_prod_with_correct_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In prod, the exact table-set hash in the body clears the guard."""
    monkeypatch.setattr(settings, "app_env", "prod")
    tables = ("t1", "t2")
    confirm = admin_routes._truncate_confirmation_hash(tables)
    body = f'{{"confirm": "{confirm}"}}'.encode()
    assert admin_routes._require_truncate_confirmation(_req(body=body), tables) is None


def test_require_truncate_confirmation_rejects_wrong_hash_in_prod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A confirm value that doesn't match the table-set hash is still rejected."""
    monkeypatch.setattr(settings, "app_env", "prod")
    resp = admin_routes._require_truncate_confirmation(
        _req(body=b'{"confirm": "not-the-right-hash"}'), ("t1",)
    )
    assert resp is not None
    assert resp.status_code == 403


def test_admin_reset_articles_truncates_outside_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside prod, the endpoint truncates every table in its list without confirmation."""
    monkeypatch.setattr(settings, "app_env", "dev")
    session = MagicMock()
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch("app.core.cassandra.get_cassandra_session", return_value=session),
        patch("app.core.typesense_client.clear_search_index", return_value=True),
    ):
        result = admin_routes.admin_reset_articles(_req())
    assert result["reset"] is True
    assert session.execute.call_count == len(admin_routes._RESET_ARTICLES_TABLES)


def test_admin_reset_articles_blocked_in_prod_without_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In prod without a confirm field, the endpoint 403s before touching Cassandra."""
    monkeypatch.setattr(settings, "app_env", "prod")
    session = MagicMock()
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch("app.core.cassandra.get_cassandra_session", return_value=session),
    ):
        resp = admin_routes.admin_reset_articles(_req(body=b"{}"))
    assert resp.status_code == 403
    session.execute.assert_not_called()


def test_admin_reset_articles_500_is_generic_and_does_not_leak_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Cassandra failure surfaces a generic 500, not the raw exception text."""
    monkeypatch.setattr(settings, "app_env", "dev")
    session = MagicMock()
    session.execute.side_effect = RuntimeError("super-secret-internal-detail")
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch("app.core.cassandra.get_cassandra_session", return_value=session),
    ):
        resp = admin_routes.admin_reset_articles(_req())
    assert resp.status_code == 500
    assert "super-secret-internal-detail" not in resp.description


def test_admin_clear_domains_blocked_in_prod_without_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In prod without a confirm field, clear-domains 403s before touching Cassandra."""
    monkeypatch.setattr(settings, "app_env", "prod")
    session = MagicMock()
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch("app.core.cassandra.get_cassandra_session", return_value=session),
    ):
        resp = admin_routes.admin_clear_domains(_req(body=b"{}"))
    assert resp.status_code == 403
    session.execute.assert_not_called()


def test_admin_clear_domains_allowed_in_prod_with_correct_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In prod, the correct confirm hash lets clear-domains truncate as usual."""
    monkeypatch.setattr(settings, "app_env", "prod")
    session = MagicMock()
    confirm = admin_routes._truncate_confirmation_hash(admin_routes._CLEAR_DOMAINS_TABLES)
    body = f'{{"confirm": "{confirm}"}}'.encode()
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch.object(admin_routes, "_invalidate_domains_cache"),
        patch("app.core.cassandra.get_cassandra_session", return_value=session),
    ):
        result = admin_routes.admin_clear_domains(_req(body=body))
    assert result == {"cleared": True}
    session.execute.assert_called_once_with("TRUNCATE domain_tracking")


def test_admin_clear_classifier_reviews_blocked_in_prod_without_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In prod without a confirm field, clear-classifier-reviews 403s before touching Cassandra."""
    monkeypatch.setattr(settings, "app_env", "prod")
    session = MagicMock()
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch("app.core.cassandra.get_cassandra_session", return_value=session),
    ):
        resp = admin_routes.admin_clear_classifier_reviews(_req(body=b"{}"))
    assert resp.status_code == 403
    session.execute.assert_not_called()


def test_admin_clear_classifier_reviews_allowed_in_prod_with_correct_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In prod, the correct confirm hash lets clear-classifier-reviews truncate as usual."""
    monkeypatch.setattr(settings, "app_env", "prod")
    session = MagicMock()
    confirm = admin_routes._truncate_confirmation_hash(
        admin_routes._CLEAR_CLASSIFIER_REVIEWS_TABLES
    )
    body = f'{{"confirm": "{confirm}"}}'.encode()
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch("app.core.cassandra.get_cassandra_session", return_value=session),
    ):
        result = admin_routes.admin_clear_classifier_reviews(_req(body=body))
    assert result == {"cleared": True}
    assert session.execute.call_args_list == [
        call("TRUNCATE classifier_review_pending"),
        call("TRUNCATE classifier_review_queue"),
    ]


def test_admin_clear_classifier_reviews_truncates_outside_prod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside prod, the endpoint truncates both tables without confirmation."""
    monkeypatch.setattr(settings, "app_env", "dev")
    session = MagicMock()
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch("app.core.cassandra.get_cassandra_session", return_value=session),
    ):
        result = admin_routes.admin_clear_classifier_reviews(_req())
    assert result == {"cleared": True}
    assert session.execute.call_count == len(admin_routes._CLEAR_CLASSIFIER_REVIEWS_TABLES)


def test_admin_retrain_500_is_generic_and_does_not_leak_exception_text() -> None:
    """A broker failure surfaces a generic 500, not the raw exception text."""
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch("celery.Celery") as mock_celery_cls,
    ):
        mock_celery_cls.side_effect = RuntimeError("broker-credentials-xyz")
        resp = admin_routes.admin_retrain(_req())
    assert resp.status_code == 500
    assert "broker-credentials-xyz" not in resp.description


def test_admin_assign_brief_now_500_is_generic_and_does_not_leak_exception_text() -> None:
    """A broker failure surfaces a generic 500, not the raw exception text."""
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch.object(admin_routes.store, "get_brief", return_value={"linked_article_id": None}),
        patch("celery.Celery") as mock_celery_cls,
    ):
        mock_celery_cls.side_effect = RuntimeError("broker-credentials-xyz")
        resp = admin_routes.admin_assign_brief_now(_req(path_params={"brief_id": "b1"}))
    assert resp.status_code == 500
    assert "broker-credentials-xyz" not in resp.description

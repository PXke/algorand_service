"""sharing.store: share-link dual-write/revoke, and the shared comment thread round-trip."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from conftest import execute_pairs, patch_cassandra, stmt_cql

from app.core.statements import DraftCommentStmts, ShareLinkStmts
from app.modules.sharing import store
from app.schemas import CommentQuoteAnchor

_ARTICLE_ID = str(uuid4())


class _OneResult:
    """Minimal stand-in for a Cassandra ResultSet's .one()."""

    def __init__(self, row: object) -> None:
        self._row = row

    def one(self) -> object:
        return self._row


def _link_row(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "share_token": "tok_abc123",
        "article_id": _ARTICLE_ID,
        "label": "for the editor",
        "created_at": datetime(2026, 8, 12, tzinfo=UTC),
        "created_by": "0xADMIN",
        "revoked": False,
        "revoked_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_create_link_dual_writes_both_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_link writes both share_links and share_links_by_article, and returns a usable token."""
    session = patch_cassandra(monkeypatch)
    item = store.create_link(_ARTICLE_ID, label="reviewer copy", created_by="0xADMIN")

    assert item.token
    assert item.article_id == _ARTICLE_ID
    assert item.revoked is False

    pairs = execute_pairs(session)
    stmts_used = {p[0] for p in pairs}
    assert stmt_cql(ShareLinkStmts, "INSERT") in stmts_used
    assert stmt_cql(ShareLinkStmts, "INSERT_BY_ARTICLE") in stmts_used


def test_resolve_active_link_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid, unrevoked token resolves with no error."""
    fake = MagicMock()
    fake.execute.return_value = _OneResult(_link_row())
    patch_cassandra(monkeypatch, fake)
    link, err = store.resolve_active_link("tok_abc123")
    assert err is None
    assert link is not None
    assert link.article_id == _ARTICLE_ID


def test_resolve_active_link_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown token resolves to the not_found error, not a crash."""
    fake = MagicMock()
    fake.execute.return_value = _OneResult(None)
    patch_cassandra(monkeypatch, fake)
    link, err = store.resolve_active_link("does-not-exist")
    assert link is None
    assert err == "not_found"


def test_resolve_active_link_revoked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A revoked token resolves to the revoked error, distinct from not_found."""
    fake = MagicMock()
    fake.execute.return_value = _OneResult(_link_row(revoked=True))
    patch_cassandra(monkeypatch, fake)
    link, err = store.resolve_active_link("tok_abc123")
    assert link is None
    assert err == "revoked"


def test_revoke_link_sets_revoked_in_both_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    """revoke_link marks both share_links and share_links_by_article revoked."""
    fake = MagicMock()
    fake.execute.side_effect = [_OneResult(_link_row()), None, None]
    patch_cassandra(monkeypatch, fake)
    item = store.revoke_link("tok_abc123")

    assert item is not None
    assert item.revoked is True
    assert item.revoked_at_epoch is not None

    pairs = execute_pairs(fake)
    stmts_used = [p[0] for p in pairs]
    assert stmt_cql(ShareLinkStmts, "GET") in stmts_used
    assert stmt_cql(ShareLinkStmts, "REVOKE") in stmts_used
    assert stmt_cql(ShareLinkStmts, "REVOKE_BY_ARTICLE") in stmts_used


def test_revoke_link_unknown_token_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Revoking a token that doesn't exist returns None instead of writing anything."""
    fake = MagicMock()
    fake.execute.return_value = _OneResult(None)
    patch_cassandra(monkeypatch, fake)
    item = store.revoke_link("does-not-exist")
    assert item is None


def test_add_comment_and_list_comments_round_trip_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A comment with a highlight anchor round-trips through add_comment -> list_comments."""
    session = patch_cassandra(monkeypatch)
    anchor = CommentQuoteAnchor(quote="Algorand mainnet", prefix="on ", suffix=" launched")
    created = store.add_comment(
        _ARTICLE_ID, body="worth double-checking this claim", author_name="Reviewer A", anchor=anchor
    )
    assert created.anchor_quote == "Algorand mainnet"
    assert created.anchor_prefix == "on "
    assert created.anchor_suffix == " launched"

    pairs = execute_pairs(session)
    assert any(p[0] == stmt_cql(DraftCommentStmts, "INSERT") for p in pairs)


def test_add_comment_without_anchor_is_a_general_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    """anchor=None produces a comment with all three anchor fields None (a general, unanchored comment)."""
    patch_cassandra(monkeypatch)
    created = store.add_comment(_ARTICLE_ID, body="general feedback", author_name="", anchor=None)
    assert created.anchor_quote is None
    assert created.anchor_prefix is None
    assert created.anchor_suffix is None


def test_list_comments_returns_stored_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_comments converts every row in the article's partition to a CommentItem."""
    comment_id = uuid4()
    row = SimpleNamespace(
        article_id=_ARTICLE_ID,
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
        comment_id=comment_id,
        body="nice catch",
        author_name="Reviewer B",
        anchor_quote=None,
        anchor_prefix=None,
        anchor_suffix=None,
    )
    fake = MagicMock()
    fake.execute.return_value = [row]
    patch_cassandra(monkeypatch, fake)
    items = store.list_comments(_ARTICLE_ID)
    assert len(items) == 1
    assert items[0].comment_id == str(comment_id)
    assert items[0].body == "nice catch"


def test_delete_comment_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """delete_comment finds the matching row by comment_id and deletes it."""
    comment_id = uuid4()
    row = SimpleNamespace(
        article_id=_ARTICLE_ID,
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
        comment_id=comment_id,
    )
    fake = MagicMock()
    fake.execute.side_effect = [[row], None]
    patch_cassandra(monkeypatch, fake)
    deleted = store.delete_comment(_ARTICLE_ID, str(comment_id))
    assert deleted is True

    pairs = execute_pairs(fake)
    assert any(p[0] == stmt_cql(DraftCommentStmts, "DELETE") for p in pairs)


def test_delete_comment_unknown_id_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """delete_comment returns False (no write attempted) when the id isn't in the partition."""
    fake = MagicMock()
    fake.execute.return_value = []
    patch_cassandra(monkeypatch, fake)
    deleted = store.delete_comment(_ARTICLE_ID, str(uuid4()))
    assert deleted is False

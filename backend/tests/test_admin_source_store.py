"""admin_source_store (backend write side): attach/list-with-preview/soft-remove round trip against article_admin_sources."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from conftest import execute_pairs, patch_cassandra, stmt_cql

from app.core.statements import AdminSourceStmts
from app.modules.admin import admin_source_store as store

_ARTICLE_ID = str(uuid4())


def _row(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "article_id": _ARTICLE_ID,
        "source_id": str(uuid4()),
        "added_by": "0xADMIN",
        "label": "Exclusive interview",
        "kind": "text",
        "attribution_url": "",
        "content": "x" * 300,
        "status": "active",
        "added_at": datetime(2026, 9, 2, tzinfo=UTC),
        "removed_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_create_source_writes_and_returns_a_source_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_source writes one INSERT and returns a usable, non-empty source_id."""
    session = patch_cassandra(monkeypatch)
    source_id = store.create_source(
        _ARTICLE_ID,
        added_by="0xADMIN",
        label="Exclusive interview",
        content="the creator said...",
        attribution_url="",
    )
    assert source_id

    pairs = execute_pairs(session)
    assert len(pairs) == 1
    stmt, params = pairs[0]
    assert stmt == stmt_cql(AdminSourceStmts, "INSERT")
    assert params[2] == "0xADMIN"  # added_by
    assert params[3] == "Exclusive interview"  # label
    assert params[4] == "text"  # kind
    assert params[6] == "the creator said..."  # content
    assert params[7] == "active"  # status


def test_list_sources_returns_only_active_rows_with_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_sources filters to active rows and elides content to a bounded preview + its true length."""
    rows = [
        _row(source_id="active-1", status="active", content="a" * 300),
        _row(source_id="removed-1", status="removed", content="should not appear"),
    ]
    fake = MagicMock()
    fake.execute.return_value = rows
    patch_cassandra(monkeypatch, fake)

    items = store.list_sources(_ARTICLE_ID)
    assert [i.source_id for i in items] == ["active-1"]
    item = items[0]
    assert item.content_length == 300
    assert len(item.content_preview) == store.PREVIEW_CHARS
    assert item.article_id == _ARTICLE_ID
    assert item.added_by == "0xADMIN"


def test_list_sources_caps_to_the_active_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """More active rows than the caller-facing limit are capped."""
    rows = [_row(source_id=f"s{i}", status="active") for i in range(5)]
    fake = MagicMock()
    fake.execute.return_value = rows
    patch_cassandra(monkeypatch, fake)

    items = store.list_sources(_ARTICLE_ID, limit=2)
    assert [i.source_id for i in items] == ["s0", "s1"]


def test_soft_delete_source_issues_the_update_statement(monkeypatch: pytest.MonkeyPatch) -> None:
    """soft_delete_source issues SOFT_DELETE (status='removed' + removed_at), never a real DELETE."""
    session = patch_cassandra(monkeypatch)
    result = store.soft_delete_source(_ARTICLE_ID, str(uuid4()))
    assert result is True

    pairs = execute_pairs(session)
    assert len(pairs) == 1
    stmt, _params = pairs[0]
    assert stmt == stmt_cql(AdminSourceStmts, "SOFT_DELETE")


def test_soft_delete_source_is_idempotent_on_an_unknown_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A soft-delete of a source_id that doesn't exist is a no-op via IF EXISTS, not an error."""
    fake = MagicMock()
    fake.execute.return_value = None
    patch_cassandra(monkeypatch, fake)
    assert store.soft_delete_source(_ARTICLE_ID, str(uuid4())) is True

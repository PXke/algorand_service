"""apply_recomposed_article must not surface a DRAFTED live article back into search/IndexNow as a side effect of an approved recompose -- root-caused 2026-08-11 before it could bite live on the held-draft Lumi Rogue article."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.modules.newspaper.tasks import publish_tasks

_LIVE_ID = "11111111-1111-1111-1111-111111111111"
_DRAFT_ID = "22222222-2222-2222-2222-222222222222"


def _fake_article(**overrides: object) -> SimpleNamespace:
    base = {
        "article_id": _LIVE_ID,
        "title": "Old title",
        "summary": "Old summary",
        "body": "Old body",
        "service_id": "lumirogue.com",
        "slug": "lumi-rogue",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _run_apply(monkeypatch: pytest.MonkeyPatch, *, live_is_drafted: bool) -> dict:
    live = _fake_article()
    draft = _fake_article(
        article_id=_DRAFT_ID, title="New title", summary="New summary", body="New body"
    )

    def fake_get_article(article_id: str) -> SimpleNamespace:
        return draft if article_id == _DRAFT_ID else live

    monkeypatch.setattr("app.modules.newspaper.article_store.get_article", fake_get_article)
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.replace_article_content",
        lambda **kw: datetime.now(tz=UTC),  # noqa: ARG005
    )
    monkeypatch.setattr("app.modules.newspaper.article_version_store.save_article_version", MagicMock())

    # Resolve _Stmt descriptors to their raw CQL so fake_execute's dispatch
    # below can tell the queries apart -- without this, ArticlesStmts.X
    # resolves to session.prepare(cql), i.e. an opaque MagicMock, not a string.
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    session = MagicMock()

    # Both the live and draft article reads now go through the SAME
    # ArticlesStmts.GET_FULL_BY_ID statement (2026-08-24) -- dispatch on
    # which article_id the call was made for, not on query text.
    def fake_execute(query: str, params: tuple = ()) -> MagicMock:  # noqa: ARG001
        result = MagicMock()
        aid = str(params[0]) if params else ""
        if aid == _DRAFT_ID:
            result.one.return_value = SimpleNamespace(
                tags=["nft"], image_url="https://example.com/hero.png"
            )
        elif aid == _LIVE_ID:
            result.one.return_value = SimpleNamespace(
                published_at=datetime.now(tz=UTC),
                status="draft" if live_is_drafted else "published",
            )
        else:
            result.one.return_value = None
        return result

    session.execute.side_effect = fake_execute
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: session)

    index_mock = MagicMock()
    monkeypatch.setattr(publish_tasks, "index_article", index_mock)
    translate_mock = MagicMock()
    monkeypatch.setattr(publish_tasks, "enqueue_article_translations", translate_mock)
    ping_mock = MagicMock()
    monkeypatch.setattr("app.modules.newspaper.indexnow.ping_article", ping_mock)

    result = publish_tasks.apply_recomposed_article(_DRAFT_ID, _LIVE_ID)

    index_mock.delay.assert_not_called() if live_is_drafted else index_mock.delay.assert_called_once()
    if live_is_drafted:
        translate_mock.assert_not_called()
        ping_mock.assert_not_called()
    else:
        translate_mock.assert_called_once()
        ping_mock.assert_called_once()
    return result


def test_apply_recomposed_article_skips_fanout_when_live_is_drafted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drafted live article gets its content swapped but no index/translate/IndexNow fanout."""
    result = _run_apply(monkeypatch, live_is_drafted=True)
    assert result["status"] == "ok_draft_preserved"
    assert result["article_id"] == _LIVE_ID


def test_apply_recomposed_article_runs_fanout_when_live_is_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The normal (non-draft) case is unaffected -- full fanout still runs."""
    result = _run_apply(monkeypatch, live_is_drafted=False)
    assert result["status"] == "ok"
    assert result["article_id"] == _LIVE_ID

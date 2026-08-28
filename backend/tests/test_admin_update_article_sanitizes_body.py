"""update_article must sanitize the body before writing it to Cassandra (W1-B).

Mirrors the frontend allowlist server-side via nh3 (app/core/sanitize.py) so
a malicious body -- an admin paste, or LLM writer output that reaches this
path via a recompose applied through the admin surface -- can never reach
storage carrying a live <script> tag or event-handler attribute, regardless
of what later renders it.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.admin.stores.cassandra import AdminCassandraStore
from app.modules.news.stores.base import StoredArticle


def _article(**overrides: Any) -> StoredArticle:  # noqa: ANN401
    base = {
        "article_id": "11111111-1111-1111-1111-111111111111",
        "service_id": "svc",
        "title": "Original title",
        "summary": "Original summary",
        "body": "Original body",
        "published_at_epoch": 1000,
        "translations": None,
        "slug": "original-title",
    }
    base.update(overrides)
    return StoredArticle(**base)


def _wire_store(monkeypatch: pytest.MonkeyPatch, *, current: StoredArticle) -> dict[str, Any]:
    """Patch update_article's collaborators so only _write_article's body arg is observed."""
    store = AdminCassandraStore()
    written: dict[str, Any] = {}

    def _fake_get_article(_article_id: str) -> StoredArticle:
        # First call reads the current row; the second (post-write) re-read
        # only feeds best-effort Typesense/IndexNow calls, already patched
        # below -- its exact content doesn't matter for this test.
        return current

    def _fake_write_article(
        _current: StoredArticle, _title: str, _summary: str, body: str, **_kw: str
    ) -> None:
        written["body"] = body

    monkeypatch.setattr(store, "get_article", _fake_get_article)
    monkeypatch.setattr(store, "_save_version_snapshot", lambda *_a, **_kw: None)
    monkeypatch.setattr(store, "_write_article", _fake_write_article)
    monkeypatch.setattr("app.modules.seo.indexnow.ping_article", lambda *_a, **_kw: None)
    monkeypatch.setattr("app.core.typesense_client.upsert_article_document", lambda **_kw: None)
    return {"store": store, "written": written}


def test_update_article_strips_script_tag_before_writing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A <script> tag (and its content) in an admin-edited body never reaches storage."""
    current = _article()
    ctx = _wire_store(monkeypatch, current=current)

    ctx["store"].update_article(current.article_id, body="Hello<script>alert(1)</script> world")

    body = ctx["written"]["body"]
    assert "<script" not in body
    assert "alert(1)" not in body
    assert "Hello" in body
    assert "world" in body


def test_update_article_strips_event_handler_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    """An onerror= handler on an otherwise-allowed <img> never reaches storage."""
    current = _article()
    ctx = _wire_store(monkeypatch, current=current)

    ctx["store"].update_article(
        current.article_id, body='<img src="https://example.com/x.png" onerror="alert(1)">'
    )

    body = ctx["written"]["body"]
    assert "onerror" not in body
    assert "alert(1)" not in body


def test_update_article_strips_javascript_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """A javascript: href in an admin-edited body never reaches storage."""
    current = _article()
    ctx = _wire_store(monkeypatch, current=current)

    ctx["store"].update_article(current.article_id, body='<a href="javascript:alert(1)">click</a>')

    body = ctx["written"]["body"]
    assert "javascript:" not in body


def test_update_article_preserves_ordinary_markdown_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """A normal body with no HTML must pass through unchanged (aside from the leading/trailing strip)."""
    current = _article()
    ctx = _wire_store(monkeypatch, current=current)
    clean_body = "# Heading\n\nSome **bold** prose with a [link](https://example.com)."

    ctx["store"].update_article(current.article_id, body=clean_body)

    assert ctx["written"]["body"] == clean_body

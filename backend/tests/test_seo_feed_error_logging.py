"""Regression: rss_feed / llms_full_txt must log a warning on a body-fetch failure.

Both must not swallow silently when the get_articles() body fetch fails, per
CLAUDE.md section 3 -- a broad except must log at >= warning with context,
even when falling back to a degraded-but-valid response is the right
behavior.
"""

from __future__ import annotations

import logging

import pytest

from app.core.http import QueryParams, Request
from app.modules.news.models.schemas import ArticleFeedItem
from app.modules.seo.api import routes as seo_routes


def _feed(n: int, *, epoch: int = 1_750_000_000) -> list[ArticleFeedItem]:
    return [
        ArticleFeedItem(
            article_id=f"id{i}",
            service_id="svc",
            title=f"Title {i}",
            summary=f"Summary {i}",
            published_at_epoch=epoch + i,
            tags=["sdk"],
        )
        for i in range(n)
    ]


def _request() -> Request:
    return Request(method="GET", headers={}, query_params=QueryParams({}), path_params={})


def _boom(_ids: list[str]) -> None:
    raise RuntimeError("cassandra down")


def test_rss_feed_logs_warning_when_get_articles_fails(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A get_articles() failure must still produce a valid (summary-only) feed, but must not vanish silently."""
    monkeypatch.setattr(seo_routes.news, "list_feed", lambda **_kw: _feed(2))
    monkeypatch.setattr(seo_routes.news, "get_articles", _boom)

    with caplog.at_level(logging.WARNING, logger="app.modules.seo.api.routes"):
        resp = seo_routes.rss_feed(_request())

    assert resp.status_code == 200
    assert "<item>" in resp.description
    assert any(
        "rss_feed" in r.message and "get_articles failed" in r.message for r in caplog.records
    )


def test_llms_full_txt_logs_warning_when_get_articles_fails(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same contract for llms_full_txt: degrade to bodies-empty output, but log the failure."""
    monkeypatch.setattr(seo_routes.news, "list_feed", lambda **_kw: _feed(2))
    monkeypatch.setattr(seo_routes.news, "get_articles", _boom)

    with caplog.at_level(logging.WARNING, logger="app.modules.seo.api.routes"):
        resp = seo_routes.llms_full_txt(_request())

    assert resp.status_code == 200
    assert any(
        "llms_full_txt" in r.message and "get_articles failed" in r.message for r in caplog.records
    )

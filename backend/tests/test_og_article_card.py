"""/og/article/<id>.png accepts the article SLUG as well as the uuid.

The SPA builds this URL from the route's path segment (the slug since
migration 056) and injects it as og:image / twitter:image / JSON-LD image
after hydration; a uuid-only lookup returned 404 for every one of those
(live 2026-09-05).
"""

from __future__ import annotations

import pytest

from app.core.http import QueryParams, Request
from app.modules.news.models.schemas import ArticleDetail
from app.modules.seo.api import routes as seo_routes

_UUID = "a74e9a07-ce56-494e-87f8-425a7c3907b6"
_SLUG = "algorand-foundation-launches-new-tool"


def _detail() -> ArticleDetail:
    return ArticleDetail(
        article_id=_UUID,
        service_id="svc",
        title="Algorand Foundation Launches New Tool",
        summary="A concise summary.",
        body="Body text.",
        published_at_epoch=1_750_000_000,
        tags=["sdk"],
        slug=_SLUG,
    )


def _req(segment: str) -> Request:
    return Request(
        method="GET",
        headers={},
        query_params=QueryParams({}),  # type: ignore[arg-type]
        path_params={"article_id": segment},
    )


def _wire(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    fetched: list[str] = []

    def fake_get_article(
        article_id: str, lang: str | None = None, **_: object
    ) -> ArticleDetail | None:
        _ = lang
        fetched.append(article_id)
        return _detail() if article_id == _UUID else None

    monkeypatch.setattr(seo_routes.news, "get_article", fake_get_article)
    monkeypatch.setattr(
        seo_routes.news, "resolve_slug", lambda slug: _UUID if slug == _SLUG else None
    )
    # Skip the real PNG render + Redis: the route's contract under test is
    # the id resolution, not the pixels.
    monkeypatch.setattr("app.core.cache.cached_bytes", lambda _k, _t, _c: b"\x89PNG-fake")
    return fetched


@pytest.mark.parametrize("segment", [f"{_SLUG}.png", _SLUG, f"{_UUID}.png", _UUID])
def test_og_article_card_resolves_slug_and_uuid(
    monkeypatch: pytest.MonkeyPatch, segment: str
) -> None:
    """Slug and uuid forms (with or without .png) all resolve to ONE detail fetch by uuid and serve the PNG."""
    fetched = _wire(monkeypatch)

    resp = seo_routes.og_article_card(_req(segment))

    assert resp.status_code == 200, segment
    assert resp.headers["Content-Type"] == "image/png"
    assert fetched == [_UUID]


def test_og_article_card_unknown_segment_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """A segment that is neither a known slug nor a uuid still 404s (no fallback card for junk)."""
    _wire(monkeypatch)

    resp = seo_routes.og_article_card(_req("no-such-article.png"))

    assert resp.status_code == 404

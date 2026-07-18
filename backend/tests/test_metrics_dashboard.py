from __future__ import annotations

from app.modules.metrics.services.dashboard_service import MetricsDashboardService
from app.modules.news.services.news_service import NewsService
from app.modules.news.stores.base import StoredArticle
from app.modules.news.stores.memory import InMemoryArticleStore


def test_dashboard_includes_articles_tile(monkeypatch) -> None:
    store = InMemoryArticleStore()
    store.insert(
        StoredArticle(
            article_id="1",
            service_id="svc",
            title="T",
            summary="S",
            body="B",
            published_at_epoch=1,
        )
    )
    news = NewsService(store=store)
    monkeypatch.setattr(
        "app.modules.metrics.services.dashboard_service.fetch_algod_status",
        lambda **kwargs: {"last-round": 12345, "time-since-last-round": 3_000_000_000},
    )
    monkeypatch.setattr(
        "app.modules.metrics.services.price_service.load_price_brief",
        lambda asset_id: None,
    )
    monkeypatch.setattr(
        "app.modules.metrics.services.dashboard_service.load_latest_price_sample",
        lambda asset_id: None,
    )

    service = MetricsDashboardService(news_service=news)
    result = service.get_dashboard(asset_id="algorand")

    ids = {tile.id for tile in result.tiles}
    assert "articles" in ids
    assert "last_round" in ids
    articles_tile = next(t for t in result.tiles if t.id == "articles")
    assert articles_tile.value == "1"
    last_round_tile = next(t for t in result.tiles if t.id == "last_round")
    assert last_round_tile.value == "12,345"
    assert last_round_tile.available is True
    round_latency_tile = next(t for t in result.tiles if t.id == "round_latency")
    assert round_latency_tile.value == "3.0s"
    assert round_latency_tile.available is True

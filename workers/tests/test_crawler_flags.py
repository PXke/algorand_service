from app.modules.scraper.crawler_registry import crawl_disabled_reason, is_crawler_enabled
from app.modules.scraper.crawler_types import CrawlerType


def test_browser_scheme_needs_spa(monkeypatch):
    monkeypatch.setenv("CRAWLER_WEB_SPA_ENABLED", "0")
    assert crawl_disabled_reason("browser://https://app.example.com/dashboard") == (
        "crawler_web_spa_disabled"
    )


def test_youtube_env_enable(monkeypatch):
    monkeypatch.setenv("CRAWLER_YOUTUBE_ENABLED", "1")
    assert is_crawler_enabled(CrawlerType.YOUTUBE)

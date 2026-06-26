from app.core import config
from app.modules.scraper.crawler_registry import (
    crawl_disabled_reason,
    infer_crawler_type,
    is_crawler_enabled,
    is_web_spa_enabled,
    mail_crawl_disabled_reason,
)
from app.modules.scraper.crawler_types import CrawlerType


def test_infer_types():
    assert infer_crawler_type("https://example.com") == CrawlerType.WEB
    assert infer_crawler_type("youtube://UC_x5XG1OV2P6uZZ5FSM9Ttw") == CrawlerType.YOUTUBE


def test_web_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CRAWLER_WEB_ENABLED", raising=False)
    from app.modules.scraper.crawler_store import CrawlerConfigRow

    fake = {
        "web": CrawlerConfigRow(
            crawler_type="web",
            display_name="Web",
            description="",
            enabled=False,
        ),
    }
    monkeypatch.setattr("app.modules.scraper.crawler_registry.load_crawler_config", lambda: fake)
    assert not is_crawler_enabled(CrawlerType.WEB)
    assert crawl_disabled_reason("https://example.com") == "crawler_web_disabled"


def test_web_spa_sub_flag(monkeypatch):
    monkeypatch.setenv("CRAWLER_WEB_SPA_ENABLED", "0")
    monkeypatch.setattr(config, "CRAWLER_HTTP_ENABLED", True)
    assert not is_web_spa_enabled()
    assert crawl_disabled_reason("browser://https://spa.example.com") == "crawler_web_spa_disabled"


def test_mail_disabled(monkeypatch):
    monkeypatch.setenv("CRAWLER_MAIL_ENABLED", "0")
    assert mail_crawl_disabled_reason() == "crawler_mail_disabled"

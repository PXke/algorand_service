from app.modules.scraper.core.telegram_web_scraper import (
    parse_telegram_preview_messages,
)
from app.modules.scraper.core.telegram_urls import resolve_telegram_preview_url


SAMPLE_HTML = """
<html><body>
<div class="tgme_widget_message_wrap">
  <time datetime="2026-06-01T12:00:00+00:00"></time>
  <div class="tgme_widget_message_text">Community call on Friday</div>
</div>
<div class="tgme_widget_message_wrap">
  <time datetime="2026-06-02T09:00:00+00:00"></time>
  <div class="tgme_widget_message_text">SDK v2 released</div>
</div>
</body></html>
"""


def test_resolve_preview_url() -> None:
    assert resolve_telegram_preview_url("telegram://s/algorand") == "https://t.me/s/algorand"
    assert resolve_telegram_preview_url("telegram://@algorand") == "https://t.me/s/algorand"


def test_parse_preview_messages() -> None:
    lines = parse_telegram_preview_messages(SAMPLE_HTML)
    assert len(lines) == 2
    assert "Community call" in lines[0]
    assert "SDK" in lines[1]

"""fetch_url scrolling via continue_reading (no public start_char API)."""

from typing import ClassVar

import pytest

from app.modules.ai import writer_tools as wt
from app.modules.ai.research_tools import (
    _fetch_url_internal,
    _publicize_fetch_result,
    _slice_document_text,
    _tool_fetch_url,
)


def test_slice_document_text_first_window_metadata() -> None:
    """Returns truncated/has_more scroll metadata (not start_char) for the first window of a long document."""
    text = "A" * 10_000
    out = _slice_document_text(
        text,
        url="https://example.com/spec",
        title="Spec",
        links=[{"text": "anchor", "url": "https://example.com/#x"}],
        max_chars=6000,
        offset=0,
    )
    assert out["chunk_chars"] == 6000
    assert out["chars"] == 10_000
    assert out["truncated"] is True
    assert out["has_more"] is True
    assert out["_next_offset"] == 6000
    assert out["links"]
    pub = _publicize_fetch_result(out)
    assert pub["scroll"]["continue_reading"] is True
    assert "start_char" not in pub
    assert "next_start_char" not in pub


def test_slice_document_text_second_window_omits_links() -> None:
    """Omits links and reports no further scrolling on the final window of a document."""
    text = "A" * 10_000
    out = _slice_document_text(
        text,
        url="https://example.com/spec",
        title="Spec",
        links=[{"text": "anchor", "url": "https://example.com/#x"}],
        max_chars=6000,
        offset=6000,
    )
    assert out["text"] == "A" * 4000
    assert out["truncated"] is False
    assert out["has_more"] is False
    assert out["_next_offset"] is None
    assert out["links"] == []


def test_fetch_url_internal_offset_scrolls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fetches the next chunk of a long page using the offset returned by the prior chunk."""
    html = f"<html><head><title>Doc</title></head><body>{'B' * 15_000}</body></html>"

    class _Resp:
        status_code = 200
        url = "https://example.com/long"
        headers: ClassVar[dict[str, str]] = {"content-type": "text/html"}
        text = html

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get",
        lambda *_a, **_k: _Resp(),
    )
    monkeypatch.setattr(
        "app.modules.scraper.crawler_registry.is_web_spa_enabled",
        lambda: False,
    )

    first = _fetch_url_internal("https://example.com/long", max_chars=5000, offset=0)
    second = _fetch_url_internal(
        "https://example.com/long",
        max_chars=5000,
        offset=first["_next_offset"],
    )
    assert len(first["text"]) == 5000
    assert second["_next_offset"] == 10_000 or len(second["text"]) == 5000


def test_continue_reading_wrap_tracks_scroll_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tracks per-call scroll state so continue_reading=True fetches the next chunk without a bare handler call."""
    html = f"<html><head><title>Doc</title></head><body>{'C' * 12_000}</body></html>"

    class _Resp:
        status_code = 200
        url = "https://example.com/long"
        headers: ClassVar[dict[str, str]] = {"content-type": "text/html"}
        text = html

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get",
        lambda *_a, **_k: _Resp(),
    )
    monkeypatch.setattr(
        "app.modules.scraper.crawler_registry.is_web_spa_enabled",
        lambda: False,
    )

    ctx: dict = {}
    handler = wt._wrap_fetch_url_scroll(
        lambda **_kw: (_ for _ in ()).throw(AssertionError("should not call bare handler")),
        ctx,
    )
    # scroll wrap calls _fetch_url_internal directly
    first = handler(url="https://example.com/long", max_chars=5000)
    assert first["has_more"] is True
    assert first["scroll"]["continue_reading"] is True
    second = handler(url="https://example.com/long", max_chars=5000, continue_reading=True)
    assert len(second["text"]) == 5000
    assert second["has_more"] is True


def test_continue_reading_wrap_applies_the_spa_notfound_augmentation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (2026-09-08, Fable audit): the scroll wrapper used to call _fetch_url_internal directly and skip every fetch_url safety augmentation entirely -- in the real compose path (which always goes through this wrapper), the SPA-not-found warning (lumirogue, 2026-08-10/12) and the github-archived-but-owner-still-shipping warning (Pera Wallet, 2026-07-20) never actually fired; only tests calling _tool_fetch_url directly ever exercised them."""
    html = (
        "<html><head><title>404</title></head><body>"
        'Page Not Found\nThe page "terms" could not be found in this application.'
        "</body></html>"
    )

    class _Resp:
        status_code = 200
        url = "https://lumirogue.com/terms"
        headers: ClassVar[dict[str, str]] = {"content-type": "text/html"}
        text = html

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get",
        lambda *_a, **_k: _Resp(),
    )
    monkeypatch.setattr(
        "app.modules.scraper.crawler_registry.is_web_spa_enabled",
        lambda: False,
    )

    handler = wt._wrap_fetch_url_scroll(
        lambda **_kw: (_ for _ in ()).throw(AssertionError("should not call bare handler")),
        {},
    )
    result = handler(url="https://lumirogue.com/terms", max_chars=5000)
    assert result["text"].startswith("[CLIENT-SIDE ROUTE CHECK]")
    assert "click_element" in result["text"]


def test_fetch_url_past_end_hints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns empty text with a hint when the requested offset is past the end of a short page."""
    html = "<html><body>short</body></html>"

    class _Resp:
        status_code = 200
        url = "https://example.com/short"
        headers: ClassVar[dict[str, str]] = {"content-type": "text/html"}
        text = html

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get",
        lambda *_a, **_k: _Resp(),
    )
    monkeypatch.setattr(
        "app.modules.scraper.crawler_registry.is_web_spa_enabled",
        lambda: False,
    )
    out = _tool_fetch_url("https://example.com/short", offset=9999)
    assert out["text"] == ""
    assert "hint" in out

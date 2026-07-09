"""fetch_url scrolling via continue_reading (no public start_char API)."""

from types import SimpleNamespace

from app.modules.ai.research_tools import (
    _fetch_url_internal,
    _publicize_fetch_result,
    _slice_document_text,
    _tool_fetch_url,
)
from app.modules.ai import writer_tools as wt


def test_slice_document_text_first_window_metadata() -> None:
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


def test_fetch_url_internal_offset_scrolls(monkeypatch) -> None:
    html = f"<html><head><title>Doc</title></head><body>{'B' * 15_000}</body></html>"

    class _Resp:
        status_code = 200
        url = "https://example.com/long"
        headers = {"content-type": "text/html"}
        text = html

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get",
        lambda *a, **k: _Resp(),
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


def test_continue_reading_wrap_tracks_scroll_state(monkeypatch) -> None:
    html = f"<html><head><title>Doc</title></head><body>{'C' * 12_000}</body></html>"

    class _Resp:
        status_code = 200
        url = "https://example.com/long"
        headers = {"content-type": "text/html"}
        text = html

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get",
        lambda *a, **k: _Resp(),
    )
    monkeypatch.setattr(
        "app.modules.scraper.crawler_registry.is_web_spa_enabled",
        lambda: False,
    )

    ctx: dict = {}
    handler = wt._wrap_fetch_url_scroll(
        lambda **kw: (_ for _ in ()).throw(AssertionError("should not call bare handler")),
        ctx,
    )
    # scroll wrap calls _fetch_url_internal directly
    first = handler(url="https://example.com/long", max_chars=5000)
    assert first["has_more"] is True
    assert first["scroll"]["continue_reading"] is True
    second = handler(url="https://example.com/long", max_chars=5000, continue_reading=True)
    assert len(second["text"]) == 5000
    assert second["has_more"] is True


def test_fetch_url_past_end_hints(monkeypatch) -> None:
    html = "<html><body>short</body></html>"

    class _Resp:
        status_code = 200
        url = "https://example.com/short"
        headers = {"content-type": "text/html"}
        text = html

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get",
        lambda *a, **k: _Resp(),
    )
    monkeypatch.setattr(
        "app.modules.scraper.crawler_registry.is_web_spa_enabled",
        lambda: False,
    )
    out = _tool_fetch_url("https://example.com/short", offset=9999)
    assert out["text"] == ""
    assert "hint" in out

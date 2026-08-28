"""Unsafe HTML is stripped from article bodies by the nh3-based sanitizer."""

from __future__ import annotations

from app.core.sanitize import sanitize_markdown_body


def test_sanitize_strips_script_tags() -> None:
    """Sanitizing a body removes script tags (and their content) while keeping surrounding text."""
    raw = "Hello<script>alert(1)</script> world"
    cleaned = sanitize_markdown_body(raw)
    assert "<script" not in cleaned
    assert "alert(1)" not in cleaned
    assert "Hello" in cleaned
    assert "world" in cleaned


def test_sanitize_strips_style_tags() -> None:
    """Style tags are removed along with their content, same as script."""
    cleaned = sanitize_markdown_body("<style>body{display:none}</style>visible text")
    assert "<style" not in cleaned
    assert "display:none" not in cleaned
    assert "visible text" in cleaned


def test_sanitize_strips_event_handler_attributes() -> None:
    """An onerror/onclick/... attribute never survives, even on an otherwise-allowed tag."""
    cleaned = sanitize_markdown_body('<img src="https://example.com/x.png" onerror="alert(1)">')
    assert "onerror" not in cleaned
    assert "alert(1)" not in cleaned
    assert cleaned == '<img src="https://example.com/x.png">'


def test_sanitize_strips_javascript_url_scheme() -> None:
    """A javascript: href is dropped entirely -- only http(s)/mailto survive."""
    cleaned = sanitize_markdown_body('<a href="javascript:alert(1)">click</a>')
    assert "javascript:" not in cleaned
    assert "click" in cleaned


def test_sanitize_strips_iframe_but_keeps_its_text() -> None:
    """A disallowed wrapper tag (iframe) is removed; it must never delete real prose."""
    cleaned = sanitize_markdown_body('<iframe src="https://evil.example">fallback text</iframe>')
    assert "<iframe" not in cleaned
    assert "fallback text" in cleaned


def test_sanitize_keeps_allowlisted_markdown_html() -> None:
    """Tags that a normally-rendered GFM article body can legitimately contain survive untouched."""
    raw = "<p>Hello <strong>world</strong>, see <a href='https://example.com'>this</a>.</p>"
    cleaned = sanitize_markdown_body(raw)
    assert "<strong>world</strong>" in cleaned
    assert 'href="https://example.com"' in cleaned


def test_sanitize_leaves_plain_markdown_untouched() -> None:
    """Ordinary Markdown source with no embedded HTML passes through unchanged."""
    raw = "# Heading\n\nSome **bold** prose with a [link](https://example.com)."
    assert sanitize_markdown_body(raw) == raw

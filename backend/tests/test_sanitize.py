"""Unsafe HTML is stripped from article bodies."""

from __future__ import annotations

from app.core.sanitize import sanitize_markdown_body


def test_sanitize_strips_script_tags() -> None:
    """Sanitizing a body removes script tags while keeping surrounding text."""
    raw = "Hello<script>alert(1)</script> world"
    assert "<script" not in sanitize_markdown_body(raw)
    assert "Hello" in sanitize_markdown_body(raw)

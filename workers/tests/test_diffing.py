from __future__ import annotations

from app.modules.pipeline.core.diffing import build_text_diff, normalize_text


def test_normalize_text_collapses_whitespace() -> None:
    assert normalize_text("  hello   world  \n\tfoo  bar ") == "hello world\nfoo bar"


def test_build_text_diff_ignores_whitespace_only_changes() -> None:
    diff = build_text_diff("hello  world", "hello world")
    assert diff == ""

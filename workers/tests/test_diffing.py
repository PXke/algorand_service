from __future__ import annotations

from app.modules.pipeline.core.diffing import build_text_diff, normalize_text


def test_normalize_text_collapses_whitespace() -> None:
    assert normalize_text("  hello   world  \n\tfoo  bar ") == "hello world\nfoo bar"


def test_build_text_diff_ignores_whitespace_only_changes() -> None:
    diff = build_text_diff("hello  world", "hello world")
    assert diff == ""


def test_build_text_diff_marks_truncation() -> None:
    previous = "line\n" * 5
    current = "\n".join(f"new line {i}" for i in range(500))
    diff = build_text_diff(previous, current, max_lines=50)
    lines = diff.splitlines()
    assert len(lines) == 51  # 50 kept + the marker
    assert lines[-1].startswith("... (") and lines[-1].endswith(" more diff lines omitted)")


def test_build_text_diff_no_marker_when_not_truncated() -> None:
    diff = build_text_diff("old line", "new line", max_lines=200)
    assert "omitted" not in diff

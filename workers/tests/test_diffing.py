"""Whitespace-normalized diffing and truncation markers."""

from __future__ import annotations

from app.modules.pipeline.core.diffing import build_text_diff, normalize_text


def test_normalize_text_collapses_whitespace() -> None:
    """Collapses runs of whitespace to single spaces, keeping line breaks."""
    assert normalize_text("  hello   world  \n\tfoo  bar ") == "hello world\nfoo bar"


def test_build_text_diff_ignores_whitespace_only_changes() -> None:
    """Ignores a diff that only differs in whitespace."""
    diff = build_text_diff("hello  world", "hello world")
    assert diff == ""


def test_build_text_diff_marks_truncation() -> None:
    """Marks a truncated diff with a trailing count-of-omitted-lines marker."""
    previous = "line\n" * 5
    current = "\n".join(f"new line {i}" for i in range(500))
    diff = build_text_diff(previous, current, max_lines=50)
    lines = diff.splitlines()
    assert len(lines) == 51  # 50 kept + the marker
    assert lines[-1].startswith("... (")
    assert lines[-1].endswith(" more diff lines omitted)")


def test_build_text_diff_no_marker_when_not_truncated() -> None:
    """Omits the truncation marker when the diff fits within max_lines."""
    diff = build_text_diff("old line", "new line", max_lines=200)
    assert "omitted" not in diff


def test_default_max_lines_matches_config() -> None:
    """The default cap comes from DIFF_MAX_LINES, not a hardcoded literal -- root-caused 2026-08-04 (vestige.fi): a 200-line default silently dropped 89% of a real 1,773-line diff before the writer ever saw it."""
    from app.core.config import DIFF_MAX_LINES

    previous = "line\n" * 5
    current = "\n".join(f"new line {i}" for i in range(DIFF_MAX_LINES + 50))
    diff = build_text_diff(previous, current)
    lines = diff.splitlines()
    assert len(lines) == DIFF_MAX_LINES + 1  # capped content + the marker
    assert lines[-1].startswith("... (")

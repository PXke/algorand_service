from app.modules.newspaper.editorial_briefs import (
    brief_matches_text,
    format_briefs_for_writer,
    EditorialBriefMatch,
)


def test_brief_matches_keywords_in_haystack():
    assert brief_matches_text(keywords="algoblow, scam", haystack="Warning about algoblow.com")
    assert not brief_matches_text(keywords="nft", haystack="weekly digest")


def test_format_briefs_for_writer_empty():
    assert format_briefs_for_writer([]) == ""


def test_format_briefs_for_writer_includes_title():
    block = format_briefs_for_writer(
        [
            EditorialBriefMatch(
                brief_id="abc",
                title="Cover algoblow",
                body_markdown="Mention D13 tweet.",
            )
        ]
    )
    assert "Cover algoblow" in block
    assert "abc" in block

"""fetch_url's bot-challenge detection: an anti-bot challenge page (Cloudflare and similar) is turned into an explicit error instead of a citable "successful" result.

Root-caused 2026-09-08 (AlgoChess three-way recompose comparison, Fable
review): a fetch of allo.info got WAF-challenged, and the challenge page's
own <title> ("Just a moment...") got picked up as a citation LABEL by
reference_block.py's fetched_sources -- a published article cited a
real-looking source whose visible link text was a bot-detection splash
screen.
"""

from __future__ import annotations

from app.modules.ai.research_tools import _augment_bot_challenge_error, _augment_fetch_result


def test_augment_bot_challenge_error_flags_a_short_challenge_page() -> None:
    """A short page titled like a known anti-bot challenge becomes an explicit error."""
    result = {
        "url": "https://allo.info/application/3693252574",
        "title": "Just a moment...",
        "text": "Enable JavaScript and cookies to continue",
    }
    out = _augment_bot_challenge_error("https://allo.info/application/3693252574", dict(result))
    assert "error" in out
    assert "Just a moment" in out["error"]
    assert "title" not in out


def test_augment_bot_challenge_error_noop_on_a_real_page_with_a_similar_title() -> None:
    """A real, substantial page whose title happens to share generic wording is left alone -- matched on title AND a short body, not title alone."""
    result = {
        "url": "https://example.com/article",
        "title": "Attention Required for New Readers: A Long-Form Essay",
        "text": "x" * 500,
    }
    out = _augment_bot_challenge_error("https://example.com/article", dict(result))
    assert out == result


def test_augment_bot_challenge_error_noop_on_an_ordinary_page() -> None:
    """A normal page with no challenge-shaped title is untouched."""
    result = {"url": "https://algochess.org", "title": "AlgoChess", "text": "x" * 500}
    out = _augment_bot_challenge_error("https://algochess.org", dict(result))
    assert out == result


def test_augment_bot_challenge_error_handles_missing_title() -> None:
    """No title field at all -- fails open, no crash."""
    result = {"url": "https://example.com", "text": "some text"}
    out = _augment_bot_challenge_error("https://example.com", dict(result))
    assert out == result


def test_augment_fetch_result_short_circuits_on_a_bot_challenge() -> None:
    """The shared augmentation pipeline stops at the bot-challenge check -- a challenge page never reaches the SPA-notfound/github-archived augmentations, which expect real page text."""
    result = {
        "url": "https://allo.info/application/3693252574",
        "title": "Just a moment...",
        "text": "",
    }
    out = _augment_fetch_result("https://allo.info/application/3693252574", dict(result))
    assert "error" in out
    assert "text" not in out

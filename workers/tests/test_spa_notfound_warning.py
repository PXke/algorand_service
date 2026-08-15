"""fetch_url's SPA-router 404 warning: a guessed bare URL (e.g. /terms) on a single-page app renders the app's own 'not found' shell, which is not proof a real on-page link/button is broken. Root-caused 2026-08-10 (lumirogue.com 'About') and recurred 2026-08-12 ('Terms of use')."""

from __future__ import annotations

from app.modules.ai.research_tools import _augment_spa_notfound_warning, _fetch_failure_hint


def test_augment_spa_notfound_warning_flags_client_router_shell() -> None:
    """A page whose own text matches the client-router not-found phrasing gets a prepended warning."""
    result = {
        "url": "https://lumirogue.com/terms",
        "text": '404\nPage Not Found\nThe page "terms" could not be found in this application.\nGo Home',
    }
    out = _augment_spa_notfound_warning(dict(result))
    assert out["text"].startswith("[CLIENT-SIDE ROUTE CHECK]")
    assert "click_element" in out["text"]
    # Original page text is preserved, not discarded.
    assert "could not be found in this application" in out["text"]


def test_augment_spa_notfound_warning_noop_on_normal_page() -> None:
    """An ordinary successful page fetch is untouched."""
    result = {"url": "https://lumirogue.com", "text": "LUMI ROGUE v0.21\nToo much left undone to stay dead."}
    out = _augment_spa_notfound_warning(dict(result))
    assert out == result


def test_augment_spa_notfound_warning_handles_missing_text_field() -> None:
    """No text field at all -- fails open, no crash."""
    result = {"url": "https://example.com"}
    out = _augment_spa_notfound_warning(dict(result))
    assert out == result


def test_fetch_failure_hint_404_mentions_guessed_url_caveat() -> None:
    """A real HTTP 404 hint now also warns that a guessed bare URL 404 doesn't prove a link/button is broken."""
    hint = _fetch_failure_hint("https://lumirogue.com/terms", "404 Not Found", status_code=404)
    assert "click_element" in hint
    assert "fetch_archive_text" in hint

"""save_screenshot persists a captured PNG outside any release dir (must survive deploys) and returns its public URL, content-addressed so repeated captures of the same state dedupe on disk."""

from __future__ import annotations

import hashlib

import pytest

from app.modules.scraper.core.browser_scrape import save_screenshot


def test_save_screenshot_returns_none_when_storage_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty SCREENSHOT_STORAGE_DIR is a deliberate kill switch."""
    monkeypatch.setattr("app.core.config.SCREENSHOT_STORAGE_DIR", "")
    assert save_screenshot(b"fake-png") is None


def test_save_screenshot_writes_content_addressed_file(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file is named by the sha256 of its bytes, and the returned URL matches SCREENSHOT_PUBLIC_BASE_URL."""
    png = b"fake-png-bytes-for-test"
    monkeypatch.setattr("app.core.config.SCREENSHOT_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.core.config.SCREENSHOT_PUBLIC_BASE_URL", "https://algorand.pxke.me/media/screenshots"
    )

    url = save_screenshot(png)

    digest = hashlib.sha256(png).hexdigest()
    assert url == f"https://algorand.pxke.me/media/screenshots/{digest}.png"
    saved_path = tmp_path / f"{digest}.png"  # type: ignore[operator]
    assert saved_path.exists()
    assert saved_path.read_bytes() == png


def test_save_screenshot_dedupes_identical_content(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Capturing the same page state twice reuses the same file instead of writing a duplicate."""
    png = b"identical-bytes"
    monkeypatch.setattr("app.core.config.SCREENSHOT_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.core.config.SCREENSHOT_PUBLIC_BASE_URL", "https://algorand.pxke.me/media/screenshots"
    )

    url1 = save_screenshot(png)
    url2 = save_screenshot(png)

    assert url1 == url2
    files = list(tmp_path.iterdir())  # type: ignore[attr-defined]
    assert len(files) == 1


def test_save_screenshot_returns_none_on_write_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A storage-layer failure (permissions, disk full) degrades to None, never raises -- a failed illustration must not abort a compose."""
    monkeypatch.setattr("app.core.config.SCREENSHOT_STORAGE_DIR", "/nonexistent/\x00/bad/path")
    assert save_screenshot(b"fake-png") is None

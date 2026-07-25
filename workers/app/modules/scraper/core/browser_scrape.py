"""Playwright-backed page fetch and visible-text extraction."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.core import config

if TYPE_CHECKING:
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_LOGIN_MARKERS = (
    "log in",
    "login",
    "sign in",
    "sign up to continue",
    "create an account",
    "cookies",
    "captcha",
)


@dataclass(frozen=True)
class BrowserPageResult:
    """A Playwright-fetched page's extracted content."""
    title: str
    text: str
    final_url: str
    engine: str
    html: str = ""


class BrowserScrapeError(Exception):
    """Raised when a browser-backed fetch fails."""
    pass


def fetch_page(
    url: str,
    *,
    wait_after_load_ms: int | None = None,
    timeout_ms: int | None = None,
    storage_state_path: str | None = None,
) -> BrowserPageResult:
    """Load a hard target (SPA / heavy JS) with Playwright Chromium.

    Python stack standard — Puppeteer/Selenium are not required.
    """
    wait_ms = wait_after_load_ms if wait_after_load_ms is not None else config.BROWSER_WAIT_MS
    timeout = timeout_ms if timeout_ms is not None else config.BROWSER_TIMEOUT_MS
    state_path = storage_state_path or config.BROWSER_STORAGE_STATE_PATH

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        msg = "playwright package not installed"
        raise BrowserScrapeError(msg) from exc

    launch_kwargs: dict[str, object] = {"headless": config.BROWSER_HEADLESS}
    channel = (config.BROWSER_CHANNEL or "").strip()
    if channel:
        launch_kwargs["channel"] = channel

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launch_kwargs)
        try:
            context_kwargs: dict = {"user_agent": _BROWSER_UA}
            if state_path and Path(state_path).is_file():
                context_kwargs["storage_state"] = state_path
                logger.info("browser scrape using storage_state=%s", state_path)

            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            # Block navigation to internal/private targets (SSRF) before load.
            from app.core.net_guard import assert_public_url

            assert_public_url(url)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            if wait_ms > 0:
                page.wait_for_timeout(wait_ms)
            title = page.title() or ""
            text = _extract_visible_text(page)
            html = page.content()
            final_url = page.url
            context.close()
        finally:
            browser.close()

    cleaned = _clean_extracted_text(text)
    if _looks_like_login_wall(cleaned, title) and not (state_path and Path(state_path).is_file()):
        raise BrowserScrapeError(
            "browser page looks like a login or gate — use push ingest, mail, or "
            "BROWSER_STORAGE_STATE_PATH for an allowlisted session you control"
        )

    if len(cleaned) < 80:
        raise BrowserScrapeError("browser page had insufficient visible text")

    return BrowserPageResult(
        title=title.strip(),
        text=cleaned,
        final_url=final_url,
        engine="playwright",
        html=html,
    )


def _extract_visible_text(page: Page) -> str:
    """Prefer main landmarks; fall back to full body text."""
    selectors = (
        "main",
        "article",
        "[role='main']",
        "#content",
        ".content",
    )
    chunks: list[str] = []
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() > 0:
            try:
                chunk = locator.first.inner_text(timeout=2000)
            except Exception:
                continue
            if chunk and len(chunk.strip()) > 100:
                chunks.append(chunk.strip())
    if chunks:
        return "\n\n".join(chunks)
    return page.inner_text("body")


def _clean_extracted_text(text: str) -> str:
    lines: list[str] = []
    prev = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) < 2:
            continue
        if line == prev:
            continue
        if len(line) > 2000:
            line = line[:2000] + "…"
        lines.append(line)
        prev = line
    return "\n".join(lines[-200:])


def _looks_like_login_wall(text: str, title: str) -> bool:
    blob = f"{title}\n{text[:2500]}".lower()
    hits = sum(1 for marker in _LOGIN_MARKERS if marker in blob)
    # Login pages are often very short with multiple auth phrases
    if hits >= 2 and len(text) < 2500:
        return True
    return "log in to discord" in blob or "login to discord" in blob


def resolve_browser_target_url(scrape_url: str) -> str | None:
    """Map registry URL to https URL for Playwright."""
    raw = scrape_url.strip()
    if raw.startswith("browser://"):
        return raw[len("browser://") :].strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return None

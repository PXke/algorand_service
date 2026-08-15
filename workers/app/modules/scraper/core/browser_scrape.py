"""Playwright-backed page fetch and visible-text extraction."""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.core import config

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

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

# Substrings that show up in the hostnames of real Algorand algod/indexer
# endpoints (algonode.cloud, nodely.dev, and their various self-hosted
# mirrors all follow this mainnet-*/testnet-* naming convention). Matched
# case-insensitively against the FULL hostname a page's own network
# requests actually went to -- ground truth for which network a dapp is
# really wired to, immune to stale/wrong page copy (root-caused 2026-08-13:
# lumirogue.com's own UI text said "Algorand Testnet" while its wallet
# code was hardcoded to mainnet, chainId 416001 -- a claim built from that
# text alone was backwards).
_MAINNET_HOST_MARKERS = ("mainnet-api", "mainnet-idx", "mainnet.algorand")
_TESTNET_HOST_MARKERS = ("testnet-api", "testnet-idx", "testnet.algorand")


def _classify_network_hosts(hosts: set[str]) -> dict[str, object]:
    """Best-effort mainnet/testnet call from a set of hostnames a page's own network requests actually hit."""
    mainnet_hits = sorted(h for h in hosts if any(m in h.lower() for m in _MAINNET_HOST_MARKERS))
    testnet_hits = sorted(h for h in hosts if any(m in h.lower() for m in _TESTNET_HOST_MARKERS))
    if mainnet_hits and not testnet_hits:
        network = "mainnet"
    elif testnet_hits and not mainnet_hits:
        network = "testnet"
    elif mainnet_hits and testnet_hits:
        network = "ambiguous"  # both seen -- e.g. a wallet library's own fallback config, or a genuine dual-network app
    else:
        network = "unknown"  # no recognized algod/indexer host observed at all
    return {
        "detected_network": network,
        "mainnet_hosts": mainnet_hits,
        "testnet_hosts": testnet_hits,
        "all_hosts": sorted(hosts),
    }


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
    skip_login_wall_check: bool = False,
) -> BrowserPageResult:
    """Load a hard target (SPA / heavy JS) with Playwright Chromium.

    Python stack standard — Puppeteer/Selenium are not required.

    skip_login_wall_check: for a specific, pre-vetted target URL (e.g. a
    known NFT marketplace's public collection page) where ordinary "Sign
    In" nav chrome would otherwise false-positive the login-wall heuristic
    below -- confirmed live 2026-08-10 on exa.market/collections, a fully
    public page. Only set this for a caller that already knows the target
    is public; leave it False for anything reached via a general-purpose
    fetch where the heuristic's protection (don't let the writer cite
    auth-gated content as real) still matters.
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
            # domcontentloaded fires on raw HTML parse, before a page's OWN
            # async fetch()/XHR calls (e.g. an API-backed gallery/stats
            # widget) resolve -- extracting text right after it can capture
            # a genuinely inconsistent in-between state: a "loading..."
            # placeholder AND a hidden "empty" fallback message both present
            # at once, neither reflecting the page's real, settled content
            # (root-caused live 2026-08-10: pixelcity.aetheralabs.es's
            # gallery fetches /api/gallery client-side; the scrape landed
            # mid-fetch and captured its display:none "no works minted yet"
            # placeholder as if genuinely shown, producing a fabricated
            # "the site's own gallery disagrees with the chain" narrative
            # for a gallery that actually renders 246 real NFTs once its
            # fetch completes). Best-effort and capped short: a page with
            # persistent background chatter (websockets, polling, ads) would
            # never go idle, and this must never turn into an extra ~timeout
            # seconds of dead wait on every ordinary page -- the fixed
            # wait_ms fallback below still runs regardless as a floor.
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout, 8_000))
            except Exception:
                logger.debug("networkidle wait timed out for %s; continuing", url)
            if wait_ms > 0:
                page.wait_for_timeout(wait_ms)
            _expand_collapsed_content(page)
            title = page.title() or ""
            text = _extract_visible_text(page)
            html = page.content()
            final_url = page.url
            context.close()
        finally:
            browser.close()

    cleaned = _clean_extracted_text(text)
    if (
        not skip_login_wall_check
        and _looks_like_login_wall(cleaned, title)
        and not (state_path and Path(state_path).is_file())
    ):
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


def click_and_read(
    url: str,
    click_text: str,
    *,
    wait_after_click_ms: int = 1500,
    timeout_ms: int | None = None,
) -> BrowserPageResult:
    """Load url, click the first visible element whose text matches click_text, and return the page's content AFTER the click.

    For content that only appears via a JS-driven action (an in-page modal,
    a non-standard toggle, a tab switch) rather than a real navigable URL --
    fetch_page (and fetch_url) can only follow real hrefs. Root-caused
    2026-08-10: an article described lumirogue.com's footer 'About
    this project' / 'Terms of use' as broken links returning 404 -- the
    guessed /about and /terms URLs genuinely do 404, but that's not what a
    real visitor experiences: the footer items are BUTTONS with no href at
    all, and clicking either opens a working in-page modal with real
    content. fetch_page has no way to discover that; this does.
    """
    timeout = timeout_ms if timeout_ms is not None else config.BROWSER_TIMEOUT_MS
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        msg = "playwright package not installed"
        raise BrowserScrapeError(msg) from exc

    from app.core.net_guard import assert_public_url

    assert_public_url(url)

    launch_kwargs: dict[str, object] = {"headless": config.BROWSER_HEADLESS}
    channel = (config.BROWSER_CHANNEL or "").strip()
    if channel:
        launch_kwargs["channel"] = channel

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launch_kwargs)
        try:
            context = browser.new_context(user_agent=_BROWSER_UA)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout, 8_000))
            except Exception:
                logger.debug("networkidle wait timed out for %s; continuing", url)

            locator = _locate_clickable(page, click_text)
            if locator is None:
                clickable = _sample_clickable_texts(page)
                msg = (
                    f"no element with text matching {click_text!r} found -- "
                    f"visible clickable text on the page includes: {clickable}"
                )
                raise BrowserScrapeError(msg)
            _click_robust(locator)
            page.wait_for_timeout(wait_after_click_ms)
            _expand_collapsed_content(page)
            title = page.title() or ""
            text = _extract_visible_text(page)
            html = page.content()
            final_url = page.url
            context.close()
        finally:
            browser.close()

    cleaned = _clean_extracted_text(text)
    if len(cleaned) < 80:
        raise BrowserScrapeError("page had insufficient visible text after the click")

    return BrowserPageResult(
        title=title.strip(),
        text=cleaned,
        final_url=final_url,
        engine="playwright-click",
        html=html,
    )


def _sample_clickable_texts(page: Page, limit: int = 25) -> list[str]:
    """Short list of visible button/link text on the page — lets a failed click_and_read tell the caller what WAS clickable, instead of a bare 'not found'."""
    try:
        texts = page.eval_on_selector_all(
            "button, a, [role=button], [onclick]",
            "els => els.map(e => (e.innerText || '').trim()).filter(t => t.length > 0 && t.length < 60)",
        )
        return texts[:limit]
    except Exception:
        return []


def _locate_clickable(page: Page, click_text: str) -> Locator | None:
    """The first element matching click_text by visible text, or by title/aria-label for icon-only controls with no text content at all.

    Root-caused 2026-08-11 verifying lumirogue.com's wallet-connect flow: its
    Pera/Defly/Lute buttons are icon-only (title attribute, empty innerText),
    so get_by_text alone can never find them -- every click_text lookup
    silently reported "not found" against a page that plainly had the button,
    exactly the failure mode _locate_field already solves for typed fields.
    """
    try:
        loc = page.get_by_text(click_text, exact=False).first
        if loc.count() > 0:
            return loc
    except Exception:
        pass
    try:
        loc = page.get_by_title(click_text, exact=False).first
        if loc.count() > 0:
            return loc
    except Exception:
        pass
    try:
        escaped = click_text.replace("'", "\\'")
        loc = page.locator(f"[aria-label*='{escaped}' i], [title*='{escaped}' i]").first
        if loc.count() > 0:
            return loc
    except Exception:
        pass
    return None


def _click_robust(locator: Locator, *, timeout: int = 10_000) -> None:
    """Click a locator, preferring Playwright's normal actionability checks over force=True.

    Root-caused 2026-08-11/12 verifying lumirogue.com's wallet-connect and
    demo flows: a force=True click bypasses Playwright's own "is this
    element actually the topmost thing at these coordinates" interception
    check -- it dispatches at the target's raw bounding-box center
    regardless of what's now covering it. On a page where toast
    notifications progressively stack up and drift over a button's
    position (confirmed live via elementFromPoint at the click coordinates
    resolving to a toast div, not the intended button, and independently
    via Playwright's own actionability trace: "<div ...screen-enter
    pointer-events-auto> ... subtree intercepts pointer events"), a force
    click silently clicks the WRONG element with no error at all: the
    button looked clickable, Playwright dispatched a click, nothing threw,
    but the button's own handler never ran.

    A normal (non-force) click is Playwright's own answer to exactly this
    failure mode -- it waits for the target to become the actual top
    element at its coordinates, auto-retrying as intervening elements move
    or disappear. But a PERSISTENT stack of toasts (not a transient
    animation) never clears on its own within any reasonable timeout, so a
    plain normal-click retry loop alone still fails here. If the target
    stays covered, this dismisses any visible "Close"-labeled controls
    (toast dismiss buttons are near-universally reachable this way, and
    dismissing a notification is never a destructive action) and retries
    once before finally falling back to force=True -- kept as a last
    resort for the different, narrower case a normal click doesn't handle
    well: a genuinely unstable/animating element (e.g. a repeating
    marquee) that never reports "stable" even though nothing is actually
    covering it.
    """
    try:
        locator.click(timeout=timeout)
        return
    except Exception:
        logger.debug("normal click failed/timed out; trying to dismiss intercepting overlays", exc_info=True)

    page = locator.page
    try:
        close_buttons = page.get_by_role("button", name="Close")
        for i in range(min(close_buttons.count(), 10)):
            with contextlib.suppress(Exception):
                close_buttons.nth(i).click(timeout=1_000, force=True)
    except Exception:
        logger.debug("dismissing intercepting overlays failed", exc_info=True)

    try:
        locator.click(timeout=timeout)
        return
    except Exception:
        logger.debug("click still blocked after dismissing overlays; falling back to force=True", exc_info=True)

    try:
        locator.click(force=True, timeout=timeout)
        return
    except Exception:
        logger.debug(
            "force click also failed (likely 'outside the viewport' -- a fixed-position "
            "or off-screen-until-breakpoint element Playwright's actionability checks "
            "won't scroll to); falling back to a native DOM .click(), which fires the "
            "element's real handler with no visibility/position requirement at all "
            "(root-caused 2026-08-13: lumirogue.com's 'Get an Ankh' button repeatedly "
            "failed with exactly this error across 5+ compose attempts, so its real "
            "destination — a marketplace listing — was never found)",
            exc_info=True,
        )
        locator.evaluate("el => el.click()")


def _expand_collapsed_content(page: Page) -> None:
    """Reveal accordion/FAQ-style content hidden behind a click before extracting text.

    A collapsed answer is invisible to a text read even after Playwright has
    rendered the page — JS execution alone doesn't expand it, only a user
    click (or, for <details>, the open attribute) does. Root-caused
    2026-08-06: fetch_url returned FAQ question titles with no answer text on
    lending.algoanna.com/faq, because the page wasn't "thin" by character
    count at all — the titles alone were plenty of text, just not the actual
    content. Targets the two markup patterns almost every accordion
    implementation uses under the hood regardless of visual styling
    (aria-expanded, native <details>), so this needs no per-site tuning.
    Best-effort and capped: a failed or slow expand must never abort the
    scrape, and a pathological page can't turn this into a click storm.
    """
    try:
        page.evaluate(
            """
            () => {
              document.querySelectorAll('details:not([open])').forEach(d => { d.open = true; });
              const toggles = Array.from(
                document.querySelectorAll('[aria-expanded="false"]')
              ).slice(0, 40);
              for (const el of toggles) {
                try { el.click(); } catch (e) { /* one bad toggle must not stop the rest */ }
              }
            }
            """
        )
        page.wait_for_timeout(300)
    except Exception:
        logger.debug("accordion expand failed; continuing with collapsed content", exc_info=True)


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


def _sample_field_labels(page: Page, limit: int = 25) -> list[str]:
    """Short list of visible input/textarea labels/placeholders on the page — lets a failed type_and_read tell the caller what fields WERE available, instead of a bare 'not found'."""
    try:
        texts = page.eval_on_selector_all(
            "input, textarea, select",
            """els => els.map(e => {
                const label = e.labels && e.labels[0] ? e.labels[0].innerText : '';
                return (label || e.placeholder || e.getAttribute('aria-label') || e.name || '').trim();
            }).filter(t => t.length > 0 && t.length < 60)""",
        )
        return texts[:limit]
    except Exception:
        return []


def maybe_start_session() -> PlaywrightSession | None:
    """Start a PlaywrightSession for one compose, or None if the feature is disabled or launch fails.

    Never raises -- a compose that can't get a browser (Playwright not
    installed, Chromium launch failure, feature flag off) still runs; it
    just falls back to the plain HTTP fetch path for research tools that
    check for a session. Caller is responsible for calling .close() in a
    finally block once the compose ends.
    """
    from app.modules.scraper.crawler_registry import is_web_spa_enabled

    if not is_web_spa_enabled():
        return None
    try:
        return PlaywrightSession()
    except Exception:
        logger.warning("failed to start persistent playwright session for compose", exc_info=True)
        return None


def save_screenshot(png_bytes: bytes) -> str | None:
    """Persist a captured PNG to SCREENSHOT_STORAGE_DIR, content-addressed by its sha256 hash, and return its public URL. None if SCREENSHOT_STORAGE_DIR isn't configured (kill switch) or the write fails -- never raises, since a failed screenshot save must not abort a compose over an illustration.

    Content-addressed naming means an identical screenshot captured twice
    (e.g. a revision pass re-checking the same page state) reuses the same
    file/URL instead of piling up duplicates on disk.
    """
    if not config.SCREENSHOT_STORAGE_DIR:
        return None
    import hashlib

    digest = hashlib.sha256(png_bytes).hexdigest()
    try:
        target_dir = Path(config.SCREENSHOT_STORAGE_DIR)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{digest}.png"
        if not target_path.exists():
            target_path.write_bytes(png_bytes)
        base = config.SCREENSHOT_PUBLIC_BASE_URL.rstrip("/")
        return f"{base}/{digest}.png"
    except Exception:
        logger.warning("failed to save captured screenshot", exc_info=True)
        return None


class PlaywrightSession:
    """One Playwright browser+context, held open for the duration of a single compose and reused across every fetch/click/type call in it.

    Launching a fresh Chromium process per fetch_url call (the old
    per-call behavior in fetch_page/click_and_read above) made "always
    render via Playwright" prohibitively expensive -- a single compose
    makes dozens of research calls, and browser launch+teardown alone
    costs 1-3s each. This launches ONE browser+context up front (compose
    start) and opens/closes only a lightweight PAGE per call; the browser
    itself is torn down exactly once, via close(), when the compose ends
    (success or failure -- caller must use try/finally).
    """

    def __init__(self) -> None:
        """Launch the browser+context immediately; raises if Playwright/Chromium isn't available (use maybe_start_session() to get a caller-safe None instead)."""
        from playwright.sync_api import sync_playwright

        self._closed = False
        self._playwright = sync_playwright().start()
        launch_kwargs: dict[str, object] = {"headless": config.BROWSER_HEADLESS}
        channel = (config.BROWSER_CHANNEL or "").strip()
        if channel:
            launch_kwargs["channel"] = channel
        self._browser = self._playwright.chromium.launch(**launch_kwargs)
        context_kwargs: dict = {"user_agent": _BROWSER_UA}
        state_path = config.BROWSER_STORAGE_STATE_PATH
        if state_path and Path(state_path).is_file():
            context_kwargs["storage_state"] = state_path
            logger.info("playwright session using storage_state=%s", state_path)
        self._context = self._browser.new_context(**context_kwargs)
        # A long-lived page for play_interactive (2026-08-11), distinct from
        # every other method above: fetch/click/type each open a FRESH page
        # and close it immediately, so state never carries between separate
        # tool calls -- confirmed live trying to chain a click-to-open-search
        # then a type-into-the-revealed-input across two calls, which failed
        # because the second call started from a blank page again. This page
        # instead stays open across interactive_click/type/read calls until
        # interactive_close() or session teardown, so the model can act on a
        # game/app's actual resulting state instead of only ever its start.
        self._interactive_page: Page | None = None

    def close(self) -> None:
        """Idempotent -- safe to call even if init only partially succeeded, and safe to call twice."""
        if self._closed:
            return
        self._closed = True
        self.interactive_close()
        with contextlib.suppress(Exception):
            self._context.close()
        with contextlib.suppress(Exception):
            self._browser.close()
        with contextlib.suppress(Exception):
            self._playwright.stop()

    def _goto_and_settle(self, page: Page, url: str, timeout: int) -> None:
        from app.core.net_guard import assert_public_url

        assert_public_url(url)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        # See fetch_page's identical wait for why this is best-effort and capped short.
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout, 8_000))
        except Exception:
            logger.debug("networkidle wait timed out for %s; continuing", url)

    def _read_page(self, page: Page, *, engine: str, skip_login_wall_check: bool = False) -> BrowserPageResult:
        _expand_collapsed_content(page)
        title = page.title() or ""
        text = page.inner_text("body")
        html = page.content()
        final_url = page.url
        cleaned = _clean_extracted_text(text)
        state_path = config.BROWSER_STORAGE_STATE_PATH
        if (
            not skip_login_wall_check
            and _looks_like_login_wall(cleaned, title)
            and not (state_path and Path(state_path).is_file())
        ):
            raise BrowserScrapeError(
                "browser page looks like a login or gate — use push ingest, mail, or "
                "BROWSER_STORAGE_STATE_PATH for an allowlisted session you control"
            )
        if len(cleaned) < 80:
            raise BrowserScrapeError("browser page had insufficient visible text")
        return BrowserPageResult(
            title=title.strip(), text=cleaned, final_url=final_url, engine=engine, html=html
        )

    def fetch(
        self,
        url: str,
        *,
        wait_after_load_ms: int | None = None,
        timeout_ms: int | None = None,
        skip_login_wall_check: bool = False,
    ) -> BrowserPageResult:
        """Same contract as the module-level fetch_page, but reuses this session's browser/context instead of launching a new one."""
        wait_ms = wait_after_load_ms if wait_after_load_ms is not None else config.BROWSER_WAIT_MS
        timeout = timeout_ms if timeout_ms is not None else config.BROWSER_TIMEOUT_MS
        page = self._context.new_page()
        try:
            self._goto_and_settle(page, url, timeout)
            if wait_ms > 0:
                page.wait_for_timeout(wait_ms)
            return self._read_page(
                page, engine="playwright-session", skip_login_wall_check=skip_login_wall_check
            )
        finally:
            page.close()

    def inspect_network_hosts(
        self,
        url: str,
        *,
        click_text: str = "",
        wait_ms: int = 2_000,
        timeout_ms: int | None = None,
    ) -> dict[str, object]:
        """Load a page (optionally clicking one element first, e.g. a wallet-connect button) and report every distinct host it actually made a network request to, plus a best-effort Algorand mainnet/testnet call from known algod/indexer hostname patterns.

        Ground truth for "what does this app actually do" -- immune to stale
        or simply wrong page copy, unlike reading rendered/bundle text (see
        _classify_network_hosts' docstring for the incident that motivated
        this). click_text is optional: a plain page load alone often already
        reveals it (many dapps fetch account/network status on mount); pass
        it to trigger a specific flow (e.g. "Connect Wallet") when the
        network call only fires after an interaction.

        A result of "unknown" doesn't mean the network is undeterminable --
        it means no algod/indexer host crossed the BROWSER, which never
        happens for a backend-proxied app (e.g. Base44-hosted, confirmed
        live 2026-08-15 on lumirogue.com) whose real chain calls run
        server-side. Callers should fall back to an on-chain query tool
        instead of trusting the page's own network label -- see the
        research_tools.py schema's guidance for the writer-facing version.
        """
        timeout = timeout_ms if timeout_ms is not None else config.BROWSER_TIMEOUT_MS
        page = self._context.new_page()
        hosts: set[str] = set()

        def _record(request: object) -> None:
            with contextlib.suppress(Exception):
                from urllib.parse import urlparse

                host = urlparse(request.url).hostname  # type: ignore[attr-defined]
                if host:
                    hosts.add(host)

        page.on("request", _record)
        try:
            self._goto_and_settle(page, url, timeout)
            if click_text:
                locator = _locate_clickable(page, click_text)
                if locator is not None:
                    with contextlib.suppress(Exception):
                        _click_robust(locator)
            page.wait_for_timeout(wait_ms)
        finally:
            page.close()
        result = _classify_network_hosts(hosts)
        result["url"] = url
        result["clicked"] = click_text or None
        return result

    def click_and_read(
        self,
        url: str,
        click_text: str,
        *,
        wait_after_click_ms: int = 1500,
        timeout_ms: int | None = None,
    ) -> BrowserPageResult:
        """Same contract as the module-level click_and_read, but reuses this session's browser/context.

        Also catches the target=_blank case (root-caused 2026-08-13): a click
        that opens a NEW tab rather than navigating the current one leaves
        `page.url` unchanged, so the old version silently read the original
        page again and reported nothing happened -- exactly how an external
        marketplace link ("Get an Ankh" -> a Downbad listing) went unnoticed.
        expect_page() races a short window for a new tab against the click;
        if one opens, its content is read instead and it's closed afterward.
        """
        timeout = timeout_ms if timeout_ms is not None else config.BROWSER_TIMEOUT_MS
        page = self._context.new_page()
        try:
            self._goto_and_settle(page, url, timeout)
            locator = _locate_clickable(page, click_text)
            if locator is None:
                clickable = _sample_clickable_texts(page)
                msg = (
                    f"no element with text matching {click_text!r} found -- "
                    f"visible clickable text on the page includes: {clickable}"
                )
                raise BrowserScrapeError(msg)
            popup = None
            try:
                with self._context.expect_page(timeout=3_000) as popup_info:
                    _click_robust(locator)
                popup = popup_info.value
            except Exception:
                logger.debug("no new tab opened from click; treating as same-page navigation")
            if popup is not None:
                try:
                    popup.wait_for_load_state("domcontentloaded", timeout=timeout)
                    popup.wait_for_timeout(wait_after_click_ms)
                    return self._read_page(popup, engine="playwright-session-click-popup")
                finally:
                    with contextlib.suppress(Exception):
                        popup.close()
            page.wait_for_timeout(wait_after_click_ms)
            return self._read_page(page, engine="playwright-session-click")
        finally:
            page.close()

    def type_and_read(
        self,
        url: str,
        field_text: str,
        value: str,
        *,
        submit: bool = False,
        timeout_ms: int | None = None,
    ) -> BrowserPageResult:
        """Load url, type value into the first input/textarea matched by field_text (its label, placeholder, or aria-label/name), optionally press Enter, and return the page's content afterward.

        Self-reported gap, 2026-08-11: existing tools could fetch and click
        but not type -- a page whose real content sits behind a search box
        or filter form (an on-chain explorer's address search, a
        directory's filter field) was unreachable. Locator strategy tries
        label, then placeholder, then aria-label/name, in that order -- the
        same cues a sighted user would recognize a field by.
        """
        timeout = timeout_ms if timeout_ms is not None else config.BROWSER_TIMEOUT_MS
        page = self._context.new_page()
        try:
            self._goto_and_settle(page, url, timeout)
            field = self._locate_field(page, field_text)
            if field is None:
                fields = _sample_field_labels(page)
                msg = (
                    f"no input/textarea/select matching {field_text!r} found -- "
                    f"visible fields on the page include: {fields}"
                )
                raise BrowserScrapeError(msg)
            field.fill(value, timeout=10_000)
            if submit:
                field.press("Enter")
                # A submit typically triggers navigation or an async content
                # swap; settle again before reading, same as a fresh goto.
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout, 8_000))
                except Exception:
                    logger.debug("post-submit networkidle wait timed out; continuing")
            page.wait_for_timeout(1200)
            return self._read_page(page, engine="playwright-session-type")
        finally:
            page.close()

    def capture_screenshot(
        self,
        url: str,
        *,
        full_page: bool = False,
        timeout_ms: int | None = None,
    ) -> bytes:
        """Load url and return a PNG screenshot of the rendered page.

        full_page=False (default) captures just the viewport -- what a real
        visitor sees without scrolling, which is what "illustrate this
        article" usually wants. full_page=True captures the whole
        scrollable page, for something like a long leaderboard a single
        viewport can't show.
        """
        timeout = timeout_ms if timeout_ms is not None else config.BROWSER_TIMEOUT_MS
        page = self._context.new_page()
        try:
            self._goto_and_settle(page, url, timeout)
            _expand_collapsed_content(page)
            return page.screenshot(full_page=full_page, type="png")
        finally:
            page.close()

    def interactive_open(self, url: str, *, timeout_ms: int | None = None) -> BrowserPageResult:
        """Start a play_interactive exploration session: open url on a page that stays alive across subsequent interactive_click/type/read calls. Closes any previously-open interactive page first -- only one at a time."""
        self.interactive_close()
        timeout = timeout_ms if timeout_ms is not None else config.BROWSER_TIMEOUT_MS
        page = self._context.new_page()
        self._goto_and_settle(page, url, timeout)
        _expand_collapsed_content(page)
        self._interactive_page = page
        return self._read_page(page, engine="playwright-interactive")

    def interactive_click(self, click_text: str, *, wait_after_click_ms: int = 1500) -> BrowserPageResult:
        """Click visible text (or, for icon-only controls, a title/aria-label match) on the currently-open interactive page (see interactive_open) and read the resulting state -- the same page, not a fresh one."""
        page = self._require_interactive_page()
        locator = _locate_clickable(page, click_text)
        if locator is None:
            clickable = _sample_clickable_texts(page)
            msg = (
                f"no element with text matching {click_text!r} found -- "
                f"visible clickable text on the page includes: {clickable}"
            )
            raise BrowserScrapeError(msg)
        _click_robust(locator)
        page.wait_for_timeout(wait_after_click_ms)
        _expand_collapsed_content(page)
        return self._read_page(page, engine="playwright-interactive-click")

    def interactive_type(
        self, field_text: str, value: str, *, submit: bool = False
    ) -> BrowserPageResult:
        """Type into a field on the currently-open interactive page and read the resulting state."""
        page = self._require_interactive_page()
        field = self._locate_field(page, field_text)
        if field is None:
            fields = _sample_field_labels(page)
            msg = (
                f"no input/textarea/select matching {field_text!r} found -- "
                f"visible fields on the page include: {fields}"
            )
            raise BrowserScrapeError(msg)
        field.fill(value, timeout=10_000)
        if submit:
            field.press("Enter")
            try:
                page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                logger.debug("post-submit networkidle wait timed out; continuing")
        page.wait_for_timeout(1200)
        _expand_collapsed_content(page)
        return self._read_page(page, engine="playwright-interactive-type")

    def interactive_read(self) -> BrowserPageResult:
        """Re-read the currently-open interactive page's state with no action -- e.g. after a timer/animation the model wants to wait out."""
        page = self._require_interactive_page()
        return self._read_page(page, engine="playwright-interactive-read")

    def interactive_close(self) -> None:
        """End the current play_interactive session, if one is open. Idempotent."""
        if self._interactive_page is not None:
            with contextlib.suppress(Exception):
                self._interactive_page.close()
            self._interactive_page = None

    def _require_interactive_page(self) -> Page:
        if self._interactive_page is None:
            msg = "no interactive session open -- call play_interactive with action='open' first"
            raise BrowserScrapeError(msg)
        return self._interactive_page

    @staticmethod
    def _locate_field(page: Page, field_text: str) -> Locator | None:
        for strategy in (page.get_by_label, page.get_by_placeholder):
            try:
                loc = strategy(field_text, exact=False).first
                if loc.count() > 0:
                    return loc
            except Exception:
                continue
        try:
            escaped = field_text.replace("'", "\\'")
            loc = page.locator(
                f"[aria-label*='{escaped}' i], input[name*='{escaped}' i], "
                f"textarea[name*='{escaped}' i]"
            ).first
            if loc.count() > 0:
                return loc
        except Exception:
            pass
        return None

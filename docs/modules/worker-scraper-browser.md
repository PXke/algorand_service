# Brick: Browser scraper (Playwright hard targets)

## Goal

Scrape **hard targets** — SPAs, heavy JavaScript, and sites where plain HTTP returns shells or empty markup — using **headless Chromium via Playwright**.

We standardize on **Playwright in Python workers**. Puppeteer (Node) and Selenium are not required; Playwright covers the same class of problems with one browser install path.

## Status

`partial` — shared engine + `BrowserScraper` for allowlisted HTTPS; Discord/Telegram keep dedicated parsers on top of the same engine.

## When to use

| Registry `scrape_url` | Scraper |
|------------------------|---------|
| `browser://https://…` | `BrowserScraper` (explicit) |
| `https://…` on allowlisted host | `BrowserScraper` when `SCRAPE_ENGINE_DEFAULT=auto` |
| `discord://channels/…` | `DiscordWebScraper` (Playwright + Discord text extraction) |
| `telegram://…` / `https://t.me/s/…` | `TelegramWebScraper` (HTTP first; optional Playwright fallback) |
| Other `https://…` | `HttpScraper` |

## Configuration (workers)

| Variable | Default | Meaning |
|----------|---------|---------|
| `SCRAPE_ENGINE_DEFAULT` | `auto` | `auto` \| `http` \| `browser` |
| `BROWSER_SCRAPE_DOMAINS` | `discord.com,…,t.me,…` | Comma hosts that trigger Playwright for plain `https://` URLs |
| `BROWSER_HEADLESS` | `1` | Headless Chromium |
| `BROWSER_TIMEOUT_MS` | `35000` | Navigation timeout |
| `BROWSER_WAIT_MS` | `2500` | Extra wait after `domcontentloaded` for SPA hydration |
| `BROWSER_CHANNEL` | *(empty)* | Optional Playwright channel (`chrome`, `msedge`) |
| `BROWSER_STORAGE_STATE_PATH` | *(empty)* | Optional Playwright storage state JSON for **your** logged-in session on allowlisted sites only |

## Install browsers (required)

On the worker host or image:

```bash
pip install playwright
playwright install-deps chromium
playwright install chromium
```

Test image (`docker/Dockerfile`) runs the same when `PLATFORM_BROWSER=1`.

## Policy (what we do **not** do)

- No residential proxy rotation or CAPTCHA-solver farms for social login walls.
- No stealth plugins to evade Discord/Telegram ToS on unauthorized pages.
- Login-gated official Discord still needs **push ingest** or **mail** — browser scrape surfaces `login_required`-style errors.

See [crawler-access-strategy.md](crawler-access-strategy.md).

## Code map

- `workers/app/modules/scraper/core/browser_scrape.py` — Playwright fetch + login-wall heuristic
- `workers/app/modules/scraper/core/browser_scraper.py` — `BaseScraper` adapter
- `workers/app/modules/scraper/core/scrape_engine.py` — routing helper
- `workers/app/modules/scraper/core/factory.py` — `get_scraper_for_url`
- `workers/app/modules/scraper/core/web_fetch.py` — HTTP + delegates Playwright to `browser_scrape`

## Example registry entries

```text
browser://https://developer.algorand.org/docs/
https://some-spa.example.com/releases   # if host in BROWSER_SCRAPE_DOMAINS
discord://channels/GUILD_ID/CHANNEL_ID  # DiscordWebScraper, not browser://
```

Publish caps (7 standard / day) apply unchanged — browser crawl only feeds the ingest queue.

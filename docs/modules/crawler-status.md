# Crawler status

Per-type crawlers: [crawler-types.md](crawler-types.md) (`crawler_config` table + `CRAWLER_*_ENABLED` env). (June 2026)

Snapshot of what works, what is stubbed, and what we **will not** build.

## Summary

| Platform | Implementation | Production-ready? | Needs |
|----------|----------------|-------------------|--------|
| **Web (HTTP)** | `HttpScraper` | Yes for static sites | — |
| **Web (browser)** | `BrowserScraper` | Yes for allowlisted SPAs | `browser://` or `BROWSER_SCRAPE_DOMAINS` — [worker-scraper-browser.md](worker-scraper-browser.md) |
| **Discord** | Bot REST API + poll | **Only if bot invited** | Official Algorand Discord **cannot** use bot → use **push ingest** |
| **Reddit** | Public `.json` + optional OAuth | **Partial** | OAuth app for prod; may block DC IPs |
| **Mail** | IMAP poll → ingest | **Yes**, if IMAP configured | `MAIL_IMAP_*` credentials |
| **Telegram** | **Web** `t.me/s/…` (default) | **Yes** for **public** channels | `telegram://s/username`; bot mode optional |
| **Discord** | **Web** Playwright (default) | **Partial** | `discord://channels/GUILD/CHANNEL`; official server often **login wall** → push ingest |
| **Chain** | Tx match → optional scrape | Yes (wake only) | `scrape_url` on registry row |

Shared: `http_retry.py` (429 backoff), `scrape_cooldown.py` (pause after failures), `ingest_publish_signal()`.

## What we explicitly reject

The following are **out of scope** for Algorand Platform news (see [crawler-access-strategy.md](crawler-access-strategy.md)):

- Residential/datacenter **proxy rotation** to evade IP blocks  
- **CAPTCHA solving** services (2Captcha, etc.)  
- **Stealth** headless browsers to scrape logged-in social feeds  
- **User-session** Telegram/Discord clients (MTProto, self-bots)  
- “Mimic human” timing solely to evade detection on unauthorized pages  

Those tactics conflict with ToS, add fragility, and do not fix Telegram’s core rule: **no bot in channel → no API access**.

## Reddit & Discord (aligned with “official API” path)

### Discord — good fit

- Anti-bot on **website** is irrelevant; we use **Bot API** with token.  
- Requirement: server owner invites bot; read permissions on each channel.  
- On 429: backoff (`Retry-After`), increase `DISCORD_POLL_SECONDS`, cooldown per `service_id`.

### Reddit — good fit with OAuth

- Tier A (public JSON): works for dev; fragile in production.  
- Tier B (**recommended**): `REDDIT_OAUTH_ENABLED=1` + client id/secret.  
- Still no HTML scraping, proxies, or CAPTCHA pipeline.

## Telegram — your constraint

> We cannot add official bot to Telegram channels that are not ours.

Then **automated ingest from those channels is not possible via Bot API**. Options:

1. **Monitor only channels we own** (or where a partner adds our bot).  
2. **Inbound webhook** — partner’s bot forwards post payload to `POST /api/v1/ingest/...` (future).  
3. **Rely on mirrors** — same content on mail, Discord, Reddit, or project website already in registry.  
4. **Editorial ingest** — human or Foundation workflow pushes text into queue.

Do **not** plan stealth scraping of `t.me` for channels we do not operate.

## Email — best official lane

For Algorand Foundation and partners, **IMAP** is often the most reliable “official” source:

- No anti-bot on well-configured OAuth/IMAP  
- High `source_trust` in scoring  
- Fast path for community calls

## Next engineering steps (compliant)

| Priority | Task |
|----------|------|
| P0 | Reddit OAuth in production env |
| P0 | Document which Discord/Telegram registry rows have bot access |
| P0 | **`POST /api/v1/ingest/signal`** for Foundation/partner pushes ([push-ingest.md](push-ingest.md)) |
| P1 | Telegram: switch from `getUpdates` to `getChat` + channel message fetch **only for bot-admin channels** |
| P2 | Expand `BROWSER_SCRAPE_DOMAINS` + `robots.txt` checks for browser hosts |
| P3 | Admin “paste announcement” → `ingest_publish_signal` |

## Anti-bot mapping (honest)

| Mechanism | Our response — not “bypass” |
|-----------|----------------------------|
| Rate limiting | Backoff, longer poll interval, cooldown |
| CAPTCHA / JS challenge | Use API lane or skip source |
| IP block | Fix OAuth/token; **do not** rotate residential IPs |
| User-Agent block | Proper Reddit UA + OAuth |
| Behavioral detection | Irrelevant when using Bot/OAuth APIs |
| Session / fingerprint | Not used for social scraping |

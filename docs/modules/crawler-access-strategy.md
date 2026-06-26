# Crawler access strategy (Reddit, Discord, Telegram, web)

We do **not** bypass platform protections with scrapers, CAPTCHA solvers, or fake logins. That breaks ToS, breaks often, and creates legal risk. We use **authorized APIs**, **consent**, and **rate discipline**.

## Principles

| Do | Do not |
|----|--------|
| Official Bot / OAuth / IMAP APIs | Headless login to user accounts |
| Tokens in env (server-side only) | Publish tokens in the Flutter app |
| Poll intervals + 429 backoff | Hammer endpoints when blocked |
| Public web previews (`t.me/s`, discord.com URLs) | Logged-in session scraping / CAPTCHA farms |
| Mail + on-chain as official lanes | Pretend social HTML is “public” when it is not |

## Per platform

### Discord (website crawl — default)

| Approach | Notes |
|----------|--------|
| **Web** `DISCORD_SCRAPE_MODE=web` | Playwright on `discord://channels/GUILD/CHANNEL` → [worker-scraper-discord-web.md](worker-scraper-discord-web.md) |
| **Bot** `DISCORD_SCRAPE_MODE=bot` | Legacy Bot API if invited |
| **Official server** | Usually **login required** on web → [push-ingest.md](push-ingest.md) |

### Reddit

| Tier | Access | Limits |
|------|--------|--------|
| **A — Public JSON** (current) | `reddit.com/r/{sub}/{sort}.json` + descriptive `REDDIT_USER_AGENT` | Strict rate limits; may 429 or block datacenter IPs |
| **B — OAuth app** (recommended prod) | `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` → bearer token | Higher, documented quotas |
| **C — Pushshift / third party** | Only if license allows | Not default |

If JSON returns 403/429 persistently: slow polls, OAuth tier B, or **stop** that subreddit and rely on mail/Discord mirrors.

### Telegram (website crawl — default)

| Approach | Notes |
|----------|--------|
| **Web** `TELEGRAM_SCRAPE_MODE=web` | `https://t.me/s/username` HTTP parse — [worker-scraper-telegram.md](worker-scraper-telegram.md) |
| **Bot** `TELEGRAM_SCRAPE_MODE=bot` | Optional `TELEGRAM_BOT_TOKEN` |
| **Private channels** | No public preview → [firefox-channel-sync.md](firefox-channel-sync.md) or push ingest |

Registry: `telegram://s/algorandfoundation`

## Rejected strategies (do not implement)

The following are sometimes suggested for “anti-bot bypass” but are **not** part of this product:

- Residential proxy pools, CAPTCHA-solving APIs, puppeteer-stealth on reddit.com/discord.com/t.me  
- Fake human delays used to evade detection on **unauthorized** pages  
- Telegram user-session clients to read channels without admin consent  

Use **E. Official APIs** and **H. rate-limit handling** from a compliance-first playbook only.

### Web (SPA / anti-bot)

| Approach | Notes |
|----------|--------|
| Static HTTP | `HttpScraper` for server-rendered pages |
| Playwright | [worker-scraper-browser.md](worker-scraper-browser.md) — allowlisted domains; respect `robots.txt` |
| Alternative | RSS, sitemap, GitHub releases, mail notifications |

## Implementation (code)

- `workers/app/modules/scraper/core/http_retry.py` — retries 429/503 with `Retry-After`
- `workers/app/modules/scraper/core/scrape_cooldown.py` — Redis cooldown per `service_id` after failures
- Env: `SCRAPE_COOLDOWN_SECONDS`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` (OAuth phase)

## Operational playbook

1. **403/401** → check token, bot invite, channel ID — not “rotate IP”.
2. **429** → backoff + increase `*_POLL_SECONDS`.
3. **Repeated failure** → set registry `enabled=false` for that source until fixed.
4. Prefer **mail** for Foundation official comms when social APIs are flaky.

## Related

- [worker-scraper-discord.md](worker-scraper-discord.md)
- [worker-scraper-reddit.md](worker-scraper-reddit.md)
- [news-pipeline-plan.md](news-pipeline-plan.md)

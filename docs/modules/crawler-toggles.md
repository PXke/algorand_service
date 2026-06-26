# Crawler toggles (legacy)

Superseded by [crawler-types.md](crawler-types.md).

| Old env | New mapping |
|---------|-------------|
| `CRAWLER_HTTP_ENABLED` | `web` crawler |
| `CRAWLER_BROWSER_ENABLED` | `CRAWLER_WEB_SPA_ENABLED` (SPA inside `web`) |
| `CRAWLER_MAIL_ENABLED` | `mail` crawler |

Add per-type: `CRAWLER_REDDIT_ENABLED`, `CRAWLER_TELEGRAM_ENABLED`, `CRAWLER_DISCORD_ENABLED`, `CRAWLER_CHAIN_ENABLED`, `CRAWLER_METRICS_ENABLED`.

Database: `crawler_config` table (migration 013).

# Flutter Frontend

Web-first client for **PXke Algorand**, a tag-sectioned newspaper built on the
Algorand Platform (front page, topic pages, article search, admin console).

## Run (web)

```bash
cd frontend_flutter
flutter pub get
flutter run -d chrome --web-port=5173 \
  --dart-define=API_BASE_URL=http://127.0.0.1:8080 \
  --dart-define=AUTH_DOMAIN=localhost
```

Backend must use matching `AUTH_DOMAIN=localhost`. **Web only:** CORS applies in the browser; with `APP_ENV=dev` or `test` the API accepts any `Origin` (random Flutter ports, Electron renderer, `Origin: null`). **Flutter desktop/mobile** uses `package:http` and is not subject to CORS. For production browser or Electron renderer hosts, add their origin to `CORS_ALLOWED_ORIGINS` (e.g. `app://your-app-id`).

## Architecture

- `lib/core/` — API client, `AppConfig` (`--dart-define`), router, theme, l10n locale list
- `lib/modules/` — `newspaper` (front page, feed, article detail, hot, topics, about/contact), `admin` (ops console), `auth` (wallet login/session), `search`, `suggestions` (flag-gated), `shell` (app chrome, `AppShell`/`AppSwitcher`), `misc` (deferred-loading bundle re-exporting About/Contact/Search/Suggestions)
- `wallet_auth_flutter` — path dependency in `pubspec.yaml`

## Products

| Route | Product |
|-------|---------|
| `/` | Front page (newspaper) |
| `/news`, `/news/articles/:articleId` | News feed + article detail |
| `/topics`, `/topic/:tag` | Tag-derived topic pages (the section structure) |
| `/hot` | Trending/hot page |
| `/section/:slug` | Redirect-only: legacy section slugs → `/topic/<tag>` or `/topics` |
| `/about`, `/contact` | Misc bundle |
| `/search` | Article search (Typesense or feed fallback) |
| `/suggestions` | Suggestions board — flag-gated (`AppConfig.suggestionsEnabled`); redirects to `/` when disabled |
| `/admin` | Admin console (wallet in `ADMIN_WALLET_ADDRESSES`) — tabs: Seeds, Articles, Writer Briefs, Classifier, Queue, Training, Gatekeeper, Domains, Tool Insights, Sessions, Analytics, Inbox, System |
| `/sources` | Redirect-only → `/admin` (standalone sources UI retired) |

**Appearance:** Light and dark themes; toggle in the app bar or choose Light / Dark / System in the drawer.

**Languages:** 9 — English, Spanish, Arabic, Farsi, French, Hindi, Pashto, Russian, Chinese (`lib/l10n/`). Choose **Language** in the drawer (or system default). Regenerate after ARB edits: `flutter gen-l10n`.

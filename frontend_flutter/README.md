# Flutter Frontend

Web-first client for the Algorand Platform (News, Suggestions, Search).

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

- `lib/core/` — API client, `AppConfig` (`--dart-define`), router, theme
- `lib/modules/` — auth, newspaper, suggestions, search, shell
- `wallet_auth_flutter` — path dependency in `pubspec.yaml`

## Products

| Route | Product |
|-------|---------|
| `/` | Home — product tiles |
| `/news` | News feed + article detail (optional `?service_id=` filter) |
| `/admin` | Admin sources panel (wallet in `ADMIN_WALLET_ADDRESSES`) |
| `/sources` | Registered crawlers: Discord, Reddit, web |

**Appearance:** Light and dark themes; toggle in the app bar or choose Light / Dark / System in the drawer.

**Languages:** English and Spanish (`lib/l10n/`). Choose **Language** in the drawer (or system default). Regenerate after ARB edits: `flutter gen-l10n`.
| `/suggestions` | Suggestions board |
| `/search` | Article search (Typesense or feed fallback) |

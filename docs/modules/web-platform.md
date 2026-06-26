# Brick: Web platform (CORS + env)

## Goal

Flutter **web** can call the Robyn API from the browser with matching auth domain and CORS.

## Status

`done`

## Features (should do)

- Register Robyn CORS for origins in `CORS_ALLOWED_ORIGINS`
- Document `AUTH_DOMAIN` alignment with browser host (e.g. `localhost` for `flutter run -d chrome`)
- Flutter `AppConfig.fromEnvironment()` for `API_BASE_URL` and `AUTH_DOMAIN`
- `.env.example` lists common dev origins (8080, 5173, etc.)
- **`APP_ENV=dev` or `test`:** permissive CORS (any `Origin`, including `null` for `file://` / Electron renderer)
- **Native HTTP clients** (Flutter mobile/desktop `http`, Electron **main** process, CLI): no CORS — not affected

## Good to have

- `GET /api/v1/config/public` returning non-secret client hints (chain id, treasury address for UI)
- Security headers (CSP) on API responses for browser clients

## Desktop shells (Electron, Tauri, etc.)

| Client | CORS? | Notes |
|--------|-------|-------|
| Electron **main** (Node `fetch` / `net`) | No | Preferred for API calls in production apps |
| Electron **renderer** (`fetch` from UI) | Yes | Dev/test: permissive. Prod: add `app://…` or dev-server URL to `CORS_ALLOWED_ORIGINS` |
| Flutter **desktop** (`package:http`) | No | Same as mobile |
| Flutter **web** | Yes | Dev/test permissive; or pin `--web-port` to a listed origin |

## Future improvements

- Per-tenant origin allowlists in deploy
- Automatic CORS origin from `APP_ENV` templates
- Cookie-based session alternative to `x-session-token` header (with CSRF protection)

## Standards & RFCs

| Reference | Use |
|-----------|-----|
| [Fetch CORS](https://fetch.spec.whatwg.org/#cors-protocol) | `ALLOW_CORS`, credentialed requests |
| [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) | HTTP semantics |
| CAIP-122 / EIP-4361 | `AUTH_DOMAIN` must match SIWA `domain` |

[standards-and-rfcs.md](../architecture/standards-and-rfcs.md#web-platform).

## Depends on

- `wallet-auth` (SIWA domain must match `AUTH_DOMAIN` on server)

## Code map

- `backend/app/core/cors.py`, `backend/app/core/config.py`
- `frontend_flutter/lib/core/config/app_config.dart`
- `frontend_flutter/README.md`

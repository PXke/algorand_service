# Brick: Frontend shell

## Goal

One Flutter web app shell with navigation across News, Suggestions, and Search.

## Status

`done`

## Features (should do)

- `go_router` with `ShellRoute` wrapping products
- Masthead navigation ≥860px: brand + inline tabs (animated accent underline) in the app bar; drawer below that
- Wallet action, theme and locale toggles in the app bar
- Soft fade/rise page transitions between products (`CustomTransitionPage`)
- Highlight active nav item from current path

## Good to have

- App bar title reflects current product
- Keyboard shortcut to open drawer

## Future improvements

- Responsive layout: bottom nav on narrow screens
- User settings page (API URL override for dev)
- i18n / l10n
- Branded theme per deployment
- Logged-in-only routes guard

## Standards & RFCs

Flutter web routing; inherits CORS/`AUTH_DOMAIN` from `web-platform`. [standards-and-rfcs.md](../architecture/standards-and-rfcs.md).

## Depends on

- `frontend-auth`

## Code map

- `frontend_flutter/lib/modules/shell/`
- `frontend_flutter/lib/core/router/app_router.dart`

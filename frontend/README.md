# PXke Algorand web SPA (Vite + Svelte 5)

## Dev

```bash
cd frontend
npm install
npm run dev
```

Defaults to http://127.0.0.1:5173 with `/api` proxied to `http://127.0.0.1:8080`.

## Env (`VITE_*`)

| Variable | Default |
|----------|---------|
| `VITE_API_BASE_URL` | empty (same-origin / vite proxy) |
| `VITE_AUTH_DOMAIN` | `localhost` |
| `VITE_ADMIN_WALLET_ADDRESSES` | (comma-separated) |
| `VITE_SUGGESTIONS_ENABLED` | `false` |
| `VITE_EXPLORER_BASE_URL` | Pera TestNet explorer |
| `VITE_ALGOD_API_URL` | Algonode TestNet |

Copy `.env.example` to `.env.local` for local overrides.

## Build

```bash
npm run build
```

Output: `dist/` (static files for nginx).

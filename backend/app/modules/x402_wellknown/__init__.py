"""Marketplace discovery bootstrap: /.well-known/x402 and /openapi.json.

Two top-level (non-`/api/`-prefixed) routes agents and peer x402 directories
(gold-402, x402 List, and similar) look for before ever calling
`GET /api/v1/x402` (the real catalog, see `app.modules.x402_catalog`):

- `GET /.well-known/x402` -- the emergent, non-ratified discovery-manifest
  convention several directories already crawl for. No single spec defines
  its shape (coinbase/x402's own spec has no well-known convention as of
  this writing), so this route re-serves the exact same document
  `/api/v1/x402` already produces rather than inventing new fields.
- `GET /openapi.json` -- a real OpenAPI 3.1 document generated from the same
  catalog roster (`x402_catalog.services.catalog.build_catalog`), not a
  second hand-written route list.

Both are free, registered unconditionally inside `x402_enabled` like the
catalog itself (no product store gate of their own), and need dedicated
nginx `location` blocks on the API host since it only proxies `/api/` and
`/health/ready` by default (see deploy/nginx/algorand-platform.conf).
"""

# Brick: Media (image proxy)

## Goal

Let Flutter web (CanvasKit) load external hero/OG images that lack CORS
headers, without exposing an open image-fetching SSRF surface.

## Status

`done`

## Features (should do)

- `GET /api/v1/img?url=` — same-origin proxy, SSRF-guarded (no internal/private targets)
- Downsizes/re-encodes to WebP, rasterizes SVG/ICO
- Redis caching, placeholder fallback on fetch failure

## Good to have

- CDN-aware cross-domain guard for hero image resolution (see hero-image-resolution memory)

## Future improvements

- n/a — narrow, complete scope

## Standards & RFCs

n/a.

## Depends on

- Redis

## Code map

- `backend/app/modules/media/`

# Brick: Frontend search

## Goal

Search UI over `search-api` with navigation to article detail.

## Status

`done`

## Features (should do)

- Search input + submit
- Display results list (title, summary)
- Tap result → newspaper article route
- Show which `engine` was used (Typesense vs feed_scan)
- Handle errors and empty results

## Good to have

- Debounced search as user types — **done** (450 ms)
- Clear button on input — **done**

## Future improvements

- Facet filter by service (dropdown)
- Highlight query terms in results
- Recent searches in local storage
- “In scope” badge when classifier exists
- Mobile-friendly result cards

## Standards & RFCs

[RFC 3986](https://www.rfc-editor.org/rfc/rfc3986) query encoding. [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#frontend-search).

## Depends on

- `search-api`, `frontend-shell`, `frontend-newspaper` (detail route)

## Code map

- `frontend_flutter/lib/modules/search/`

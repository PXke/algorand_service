# Brick: Feed placements

## Goal

Let sponsored or editorially pinned content appear in specific slots of the
news feed alongside organic articles.

## Status

`done` (read path); write path is DB-only — no admin UI yet

## Features (should do)

- `GET /api/v1/news/placements` → `PlacementService.list_feed_placements()` → Cassandra `PlacementsStore`

## Good to have

- Admin UI for creating/editing placements (currently DB-only)

## Future improvements

- Scheduling (start/end time) and targeting rules

## Standards & RFCs

n/a.

## Depends on

- `news-api` (rendered inline with the feed)

## Code map

- `backend/app/modules/placements/`

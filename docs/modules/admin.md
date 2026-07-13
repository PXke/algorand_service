# Brick: Admin console

## Goal

Wallet-gated ops/CMS surface for running the newspaper day to day: editorial
review, source curation, classifier tuning, and system health — without
touching the database directly.

## Status

`done`

## Features (should do)

- Wallet-gated via `require_admin_wallet` (`ADMIN_WALLET_ADDRESSES`)
- Article edit/delete/versions, editorial briefs, official channels
- Source CRUD + merge, scraper triggers, domain approve/reject
- Classifier review/feedback/retrain, gatekeeper anchors/validation
- Compose sessions, translations backfill, investigations, Celery status
- Analytics dashboard, contact inbox
- Flutter tabs: Seeds, Articles, Writer Briefs, Classifier, Queue, Training, Gatekeeper, Domains, Tool Insights, Sessions, Analytics, Inbox, System

## Good to have

- Finer-grained per-tab wallet roles (currently one flat admin allowlist)

## Future improvements

- Audit log of admin actions

## Standards & RFCs

n/a (internal tool).

## Depends on

- Every other backend module (this is the control surface for all of them)

## Code map

- `backend/app/modules/admin/` (`api/routes.py` ~35 endpoints, `stores/cassandra.py`)
- `frontend_flutter/lib/modules/admin/`

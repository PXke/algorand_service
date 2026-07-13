# Brick: Gatekeeper (pre-publish quality gate)

## Goal

Catch incomplete or unsupported drafts before they publish, without adding a
second slow LLM pass to every article.

## Status

`partial` — deterministic gate is live (shadow mode); ModernBERT quality head is built but not wired into serving

## Features (should do)

- Deterministic gate (`live.py:gate_draft`) — rule-based completeness + numeric-entailment factuality check, no ML, called from every publish path
- `GATEKEEPER_ENABLED=true` (default on, shadow) / `GATEKEEPER_ENFORCE=false` (default off) — computes and logs but does not block
- ModernBERT "MTTH" multi-task head (quality/factuality/tone) — `model.py`, `training.py`, `inference.py`
- Admin-triggered training task `app.tasks.gatekeeper.train_quality_head` (not on beat)
- Admin-triggered annotator validation against human-tagged anchors (`run_annotator_validation`)
- `GATEKEEPER_QUALITY_LIVE=false` (default off) — and even when flipped on, `live.py:quality_proba()` currently has no callers in the publish path

## Good to have

- Wire `quality_proba()` into the publish path behind `GATEKEEPER_QUALITY_LIVE`

## Future improvements

- Flip `GATEKEEPER_ENFORCE=true` once shadow-mode false-positive rate is known
- Retire the old sklearn "learned grader" (`newspaper/grader_model.py`) — already has no live reader

## Standards & RFCs

n/a (internal ML gate).

## Depends on

- `article-compose`, `classifier_feedback` (admin-corrected scores train the quality head — see grader-feedback-disconnect memory)

## Code map

- `workers/app/modules/gatekeeper/` (`live.py`, `model.py`, `training.py`, `inference.py`)
- `workers/app/tasks/gatekeeper.py`
- Called from `workers/app/modules/newspaper/tasks/publish_tasks.py` (5 call sites)

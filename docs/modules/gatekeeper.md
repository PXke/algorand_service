# Brick: Gatekeeper (pre-publish quality gate)

## Goal

Catch incomplete or unsupported drafts before they publish, without adding a
second slow LLM pass to every article.

## Status

`partial` — the deterministic gate is live (shadow mode). The ModernBERT
quality/relevance heads are defined but have no training or serving wiring
(the factuality/tone heads that used to sit alongside them, plus the
quality-head training task, were removed as dead code 2026-08-25 — see
Future improvements).

## Features (should do)

- Deterministic gate (`live.py:gate_draft`) — rule-based completeness + numeric-entailment factuality check, no ML, called from every publish path
- `GATEKEEPER_ENABLED=true` (default on, shadow) / `GATEKEEPER_ENFORCE=false` (default off) — computes and logs but does not block
- Admin-triggered annotator validation against human-tagged anchors (`run_annotator_validation`)

## Not currently wired up

- `ModernBertMultiTaskGrader` (`model.py`) still defines a `quality_head` and a
  `relevance_head` and `training.py`'s loop can still train either one, but
  nothing in the codebase produces training batches for them and nothing
  reads a trained checkpoint at inference time. The task that used to train
  the quality head (`app.tasks.gatekeeper.train_quality_head`, triggered via
  `POST /api/v1/admin/retrain`) and its feedback-batch loader
  (`feedback_loader.py`) were removed 2026-08-25 because the checkpoint they
  produced had no reader (the serving path, `live.py:quality_proba()` +
  `inference.py`, was already confirmed dead and removed 2026-08-24). The
  factuality/tone heads were removed in the same pass — they never had a
  training corpus (blocked on a gold-run/corruptor corpus that itself was
  deleted as dead code 2026-08-24).
- `POST /api/v1/admin/retrain` now only queues `retrain_publish_classifier`
  (the sklearn publish classifier); it no longer sends a gatekeeper task.

## Future improvements

- Flip `GATEKEEPER_ENFORCE=true` once shadow-mode false-positive rate is known
- If the quality/relevance heads are ever revived, they need: a real training
  corpus, a feedback-batch loader, and a serving path wired into the publish
  flow behind an explicit live-flag — none of that exists today

## Standards & RFCs

n/a (internal ML gate).

## Depends on

- `article-compose` — the deterministic gate reads the compose trace + draft

## Code map

- `workers/app/modules/gatekeeper/` (`live.py`, `fact_align.py`, `completeness.py`, `model.py`, `training.py`, `anchors.py`, `annotator.py`, `validation.py`, `profile.py`, `structure.py`)
- `workers/app/tasks/gatekeeper.py`
- Called from `workers/app/modules/newspaper/tasks/publish_tasks.py` (5 call sites)

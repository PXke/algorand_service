# Brick: Gatekeeper (pre-publish quality gate)

## Goal

Catch incomplete or unsupported drafts before they publish, without adding a
second slow LLM pass to every article.

## Status

`partial` — the deterministic gate is live (shadow mode). That is now the
entire gatekeeper subsystem: the ModernBERT quality/relevance heads
(`model.py`/`training.py`) and the separate LLM-annotator anchor-validation
harness (`anchors.py`/`annotator.py`/`validation.py`/`profile.py`,
`app/tasks/gatekeeper.py`) were both removed as dead code 2026-08-25 — see
History.

## Features (should do)

- Deterministic gate (`live.py:gate_draft`) — rule-based completeness + numeric-entailment factuality check, no ML, called from every publish path
- `GATEKEEPER_ENABLED=true` (default on, shadow) / `GATEKEEPER_ENFORCE=false` (default off) — computes and logs but does not block
- `article_grader.py`'s LLM rubric (`fact_align.numeric_entailment_score` /
  `content_recency_score`, `structure.evaluate_structure`) is the trusted
  factuality/tone/structure signal today

## Future improvements

- Flip `GATEKEEPER_ENFORCE=true` once shadow-mode false-positive rate is known

## History

- 2026-08-24: the ModernBERT serving path (`live.py:quality_proba()` +
  `inference.py`) was confirmed dead and removed
- 2026-08-25 (first pass): the quality-head training task
  (`app.tasks.gatekeeper.train_quality_head`) and its feedback-batch loader
  (`feedback_loader.py`) were removed — the checkpoint they produced had no
  reader. The factuality/tone heads were removed the same pass — they never
  had a training corpus (blocked on a gold-run/corruptor corpus itself
  deleted as dead code 2026-08-24).
- 2026-08-25 (second pass): the remaining ModernBERT scaffolding
  (`model.py`, `training.py`) and the separate LLM-annotator
  anchor-validation harness (`anchors.py`, `annotator.py`, `validation.py`,
  `profile.py`, `app/tasks/gatekeeper.py`, the admin
  `/api/v1/admin/gatekeeper/*` routes, the `GatekeeperTab` admin UI, and the
  `gatekeeper_anchors`/`gatekeeper_validation_report` tables, migration 078)
  were removed. The harness had zero downstream consumers — its
  `trusted_types` output was never read by anything — and its anchor pool
  was never populated (0/40 tagged). Owner call: the existing
  `article_grader.py` LLM rubric is trusted as-is, so the validation harness
  isn't needed.
- `POST /api/v1/admin/retrain` only queues `retrain_publish_classifier`
  (the sklearn publish classifier); it never sent a gatekeeper task.

## Standards & RFCs

n/a (internal ML gate).

## Depends on

- `article-compose` — the deterministic gate reads the compose trace + draft

## Code map

- `workers/app/modules/gatekeeper/` (`live.py`, `fact_align.py`, `completeness.py`, `structure.py`)
- Called from `workers/app/modules/newspaper/tasks/publish_tasks.py` (5 call sites)

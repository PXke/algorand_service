"""Deterministic pre-publish gatekeeper.

The gatekeeper grades a finished draft against the agent's execution trace
before publication:

- ``fact_align``    deterministic trace<->article numeric-entailment check (no
                    deps). Powers ``live.py``'s live factuality signal.
- ``completeness``  deterministic mandatory-tool rule check (no deps).
- ``live``          the deterministic gate (``gate_draft``) that actually runs
                    on every publish path today.
- ``structure``     deterministic structure/format check, used by the article
                    grader rubric.

The ModernBERT quality/relevance heads (``model.py``/``training.py``) and the
LLM-annotator anchor-validation harness (``anchors.py``/``annotator.py``/
``validation.py``/``profile.py``) were removed 2026-08-25 as dead code — see
docs/modules/gatekeeper.md.
"""

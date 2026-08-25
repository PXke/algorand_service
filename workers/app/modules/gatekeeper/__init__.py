"""Local multi-task gatekeeper (MTTH) subsystem.

The gatekeeper grades a finished draft against the agent's execution trace
before publication. It is split into pieces by their dependency footprint so the
existing workers keep running without the heavy ML stack installed:

- ``fact_align``    deterministic trace<->article numeric-entailment check (no
                    deps). Powers ``live.py``'s live factuality signal.
- ``completeness``  deterministic mandatory-tool rule check (no deps).
- ``live``          the deterministic gate (``gate_draft``) that actually runs
                    on every publish path today.
- ``model``         ModernBERT multi-task encoder (lazy torch/transformers).
                    Defines ``quality_head``/``relevance_head`` but neither is
                    currently trained or served — see docs/modules/gatekeeper.md.
- ``training``      vanilla-BCE soft-label training loop (lazy torch/IPEX). No
                    production caller as of 2026-08-25 (its only caller, the
                    quality-head training task, was removed as dead code).
- ``anchors``, ``annotator``, ``validation``  the separate LLM-annotator
                    validation pipeline (``run_annotator_validation``), not
                    part of the ModernBERT model at all.

Heavy modules lazy-import torch so importing this package is always cheap.
"""

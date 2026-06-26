"""Local multi-task gatekeeper (MTTH) subsystem.

The gatekeeper grades a finished draft against the agent's execution trace
before publication. It is split into pieces by their dependency footprint so the
existing workers keep running without the heavy ML stack installed:

- ``fact_align``    deterministic trace<->article extractors (no deps). Powers
                    both the corruptor's Tier-1 negatives and a cheap factuality
                    signal that needs no model.
- ``completeness``  deterministic mandatory-tool rule check (no deps). Replaces
                    the old neural Head 2.
- ``model``         ModernBERT multi-task encoder (lazy torch/transformers).
- ``inference``     prior-corrected scoring; the log-odds shift lives here ONLY.
- ``training``      vanilla-BCE soft-label training loop (lazy torch/IPEX).
- ``corruptor``     adversarial synthetic-negative generator (Layer 2).

Heavy modules lazy-import torch so importing this package is always cheap.
"""

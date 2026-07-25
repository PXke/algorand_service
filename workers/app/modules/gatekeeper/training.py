"""CPU training loop for the ModernBERT multi-task grader.

Deliberately dumb training, smart inference (see ``inference``):
- Loss is plain ``BCEWithLogitsLoss`` on count-balanced batches. NO class-prior
  term in the loss — that would cancel the inference-time shift and redeploy the
  balanced model. The prior lives only in ``inference.logit_adjustment``.
- Targets are soft (annotator probabilities in [0,1]); BCEWithLogits accepts
  them directly, preserving the annotator's uncertainty instead of rounding.
- Heads are single-logit; the binary log-odds correction depends on it.

CPU optimization: thread pinning env (must be set before torch imports), then
``ipex.optimize`` + bf16 autocast. IPEX is optional — absence falls back to
plain torch so this runs anywhere. torch is lazy-imported so the module is free
to import without the ``ml`` extra.

Blocked on data: needs the gold runs + corruptor corpus to populate the loader.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Dedicated-evaluator thread pinning. Must be set BEFORE torch is imported, so
# call ``configure_cpu_threads()`` at process start (the Celery worker bootstrap),
# not just before training. Assumes the worker owns the box at --concurrency=1.
_CPU_ENV = {
    "OMP_NUM_THREADS": os.getenv("GATEKEEPER_OMP_THREADS", "16"),
    "KMP_BLOCKTIME": "1",
    "KMP_AFFINITY": "granularity=fine,compact,1,0",
}


def configure_cpu_threads() -> dict[str, str]:
    """Pin OpenMP/KMP threads for cache-friendly CPU inference/training. Returns the applied env for logging. No-op effect if torch is already imported."""
    os.environ.update(_CPU_ENV)
    return dict(_CPU_ENV)


@dataclass
class TrainConfig:
    """Hyperparameters and output path for a training run."""
    model_name: str = "answerdotai/ModernBERT-base"
    lr: float = 2e-5
    epochs: int = 3
    use_ipex: bool = True
    use_bf16: bool = True
    out_path: str = "data/models/gatekeeper_mtth.pt"


def _maybe_ipex(model: Any, optimizer: Any, dtype: Any) -> tuple[Any, Any, bool]:  # noqa: ANN401 -- torch model/optimizer/dtype, lazily imported to keep this module torch-free by default
    """Apply ipex.optimize when available; otherwise return inputs unchanged."""
    try:
        import intel_extension_for_pytorch as ipex
    except ImportError:
        return model, optimizer, False
    model, optimizer = ipex.optimize(model, optimizer=optimizer, dtype=dtype)
    return model, optimizer, True


def train_gatekeeper(train_loader: Any, cfg: TrainConfig | None = None) -> dict:  # noqa: ANN401 -- torch DataLoader-like iterable, lazily imported to keep this module torch-free by default
    """Train the grader on a loader yielding batches with keys ``input_ids``, ``attention_mask``, ``soft_label_factuality``, ``soft_label_tone`` (soft targets in [0,1]). Saves a state_dict to ``cfg.out_path``. Returns a summary dict."""
    configure_cpu_threads()
    cfg = cfg or TrainConfig()

    import torch
    import torch.nn as nn

    from app.modules.gatekeeper.model import build_model

    model = build_model(cfg.model_name)
    model.train()
    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg.lr)

    dtype = torch.bfloat16 if cfg.use_bf16 else torch.float32
    ipex_on = False
    if cfg.use_ipex:
        model, optimizer, ipex_on = _maybe_ipex(model, optimizer, dtype)

    # Plain BCE — soft targets, NO prior term. This is the whole point.
    criterion = nn.BCEWithLogitsLoss()

    n_batches = 0
    last_loss = 0.0
    for _ in range(cfg.epochs):
        for batch in train_loader:
            optimizer.zero_grad()
            with torch.autocast(device_type="cpu", dtype=dtype, enabled=cfg.use_bf16):
                out = model(batch["input_ids"], batch["attention_mask"])
                # Every head trains only when the batch carries its label — gold/
                # corrupted runs supply factuality+tone, feedback-derived batches
                # (no corruptor corpus yet) supply quality only. Optional per head.
                loss = None
                for head, key in (
                    ("factuality", "soft_label_factuality"),
                    ("tone", "soft_label_tone"),
                    ("quality", "soft_label_quality"),
                    ("relevance", "soft_label_relevance"),
                ):
                    if key in batch:
                        head_loss = criterion(out[head], batch[key])
                        loss = head_loss if loss is None else loss + head_loss
            if loss is None:
                continue
            loss.backward()
            optimizer.step()
            last_loss = float(loss.detach().float().item())
            n_batches += 1

    Path(cfg.out_path).parent.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), cfg.out_path)
    return {
        "status": "trained",
        "batches": n_batches,
        "epochs": cfg.epochs,
        "ipex": ipex_on,
        "bf16": cfg.use_bf16,
        "last_loss": round(last_loss, 4),
        "out_path": cfg.out_path,
    }

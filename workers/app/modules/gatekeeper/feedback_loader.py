"""Turns admin ``classifier_feedback`` labels into training batches for the gatekeeper's quality head only — factuality/tone still need the gold-run / corruptor corpus (see ``training.py``), which doesn't exist yet. Reuses the same ground truth ``grader_model._training_rows`` used for the now-dead sklearn grader (``row.approved``), redirected to the model that's actually served (``live.py:quality_proba``)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FeedbackBatchConfig:
    """Batch-loading parameters for classifier-feedback training data."""

    limit: int = 1000
    batch_size: int = 4
    max_length: int = 4096


def _labeled_examples(limit: int) -> list[tuple[str, float]]:
    """(model_input_text, label) pairs from classifier_feedback rows that captured the article body. label is 1.0/0.0 from the human approve/reject decision — same ground truth the sklearn grader used as ``y``."""
    from app.core.cassandra import execute_parallel_with_args, get_cassandra_session
    from app.core.statements import ClassifierFeedbackStmts
    from app.modules.gatekeeper.model import build_input
    from app.modules.newspaper.investigation_store import load_investigation_trace

    session = get_cassandra_session()
    index = list(session.execute(ClassifierFeedbackStmts.LIST_IDS, ("main", limit)))
    examples: list[tuple[str, float]] = []
    # Fan the per-id detail lookups out concurrently instead of one round-trip each.
    for ok, result in execute_parallel_with_args(
        ClassifierFeedbackStmts.GET_GRADE, [(idx.feedback_id,) for idx in index]
    ):
        row = result.one() if ok else None
        if row is None or not row.metadata:
            continue
        meta = dict(row.metadata)
        article_text = str(meta.get("article_text", "") or "")
        if not article_text.strip():
            continue
        # Best-effort trace, same construction quality_proba() uses at
        # inference — training and serving inputs must match.
        trace = load_investigation_trace(row.url) if row.url else ""
        text = build_input("", trace, article_text)
        examples.append((text, 1.0 if row.approved else 0.0))
    return examples


def quality_sample_stats(limit: int = 1000) -> dict[str, int]:
    """Cheap pre-flight class-balance check, for the caller's min-samples guard before kicking off a training run."""
    examples = _labeled_examples(limit)
    approved = sum(1 for _, y in examples if y >= 0.5)
    return {"total": len(examples), "approved": approved, "rejected": len(examples) - approved}


def iter_quality_batches(cfg: FeedbackBatchConfig | None = None) -> Iterator[dict[str, Any]]:
    """Yields batches of ``{input_ids, attention_mask, soft_label_quality}`` for ``train_gatekeeper``'s quality-only path. Caller decides whether there's enough data to bother (see ``quality_sample_stats``)."""
    cfg = cfg or FeedbackBatchConfig()
    examples = _labeled_examples(cfg.limit)
    if not examples:
        return

    import torch

    from app.modules.gatekeeper.model import load_tokenizer

    tok = load_tokenizer()
    for start in range(0, len(examples), cfg.batch_size):
        chunk = examples[start : start + cfg.batch_size]
        texts = [t for t, _ in chunk]
        labels = [y for _, y in chunk]
        enc = tok(
            texts,
            truncation=True,
            padding=True,
            max_length=cfg.max_length,
            return_tensors="pt",
        )
        yield {
            "input_ids": enc["input_ids"],
            "attention_mask": enc["attention_mask"],
            "soft_label_quality": torch.tensor(labels, dtype=torch.float32),
        }

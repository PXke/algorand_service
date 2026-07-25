"""ModernBERT multi-task grader (long-context encoder, two single-logit heads).

Design decisions locked in during spec review:
- ModernBERT-base (native 8192 ctx) so [source | trace] [SEP] [article] is not
  truncated the way deberta-v3-small (512) silently was.
- Tool-completeness is NOT a head here — it's a deterministic rule check
  (see ``completeness``). Only Factuality and Tone are learned.
- Single-logit heads (``Linear(h, 1)``), not 2-logit softmax: the binary
  log-odds prior correction at inference only makes sense for one logit.
- Mean pooling over the attention mask, not [CLS]: ModernBERT has no pretrained
  pooler, so [CLS] is not load-bearing.

torch/transformers are imported lazily so importing this module (and the wider
worker package) costs nothing when the ML extra isn't installed.
"""

from __future__ import annotations

from typing import Any

DEFAULT_MODEL_NAME = "answerdotai/ModernBERT-base"
# Distinct, rare sentinels. ModernBERT (RoBERTa-style) has no token_type_ids, so
# segment boundaries are purely lexical — keep these byte-identical everywhere.
SRC_TRACE_SEP = " ⟦TRACE⟧ "
TRACE_ARTICLE_SEP = " ⟦ARTICLE⟧ "


def build_input(source_text: str, tool_trace: str, article_json: str) -> str:
    """The single concatenated input string. Source first so the long-context window keeps the agent's environment visible to the factuality head."""
    return f"{source_text}{SRC_TRACE_SEP}{tool_trace}{TRACE_ARTICLE_SEP}{article_json}"


def _require_torch() -> tuple[Any, Any]:
    try:
        import torch
        import torch.nn as nn
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "gatekeeper.model needs the 'ml' extra: pip install 'algorand-platform-workers[ml]'"
        ) from exc
    return torch, nn


def build_model(model_name: str = DEFAULT_MODEL_NAME) -> Any:  # noqa: ANN401 -- torch tensor/model, lazily imported to keep this module torch-free by default
    """Construct the multi-task grader. Returns an ``nn.Module`` with a ``forward(input_ids, attention_mask) -> {'factuality', 'tone'}`` (raw logits, shape ``[B]``). Lazy so the import graph stays torch-free."""
    _torch, nn = _require_torch()
    from transformers import AutoModel

    class ModernBertMultiTaskGrader(nn.Module):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.encoder = AutoModel.from_pretrained(name)
            h = self.encoder.config.hidden_size
            self.factuality_head = nn.Linear(h, 1)
            self.tone_head = nn.Linear(h, 1)
            # Quality head: P(good article), trained on the human grade labels.
            # Replaces the sklearn TF-IDF grader once trained; shares this encoder.
            self.quality_head = nn.Linear(h, 1)
            # Relevance head: P(relevant to Algorand) from the article text.
            # Article-time relevance (distinct from the source-page enqueue gate).
            self.relevance_head = nn.Linear(h, 1)

        def _mean_pool(self, last_hidden: Any, attention_mask: Any) -> Any:  # noqa: ANN401 -- torch tensor/model, lazily imported to keep this module torch-free by default
            mask = attention_mask.unsqueeze(-1).to(last_hidden.dtype)
            summed = (last_hidden * mask).sum(dim=1)
            counts = mask.sum(dim=1).clamp(min=1e-9)
            return summed / counts

        def forward(self, input_ids: Any, attention_mask: Any) -> dict[str, Any]:  # noqa: ANN401 -- torch tensor/model, lazily imported to keep this module torch-free by default
            out = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
            pooled = self._mean_pool(out.last_hidden_state, attention_mask)
            return {
                "factuality": self.factuality_head(pooled).squeeze(-1),
                "tone": self.tone_head(pooled).squeeze(-1),
                "quality": self.quality_head(pooled).squeeze(-1),
                "relevance": self.relevance_head(pooled).squeeze(-1),
            }

    return ModernBertMultiTaskGrader(model_name)


def load_tokenizer(model_name: str = DEFAULT_MODEL_NAME) -> Any:  # noqa: ANN401 -- torch tensor/model, lazily imported to keep this module torch-free by default
    """Load the pretrained tokenizer matching the gatekeeper model."""
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(model_name)

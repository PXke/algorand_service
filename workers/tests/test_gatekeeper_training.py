"""train_gatekeeper must train from a batch carrying only soft_label_quality
(no factuality/tone) -- the path feedback_loader relies on, since there's no
gold-run/corruptor corpus yet to supply the other two heads."""

import pytest

torch = pytest.importorskip("torch")
nn = pytest.importorskip("torch.nn")

from app.modules.gatekeeper import model as gk_model  # noqa: E402
from app.modules.gatekeeper.training import TrainConfig, train_gatekeeper  # noqa: E402


class _FakeMultiTaskModel(nn.Module):
    """Tiny stand-in for ModernBertMultiTaskGrader with the same head names,
    so tests never download real ModernBERT weights."""

    def __init__(self, _name: str) -> None:
        super().__init__()
        self.factuality_head = nn.Linear(4, 1)
        self.tone_head = nn.Linear(4, 1)
        self.quality_head = nn.Linear(4, 1)
        self.relevance_head = nn.Linear(4, 1)

    def forward(self, input_ids, attention_mask):
        x = input_ids.float().mean(dim=1, keepdim=True).expand(-1, 4)
        return {
            "factuality": self.factuality_head(x).squeeze(-1),
            "tone": self.tone_head(x).squeeze(-1),
            "quality": self.quality_head(x).squeeze(-1),
            "relevance": self.relevance_head(x).squeeze(-1),
        }


def _quality_only_batch() -> dict:
    return {
        "input_ids": torch.tensor([[1, 2, 3], [4, 5, 6]]),
        "attention_mask": torch.ones(2, 3),
        "soft_label_quality": torch.tensor([1.0, 0.0]),
    }


def test_trains_from_quality_only_batch(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(gk_model, "build_model", lambda name: _FakeMultiTaskModel(name))
    out_path = tmp_path / "quality_only.pt"

    summary = train_gatekeeper(
        [_quality_only_batch()],
        TrainConfig(epochs=1, use_ipex=False, use_bf16=False, out_path=str(out_path)),
    )

    assert summary["status"] == "trained"
    assert summary["batches"] == 1
    assert out_path.exists()


def test_batch_with_no_labels_is_skipped(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(gk_model, "build_model", lambda name: _FakeMultiTaskModel(name))
    out_path = tmp_path / "no_labels.pt"
    empty_batch = {
        "input_ids": torch.tensor([[1, 2, 3]]),
        "attention_mask": torch.ones(1, 3),
    }

    summary = train_gatekeeper(
        [empty_batch],
        TrainConfig(epochs=1, use_ipex=False, use_bf16=False, out_path=str(out_path)),
    )

    assert summary["batches"] == 0
    assert summary["last_loss"] == 0.0

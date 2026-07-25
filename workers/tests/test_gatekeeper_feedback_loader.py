"""feedback_loader turns classifier_feedback rows into quality-head training examples: label = the human approve/reject decision (same ground truth the retired sklearn grader used), text = build_input("", trace, article_text)."""

from typing import Any

import pytest

from app.modules.gatekeeper import feedback_loader as fl
from tests.conftest import FakeCassandraResult as _Result


class _Idx:
    def __init__(self, feedback_id: str) -> None:
        self.feedback_id = feedback_id


class _Row:
    def __init__(self, *, url: str, approved: bool, metadata: dict | None) -> None:
        self.url = url
        self.approved = approved
        self.metadata = metadata


def _stub_session(monkeypatch: pytest.MonkeyPatch, index_rows: list[_Idx]) -> None:
    class _Session:
        def prepare(self, cql: str) -> str:  # ClassifierFeedbackStmts.* prepares on access
            return cql

        def execute(self, _stmt: str, _args: tuple) -> list[_Idx]:
            return index_rows

    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _Session())
    monkeypatch.setattr(
        "app.modules.newspaper.investigation_store.load_investigation_trace", lambda _url, **_kw: ""
    )


def test_labeled_examples_uses_approved_as_label(monkeypatch: pytest.MonkeyPatch) -> None:
    """Maps the human approved flag to a 1.0/0.0 label and includes the article text."""
    _stub_session(monkeypatch, [_Idx("a"), _Idx("b")])
    results = [
        (True, _Result(_Row(url="u1", approved=True, metadata={"article_text": "Title\nBody 1"}))),
        (True, _Result(_Row(url="u2", approved=False, metadata={"article_text": "Title\nBody 2"}))),
    ]
    monkeypatch.setattr(
        "app.core.cassandra.execute_parallel_with_args", lambda _stmt, _args: results
    )

    examples = fl._labeled_examples(10)

    assert [label for _, label in examples] == [1.0, 0.0]
    assert "Body 1" in examples[0][0]


def test_labeled_examples_skips_rows_without_article_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips feedback rows whose metadata has no article_text."""
    _stub_session(monkeypatch, [_Idx("a"), _Idx("b")])
    results = [
        (True, _Result(_Row(url="u1", approved=True, metadata={}))),
        (True, _Result(_Row(url="u2", approved=True, metadata=None))),
    ]
    monkeypatch.setattr(
        "app.core.cassandra.execute_parallel_with_args", lambda _stmt, _args: results
    )

    assert fl._labeled_examples(10) == []


def test_labeled_examples_skips_failed_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips rows whose parallel Cassandra lookup failed instead of raising."""
    _stub_session(monkeypatch, [_Idx("a")])
    monkeypatch.setattr(
        "app.core.cassandra.execute_parallel_with_args",
        lambda _stmt, _args: [(False, None)],
    )

    assert fl._labeled_examples(10) == []


def test_quality_sample_stats_counts_classes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Counts total/approved/rejected examples from the labeled example list."""
    monkeypatch.setattr(
        fl, "_labeled_examples", lambda _limit: [("t1", 1.0), ("t2", 1.0), ("t3", 0.0)]
    )

    stats = fl.quality_sample_stats(100)

    assert stats == {"total": 3, "approved": 2, "rejected": 1}


def test_iter_quality_batches_empty_when_no_examples(monkeypatch: pytest.MonkeyPatch) -> None:
    """Yields no batches when there are no labeled examples."""
    monkeypatch.setattr(fl, "_labeled_examples", lambda _limit: [])

    assert list(fl.iter_quality_batches()) == []


def test_iter_quality_batches_builds_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tokenizes labeled examples into batches of the configured size, with soft labels attached."""
    import pytest

    pytest.importorskip("torch")

    monkeypatch.setattr(
        fl,
        "_labeled_examples",
        lambda _limit: [("good article", 1.0), ("bad article", 0.0), ("ok article", 1.0)],
    )

    class _FakeTok:
        def __call__(self, texts: list[str], **_kwargs: object) -> dict[str, Any]:
            import torch

            n = len(texts)
            return {
                "input_ids": torch.zeros((n, 3), dtype=torch.long),
                "attention_mask": torch.ones((n, 3), dtype=torch.long),
            }

    monkeypatch.setattr("app.modules.gatekeeper.model.load_tokenizer", lambda: _FakeTok())

    batches = list(fl.iter_quality_batches(fl.FeedbackBatchConfig(batch_size=2)))

    assert len(batches) == 2  # 3 examples, batch_size=2 -> [2, 1]
    assert batches[0]["soft_label_quality"].tolist() == [1.0, 0.0]
    assert batches[1]["soft_label_quality"].tolist() == [1.0]

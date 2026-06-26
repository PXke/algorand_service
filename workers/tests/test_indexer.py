from __future__ import annotations

from app.modules.search.classifier.score import score_page
from app.modules.search.core.indexer import upsert_article_document


def test_classifier_accepts_algorand_page() -> None:
    result = score_page(
        url="https://algorand.foundation",
        text="Algorand blockchain and ALGO staking on TestNet.",
    )
    assert result.in_scope


def test_indexer_skips_when_typesense_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modules.search.core.indexer.is_typesense_configured",
        lambda: False,
    )
    outcome = upsert_article_document(
        article_id="a1",
        title="Title",
        summary="Summary",
        body="Body",
        service_id="svc",
        published_at_epoch=1,
    )
    assert outcome["status"] == "skipped"


def test_page_index_skips_when_classifier_rejects(monkeypatch) -> None:
    from app.modules.search.tasks import index_tasks

    monkeypatch.setattr(
        index_tasks,
        "score_page",
        lambda **_: type("R", (), {"in_scope": False, "score": 0.1})(),
    )
    outcome = index_tasks.index_crawled_page(
        url="https://example.com",
        title="Algorithm homework",
        text="Sorting algorithm for algebra class.",
        service_id="svc",
    )
    assert outcome["status"] == "skipped"
    assert outcome["reason"] == "classifier_rejected"

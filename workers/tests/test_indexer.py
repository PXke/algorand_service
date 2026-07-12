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


def test_index_article_reads_tags_from_article_detail(monkeypatch) -> None:
    from app.modules.newspaper.article_store import ArticleDetail
    from app.modules.search.tasks import index_tasks

    captured: dict = {}

    def fake_get_article(article_id: str) -> ArticleDetail:
        assert article_id == "a1"
        return ArticleDetail(
            article_id="a1",
            service_id="svc",
            title="Title",
            summary="Summary",
            body="Body",
            published_at_epoch=1,
            trigger_txid="",
            trigger_round=0,
            source_url="https://example.com",
            tags=("defi", "payments"),
        )

    def fake_upsert(**kwargs):
        captured.update(kwargs)
        return {"status": "indexed"}

    monkeypatch.setattr(index_tasks, "get_article", fake_get_article)
    monkeypatch.setattr(index_tasks, "upsert_article_document", fake_upsert)

    outcome = index_tasks.index_article(
        article_id="a1",
        title="Title",
        summary="Summary",
        body="Body",
        service_id="svc",
        published_at_epoch=1,
    )
    assert outcome["status"] == "indexed"
    assert captured["tags"] == ["defi", "payments"]


def test_classifier_anchors_chain_silent_ecosystem_domains() -> None:
    """HesabPay/Sealed: real Algorand-ecosystem services whose own sites never
    say 'Algorand' (hesab.com has zero chain mentions). Without a KNOWN_DOMAINS
    anchor they score 0 relevance, so their discovery rows drained at priority
    ~0 and every future diff would fail CONTENT_UPDATE_RELEVANCE_FLOOR (0.35)."""
    hesab = score_page(
        url="https://hesab.com/",
        text="HesabPay - Digital Wallet. Send Money, Pay Bills, Top-Up Mobile.",
    )
    assert hesab.score >= 0.35
    sealed = score_page(
        url="https://www.sealed.channel/",
        text="Sealed - Fully Anonymous Multi-Chain Messenger built on blockchain.",
    )
    assert sealed.score >= 0.35

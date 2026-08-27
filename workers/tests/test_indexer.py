"""Typesense indexing gates on classifier relevance and configuration."""

from __future__ import annotations

import pytest

from app.modules.search.classifier.score import score_page
from app.modules.search.core.indexer import upsert_article_document


def test_classifier_accepts_algorand_page() -> None:
    """A clearly Algorand-relevant page passes the classifier's in-scope check."""
    result = score_page(
        url="https://algorand.foundation",
        text="Algorand blockchain and ALGO staking on TestNet.",
    )
    assert result.in_scope


def test_indexer_skips_when_typesense_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips indexing when Typesense is not configured."""
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


def test_page_index_skips_when_classifier_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips indexing a crawled page the classifier rejects as out of scope."""
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


def test_index_article_reads_tags_from_article_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reads an article's tags off its ArticleDetail before indexing it."""
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

    def fake_upsert(**kwargs: object) -> dict:
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


def test_index_article_reads_slug_from_article_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reads an article's permanent slug off its ArticleDetail before indexing it (root-caused 2026-08-26: this field never reached Typesense at all, so every search result fell back to a raw-UUID URL)."""
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
            slug="al-goanna-launches-nft-backed-loans",
        )

    def fake_upsert(**kwargs: object) -> dict:
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
    assert captured["slug"] == "al-goanna-launches-nft-backed-loans"


def test_upsert_article_document_writes_slug_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """A newly-indexed article's Typesense document carries its `slug` field."""
    from app.modules.search.core import indexer

    captured: dict = {}

    class _FakeDocuments:
        def upsert(self, document: dict) -> None:
            captured.update(document)

    class _FakeCollection:
        documents = _FakeDocuments()

    class _FakeCollections:
        def __getitem__(self, _name: str) -> _FakeCollection:
            return _FakeCollection()

    class _FakeClient:
        collections = _FakeCollections()

    monkeypatch.setattr(indexer, "is_typesense_configured", lambda: True)
    monkeypatch.setattr(indexer, "build_typesense_client", lambda: _FakeClient())
    monkeypatch.setattr(indexer, "_ensure_collection", lambda *_a, **_k: None)

    outcome = indexer.upsert_article_document(
        article_id="a1",
        title="Title",
        summary="Summary",
        body="Body",
        service_id="svc",
        published_at_epoch=1,
        slug="al-goanna-launches-nft-backed-loans",
    )
    assert outcome["status"] == "indexed"
    assert captured["slug"] == "al-goanna-launches-nft-backed-loans"


def test_upsert_article_document_omits_slug_key_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """No `slug` kwarg (e.g. an article somehow missing one) sends no `slug` key at all, rather than an explicit null the optional schema field might reject."""
    from app.modules.search.core import indexer

    captured: dict = {}

    class _FakeDocuments:
        def upsert(self, document: dict) -> None:
            captured.update(document)

    class _FakeCollection:
        documents = _FakeDocuments()

    class _FakeCollections:
        def __getitem__(self, _name: str) -> _FakeCollection:
            return _FakeCollection()

    class _FakeClient:
        collections = _FakeCollections()

    monkeypatch.setattr(indexer, "is_typesense_configured", lambda: True)
    monkeypatch.setattr(indexer, "build_typesense_client", lambda: _FakeClient())
    monkeypatch.setattr(indexer, "_ensure_collection", lambda *_a, **_k: None)

    outcome = indexer.upsert_article_document(
        article_id="a1",
        title="Title",
        summary="Summary",
        body="Body",
        service_id="svc",
        published_at_epoch=1,
    )
    assert outcome["status"] == "indexed"
    assert "slug" not in captured


def test_upsert_article_document_computes_glossary_slugs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The document written to Typesense carries glossary_slugs extracted from the English body AND every translated body, unioned."""
    import json

    from app.modules.search.core import indexer

    captured: dict = {}

    class _FakeDocuments:
        def upsert(self, document: dict) -> None:
            captured.update(document)

    class _FakeCollection:
        documents = _FakeDocuments()

    class _FakeCollections:
        def __getitem__(self, _name: str) -> _FakeCollection:
            return _FakeCollection()

    class _FakeClient:
        collections = _FakeCollections()

    monkeypatch.setattr(indexer, "is_typesense_configured", lambda: True)
    monkeypatch.setattr(indexer, "build_typesense_client", lambda: _FakeClient())
    monkeypatch.setattr(indexer, "_ensure_collection", lambda *_a, **_k: None)

    outcome = indexer.upsert_article_document(
        article_id="a1",
        title="Title",
        summary="Summary",
        body='See [ARC-27](/glossary/arc-27 "A wallet interop standard") for details.',
        service_id="svc",
        published_at_epoch=1,
        translations={
            "fr": json.dumps(
                {
                    "title": "Titre",
                    "summary": "Résumé",
                    "body": 'Voir [ARC-27](/glossary/arc-27 "Norme") et [jalonnement](/glossary/staking).',
                }
            )
        },
    )
    assert outcome["status"] == "indexed"
    assert captured["glossary_slugs"] == ["arc-27", "staking"]


def test_upsert_article_translation_merges_glossary_slugs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A later-landing translation's new glossary slugs are UNIONED into the existing document, not overwriting the ones already found in English (Typesense .update() replaces string[] fields wholesale)."""
    from app.modules.search.core import indexer

    updated: dict = {}

    class _FakeDocument:
        def retrieve(self) -> dict:
            return {"glossary_slugs": ["arc-27"]}

        def update(self, fields: dict) -> None:
            updated.update(fields)

    class _FakeDocumentsIndex:
        def __getitem__(self, _article_id: str) -> _FakeDocument:
            return _FakeDocument()

    class _FakeCollection:
        documents = _FakeDocumentsIndex()

    class _FakeCollections:
        def __getitem__(self, _name: str) -> _FakeCollection:
            return _FakeCollection()

    class _FakeClient:
        collections = _FakeCollections()

    monkeypatch.setattr(indexer, "is_typesense_configured", lambda: True)
    monkeypatch.setattr(indexer, "build_typesense_client", lambda: _FakeClient())
    monkeypatch.setattr(indexer, "_ensure_collection", lambda *_a, **_k: None)

    outcome = indexer.upsert_article_translation(
        article_id="a1",
        lang="fr",
        title="Titre",
        summary="Résumé",
        body="Voir [jalonnement](/glossary/staking).",
    )
    assert outcome["status"] == "indexed"
    assert updated["glossary_slugs"] == ["arc-27", "staking"]


def test_classifier_anchors_chain_silent_ecosystem_domains() -> None:
    """HesabPay/Sealed: real Algorand-ecosystem services whose own sites never say 'Algorand' (hesab.com has zero chain mentions). Without a KNOWN_DOMAINS anchor they score 0 relevance, so their discovery rows drained at priority ~0 and every future diff would fail CONTENT_UPDATE_RELEVANCE_FLOOR (0.35)."""
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

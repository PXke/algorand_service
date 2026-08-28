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


def test_page_index_skips_soft_404s(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client-router 'not found' fallback that classifies in-scope (it's real, substantial-looking text) is still skipped before storage -- it's never worth a Cassandra row or a Typesense document."""
    from app.modules.search.tasks import index_tasks

    monkeypatch.setattr(
        index_tasks,
        "score_page",
        lambda **_: type("R", (), {"in_scope": True, "score": 1.0})(),
    )
    outcome = index_tasks.index_crawled_page(
        url="https://lumirogue.com/gungi",
        title="Lumi Rogue",
        text='404 Page Not Found The page "gungi" could not be found in this application. Go Home',
        service_id="lumirogue-com",
    )
    assert outcome == {"status": "skipped", "reason": "soft_404"}


def test_page_index_checks_interactive_crawl_trigger_on_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A domain-duplicate hit is exactly the signal needs_interactive_crawl exists to catch -- index_crawled_page must offer that domain up for the check right when it catches one, not just at read time."""
    from app.modules.search.tasks import index_tasks

    monkeypatch.setattr(
        index_tasks,
        "score_page",
        lambda **_: type("R", (), {"in_scope": True, "score": 1.0})(),
    )
    monkeypatch.setattr(index_tasks, "domain_has_similar_content", lambda *_a, **_k: True)
    calls: list[tuple] = []
    monkeypatch.setattr(
        "app.modules.crawler.interactive_crawl.maybe_trigger_interactive_crawl",
        lambda url, **kw: calls.append((url, kw)) or False,
    )
    index_tasks.index_crawled_page(
        url="https://lumirogue.com/?view=gungi",
        title="Lumi Rogue",
        text="LUMI ROGUE v0.21 Try the demo (tutorial)",
        service_id="lumirogue-com",
    )
    assert calls == [("https://lumirogue.com/?view=gungi", {"service_id": "lumirogue-com"})]


def test_page_index_skips_domain_duplicate_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """Root-caused 2026-08-28 (Lumi Rogue): a client-rendered SPA served the SAME shell HTML for ~20 crawler-guessed URL variants. Content that byte-matches a page already crawled for the domain is skipped before storage, not just filtered later at aggregate-read time."""
    from app.modules.search.tasks import index_tasks

    monkeypatch.setattr(
        index_tasks,
        "score_page",
        lambda **_: type("R", (), {"in_scope": True, "score": 1.0})(),
    )
    monkeypatch.setattr(index_tasks, "domain_has_similar_content", lambda *_a, **_k: True)
    outcome = index_tasks.index_crawled_page(
        url="https://lumirogue.com/?view=gungi",
        title="Lumi Rogue",
        text="LUMI ROGUE v0.21 Try the demo (tutorial) Rankings Need an Ankh?",
        service_id="lumirogue-com",
    )
    assert outcome == {"status": "skipped", "reason": "duplicate_content"}


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


def _wire_index_crawled_page_storage(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Fake index_crawled_page's storage/indexing seams so a run never touches the network.

    Returns the dict that upsert_page_document's kwargs get captured into.
    """
    from app.modules.crawler.crawled_page_store import CrawledPageRecord
    from app.modules.search.tasks import index_tasks

    def fake_upsert_crawled_page(**kwargs: object) -> CrawledPageRecord:
        return CrawledPageRecord(
            page_id="11111111-1111-1111-1111-111111111111",
            url=str(kwargs["url"]),
            domain="svc.example",
            title=str(kwargs["title"]),
            description="desc",
            body=str(kwargs["body"]),
            service_id=str(kwargs["service_id"]),
            source=str(kwargs["source"]),
            keywords=(),
            classifier_score=float(kwargs["classifier_score"]),
            crawled_at_epoch=0,
        )

    captured: dict = {}

    def fake_upsert_page_document(**kwargs: object) -> dict:
        captured.update(kwargs)
        return {"status": "indexed"}

    monkeypatch.setattr(index_tasks, "upsert_crawled_page", fake_upsert_crawled_page)
    monkeypatch.setattr(index_tasks, "upsert_page_document", fake_upsert_page_document)
    return captured


def test_index_crawled_page_forwards_outbound_links_to_score_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """outbound_links passed into index_crawled_page reaches score_page.

    Same explorer-link signal every other score_page caller (url_queue_tasks.py)
    already gets — previously silently dropped.
    """
    from app.modules.search.tasks import index_tasks

    _wire_index_crawled_page_storage(monkeypatch)
    seen_links: list[tuple[str, ...]] = []

    def fake_score_page(*, url: str, text: str, outbound_links: tuple[str, ...] = ()) -> object:  # noqa: ARG001
        seen_links.append(outbound_links)
        return type("R", (), {"in_scope": True, "score": 1.0})()

    monkeypatch.setattr(index_tasks, "score_page", fake_score_page)

    index_tasks.index_crawled_page(
        url="https://svc.example/page",
        title="Title",
        text="algorand ecosystem partner",
        service_id="svc",
        outbound_links=("https://allo.info/asset/1/token",),
    )
    assert seen_links == [("https://allo.info/asset/1/token",)]


def test_index_crawled_page_converts_published_at_iso_to_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page's own ISO-8601 published_at drives the indexed epoch.

    Applies when no explicit published_at_epoch is given, instead of always
    falling back to indexing time.
    """
    from app.modules.search.tasks import index_tasks

    captured = _wire_index_crawled_page_storage(monkeypatch)
    monkeypatch.setattr(
        index_tasks, "score_page", lambda **_: type("R", (), {"in_scope": True, "score": 1.0})()
    )

    index_tasks.index_crawled_page(
        url="https://svc.example/page",
        title="Title",
        text="algorand ecosystem partner",
        service_id="svc",
        published_at="2026-08-21T12:30:00Z",
    )
    assert captured["published_at_epoch"] == 1787315400


def test_index_crawled_page_explicit_epoch_wins_over_published_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit published_at_epoch still wins over the raw published_at string.

    e.g. a recompose re-stamping the same page.
    """
    from app.modules.search.tasks import index_tasks

    captured = _wire_index_crawled_page_storage(monkeypatch)
    monkeypatch.setattr(
        index_tasks, "score_page", lambda **_: type("R", (), {"in_scope": True, "score": 1.0})()
    )

    index_tasks.index_crawled_page(
        url="https://svc.example/page",
        title="Title",
        text="algorand ecosystem partner",
        service_id="svc",
        published_at="2026-08-21T12:30:00Z",
        published_at_epoch=42,
    )
    assert captured["published_at_epoch"] == 42


def test_index_crawled_page_falls_back_to_now_when_published_at_unparseable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty/garbage published_at falls back to indexing time.

    Matches behavior from before this fix (no page metadata found), rather
    than erroring.
    """
    from app.modules.search.tasks import index_tasks

    captured = _wire_index_crawled_page_storage(monkeypatch)
    monkeypatch.setattr(
        index_tasks, "score_page", lambda **_: type("R", (), {"in_scope": True, "score": 1.0})()
    )
    monkeypatch.setattr(index_tasks.time, "time", lambda: 1234567.0)

    index_tasks.index_crawled_page(
        url="https://svc.example/page",
        title="Title",
        text="algorand ecosystem partner",
        service_id="svc",
        published_at="not-a-date",
    )
    assert captured["published_at_epoch"] == 1234567


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


def test_upsert_article_document_omits_slug_key_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

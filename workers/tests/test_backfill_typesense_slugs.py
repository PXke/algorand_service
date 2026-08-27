"""workers/scratch/backfill_typesense_slugs.py: dry-run makes no writes, a real run upserts each article's real Cassandra slug into Typesense."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scratch" / "backfill_typesense_slugs.py"


def _load_script() -> ModuleType:
    """Import the one-off script as a module (it lives outside the `app` package, so it's not on pythonpath)."""
    spec = importlib.util.spec_from_file_location("backfill_typesense_slugs", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """A freshly-loaded copy of the backfill script, with list_feed_articles/get_article stubbed to two fake published articles (one of them the "Goana" article from the live incident)."""
    from app.modules.newspaper.article_store import ArticleDetail, FeedArticleRow

    mod = _load_script()

    rows = [
        FeedArticleRow(
            article_id="a1",
            service_id="svc",
            title="Al-Goanna launches NFT-backed loans",
            summary="s",
            published_at_epoch=1,
        ),
        FeedArticleRow(
            article_id="a2",
            service_id="svc",
            title="Another article",
            summary="s",
            published_at_epoch=2,
        ),
    ]
    details = {
        "a1": ArticleDetail(
            article_id="a1",
            service_id="svc",
            title="Al-Goanna launches NFT-backed loans",
            summary="s",
            body="b",
            published_at_epoch=1,
            trigger_txid="",
            trigger_round=0,
            source_url="https://example.com",
            slug="al-goanna-launches-nft-backed-loans-and-40-000-algo-staking-battles",
        ),
        "a2": ArticleDetail(
            article_id="a2",
            service_id="svc",
            title="Another article",
            summary="s",
            body="b",
            published_at_epoch=2,
            trigger_txid="",
            trigger_round=0,
            source_url="https://example.com",
            slug="another-article",
        ),
    }

    monkeypatch.setattr(mod, "list_feed_articles", lambda limit: rows)  # noqa: ARG005
    monkeypatch.setattr(mod, "get_article", lambda article_id: details[article_id])
    return mod


def test_dry_run_makes_no_typesense_writes(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--dry-run reports the real Cassandra slug for each article without calling upsert_article_document."""
    calls: list[dict] = []
    monkeypatch.setattr(script, "upsert_article_document", lambda **kw: calls.append(kw))
    monkeypatch.setattr(sys, "argv", ["backfill_typesense_slugs.py", "--dry-run"])

    script.main()

    assert calls == []
    out = capsys.readouterr().out
    assert "al-goanna-launches-nft-backed-loans-and-40-000-algo-staking-battles" in out
    assert "DRY_RUN_DONE" in out


def test_real_run_upserts_each_articles_real_slug(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real (non-dry-run) invocation upserts every scanned article with its actual Cassandra slug, and reports a summary."""
    calls: list[dict] = []

    def fake_upsert(**kw: object) -> dict[str, str]:
        calls.append(kw)
        return {"status": "indexed"}

    monkeypatch.setattr(script, "upsert_article_document", fake_upsert)
    monkeypatch.setattr(sys, "argv", ["backfill_typesense_slugs.py"])

    script.main()

    assert {c["article_id"]: c["slug"] for c in calls} == {
        "a1": "al-goanna-launches-nft-backed-loans-and-40-000-algo-staking-battles",
        "a2": "another-article",
    }
    out = capsys.readouterr().out
    assert "indexed=2 skipped=0 errors=0" in out
    assert "BACKFILL_DONE" in out


def test_dry_run_flags_an_article_with_no_slug_in_cassandra(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An article somehow missing a slug even in Cassandra (violates the platform's own invariant) is called out as an ANOMALY, not silently skipped."""
    from app.modules.newspaper.article_store import ArticleDetail, FeedArticleRow

    mod = _load_script()
    row = FeedArticleRow(
        article_id="a3", service_id="svc", title="No slug", summary="s", published_at_epoch=1
    )
    detail = ArticleDetail(
        article_id="a3",
        service_id="svc",
        title="No slug",
        summary="s",
        body="b",
        published_at_epoch=1,
        trigger_txid="",
        trigger_round=0,
        source_url="https://example.com",
        slug=None,
    )
    monkeypatch.setattr(mod, "list_feed_articles", lambda limit: [row])  # noqa: ARG005
    monkeypatch.setattr(mod, "get_article", lambda article_id: detail)  # noqa: ARG005
    monkeypatch.setattr(sys, "argv", ["backfill_typesense_slugs.py", "--dry-run"])

    mod.main()

    out = capsys.readouterr().out
    assert "ANOMALY a3" in out
    assert "1 have no slug in Cassandra at all" in out

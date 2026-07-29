"""Slug assignment at publish.

The failure mode this guards is silent: with no slug an article still resolves
by uuid, so nothing looks broken — the slug migration would simply stop
applying to new stories and nobody would notice for weeks.
"""

from __future__ import annotations

from pathlib import Path

from algorand_shared.slugs import slugify, unique_slug


def test_publish_path_claims_a_slug() -> None:
    """Both go-live writes claim a slug; held drafts deliberately do not."""
    from app.modules.newspaper import article_store
    from app.modules.newspaper.tasks import queue_drain_tasks

    store = Path(article_store.__file__).read_text(encoding="utf-8")
    drain = Path(queue_drain_tasks.__file__).read_text(encoding="utf-8")

    # insert_stored_article claims only inside the publish_to_feed branch.
    claim = "_claim_slug_for_feed(article_id, title, published_at)"
    assert claim in store
    # The claim must come AFTER the publish_to_feed guard, so a held draft
    # never takes a slug it may never use.
    assert store.index("if publish_to_feed:") < store.index(claim)

    # The queue drain is how most articles actually go live.
    assert "_claim_slug_for_feed(art.article_id, art.title, released_at)" in drain


def test_slug_claim_never_raises(monkeypatch) -> None:  # noqa: ANN001
    """A slug failure must not fail a publish — the uuid URL still resolves."""
    from app.modules.newspaper import article_store

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("cassandra down")

    monkeypatch.setattr(article_store, "ensure_article_slug", boom)
    from datetime import UTC, datetime
    from uuid import uuid4

    # Must swallow the error rather than propagate.
    article_store._claim_slug_for_feed(uuid4(), "Some title", datetime.now(tz=UTC))


def test_suffix_only_applies_on_a_real_collision() -> None:
    """An article with a unique title keeps the bare slug."""
    taken: set[str] = set()
    first = unique_slug("A Unique Headline", fallback="id1", is_taken=lambda s: s in taken)
    assert first == slugify("A Unique Headline")
    assert not first.endswith("-2")

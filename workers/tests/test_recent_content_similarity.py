"""Content-level novelty: retrieve recently-published articles closest to a
candidate from the Typesense articles index, then score token overlap against the
matched article's title+summary. Catches same-topic/different-headline dupes."""

from app.modules.newspaper import article_grader


class _FakeDocs:
    def __init__(self, hits):
        self._hits = hits

    def search(self, params):
        return {"hits": self._hits}


class _FakeCollection:
    def __init__(self, hits):
        self.documents = _FakeDocs(hits)


class _FakeClient:
    def __init__(self, hits):
        self.collections = {"articles": _FakeCollection(hits)}


def _patch(monkeypatch, client):
    monkeypatch.setattr(
        "app.modules.search.core.typesense_config.build_typesense_client",
        lambda: client,
    )


def test_paraphrased_headline_about_same_story_scores_high(monkeypatch):
    # Different headline tokens, same story — the body retrieval still finds the
    # recent article and the title+summary overlap is high.
    hits = [
        {
            "document": {
                "title": "Pera Wallet rolls out in-app staking",
                "summary": "Pera Wallet users can now stake directly in the wallet.",
            }
        }
    ]
    _patch(monkeypatch, _FakeClient(hits))
    sim, match = article_grader.recent_content_similarity(
        "Explore staking with Pera Wallet", "Pera Wallet staking is now live"
    )
    assert sim > 0.3
    assert "Pera" in match


def test_unrelated_candidate_scores_low(monkeypatch):
    hits = [
        {
            "document": {
                "title": "Folks Finance launches new lending market",
                "summary": "A new borrowing market opened on Folks Finance.",
            }
        }
    ]
    _patch(monkeypatch, _FakeClient(hits))
    sim, _ = article_grader.recent_content_similarity(
        "Tinyman releases governance proposal", "Tinyman DAO votes on fees"
    )
    assert sim < 0.2


def test_age_decay_eases_penalty_for_old_near_duplicates(monkeypatch):
    # The SAME near-duplicate scores high when published recently but is heavily
    # discounted once the match is older than the decay horizon — so a story can
    # be re-covered after enough time has passed.
    import time

    from app.core import config

    now = int(time.time())
    monkeypatch.setattr(config, "NOVELTY_DECAY_FULL_DAYS", 7, raising=False)
    monkeypatch.setattr(config, "NOVELTY_DECAY_ZERO_DAYS", 70, raising=False)

    def _hit(age_days):
        return [
            {
                "document": {
                    "title": "Pera Wallet rolls out in-app staking",
                    "summary": "Pera Wallet users can now stake directly in the wallet.",
                    "published_at": now - age_days * 86400,
                }
            }
        ]

    _patch(monkeypatch, _FakeClient(_hit(1)))
    fresh, _ = article_grader.recent_content_similarity(
        "Explore staking with Pera Wallet", "Pera Wallet staking is now live"
    )
    _patch(monkeypatch, _FakeClient(_hit(80)))
    stale, _ = article_grader.recent_content_similarity(
        "Explore staking with Pera Wallet", "Pera Wallet staking is now live"
    )
    assert fresh > 0.3
    assert stale == 0.0  # past the zero-day horizon → no penalty (fully novel)
    assert fresh > stale


def test_fails_open_without_typesense(monkeypatch):
    _patch(monkeypatch, None)
    assert article_grader.recent_content_similarity("anything", "body") == (0.0, "")


def test_disabled_when_window_zero(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.NOVELTY_CONTENT_WINDOW_HOURS", 0, raising=False
    )
    # Must not even build a client when disabled.
    _patch(monkeypatch, _FakeClient([{"document": {"title": "x", "summary": "x"}}]))
    assert article_grader.recent_content_similarity("x", "x") == (0.0, "")

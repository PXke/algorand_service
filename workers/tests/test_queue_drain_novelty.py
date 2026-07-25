"""_novelty_collapsed must use the SAME boundary as the compose-time duplicate check (NOVELTY_MAX_SIMILARITY), not the old, more lenient NOVELTY_DUPLICATE_FLOOR — a row in between the two used to survive the drain-time cut only to be discarded as a duplicate mid-compose, wasting a full Mistral call."""

from types import SimpleNamespace

import pytest

from app.core import config
from app.modules.newspaper import article_grader
from app.modules.newspaper.tasks.queue_drain_tasks import _novelty_collapsed


def _row(title: str = "Update", text: str = "Algorand update") -> SimpleNamespace:
    return SimpleNamespace(payload={"page_title": title, "page_text": text})


def _patch_similarity(monkeypatch: pytest.MonkeyPatch, sim: float) -> None:
    monkeypatch.setattr(article_grader, "recent_title_similarity", lambda *_a, **_kw: (sim, ""))
    monkeypatch.setattr(article_grader, "recent_content_similarity", lambda *_a, **_kw: (sim, ""))


def test_uses_novelty_max_similarity_not_old_duplicate_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collapses a row whose similarity clears NOVELTY_MAX_SIMILARITY even though it's below the old, laxer duplicate floor."""
    monkeypatch.setattr(config, "NOVELTY_MAX_SIMILARITY", 0.6, raising=False)
    # similarity 0.7 -> novelty 0.3: below the old NOVELTY_DUPLICATE_FLOOR
    # (0.1) would NOT have collapsed, but must collapse now that the boundary
    # matches the compose-time duplicate check.
    _patch_similarity(monkeypatch, 0.7)
    assert _novelty_collapsed(_row()) is True


def test_low_similarity_does_not_collapse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Does not collapse a row whose similarity is well below the novelty boundary."""
    monkeypatch.setattr(config, "NOVELTY_MAX_SIMILARITY", 0.6, raising=False)
    _patch_similarity(monkeypatch, 0.2)
    assert _novelty_collapsed(_row()) is False


def test_boundary_is_inclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collapses a row whose similarity exactly equals NOVELTY_MAX_SIMILARITY."""
    monkeypatch.setattr(config, "NOVELTY_MAX_SIMILARITY", 0.6, raising=False)
    _patch_similarity(monkeypatch, 0.6)
    assert _novelty_collapsed(_row()) is True
